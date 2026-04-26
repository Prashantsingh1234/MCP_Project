"""Async MCP client wrapper for all five servers with retry and guard hooks."""

from __future__ import annotations

import asyncio
import random
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any, Optional

from src.agents.discharge_agent import AsyncMCPToolClient
from src.chatbot.metrics import RequestMetrics
from src.chatbot.rbac_guard import ActorContext, RBACGuard
from src.utils.exceptions import MCPConnectionError, ToolExecutionError
from src.utils.telemetry import get_telemetry


@dataclass(frozen=True)
class MCPServerURLs:
    ehr: str = "http://localhost:8001/sse"
    pharmacy: str = "http://localhost:8002/sse"
    billing: str = "http://localhost:8003/sse"
    security: str = "http://localhost:8004/sse"
    telemetry: str = "http://localhost:8005/sse"


class MCPClient:
    def __init__(self, urls: MCPServerURLs, actor: ActorContext, metrics: RequestMetrics, max_retries: int = 3):
        self.urls = urls
        self.actor = actor
        self.metrics = metrics
        self.max_retries = max_retries
        self.rbac = RBACGuard()

        self._ehr: Optional[AsyncMCPToolClient] = None
        self._pharmacy: Optional[AsyncMCPToolClient] = None
        self._billing: Optional[AsyncMCPToolClient] = None
        self._security: Optional[AsyncMCPToolClient] = None
        self._telemetry: Optional[AsyncMCPToolClient] = None
        self._stack: Optional[AsyncExitStack] = None
        self._connect_lock = asyncio.Lock()
        # The MCP Python SDK SSE client is not concurrency-safe for parallel tool calls
        # over a single session/transport. Serialize per-server tool calls.
        self._ehr_call_lock = asyncio.Lock()
        self._pharmacy_call_lock = asyncio.Lock()
        self._billing_call_lock = asyncio.Lock()
        self._security_call_lock = asyncio.Lock()
        self._telemetry_call_lock = asyncio.Lock()

    async def __aenter__(self):
        await self._ensure_connected()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.aclose(exc_type=exc_type, exc=exc, tb=tb)

    async def _ensure_connected(self) -> None:
        """Lazy-connect on first tool call.

        Security and Telemetry servers are connected on demand — if they are
        not running (optional servers) the failure is caught per call, not here.
        """
        if self._stack is not None:
            return
        async with self._connect_lock:
            if self._stack is not None:
                return
            stack = AsyncExitStack()
            self._stack = stack
            self._ehr = await stack.enter_async_context(AsyncMCPToolClient(self.urls.ehr))
            self._pharmacy = await stack.enter_async_context(AsyncMCPToolClient(self.urls.pharmacy))
            self._billing = await stack.enter_async_context(AsyncMCPToolClient(self.urls.billing))
            # Security and Telemetry are optional: connect best-effort.
            try:
                self._security = await stack.enter_async_context(AsyncMCPToolClient(self.urls.security))
            except Exception:
                self._security = None
            try:
                self._telemetry = await stack.enter_async_context(AsyncMCPToolClient(self.urls.telemetry))
            except Exception:
                self._telemetry = None

    async def aclose(self, *, exc_type=None, exc=None, tb=None) -> None:
        if self._stack is None:
            return
        await self._stack.__aexit__(exc_type, exc, tb)
        self._stack = None
        self._ehr = None
        self._pharmacy = None
        self._billing = None
        self._security = None
        self._telemetry = None

    async def _retry(self, fn, tool: str, server: str, *, patient_id: Optional[str]):
        base_delay = 0.2
        last_exc: Optional[Exception] = None
        retry_lines: list[str] = []
        for attempt in range(1, self.max_retries + 1):
            self.metrics.mcp_call_count += 1
            try:
                self.metrics.mcp_call_count_by_server[server] = (
                    self.metrics.mcp_call_count_by_server.get(server, 0) + 1
                )
            except Exception:
                pass
            t0 = time.perf_counter()
            try:
                result = await fn()
            except Exception as exc:
                last_exc = exc
                try:
                    dur_ms = (time.perf_counter() - t0) * 1000
                    err = str(exc)
                    if len(err) > 500:
                        err = err[:500] + "…"
                    get_telemetry().record_call(
                        server=server, tool=tool, role=self.actor.role,
                        patient_id=patient_id, duration_ms=dur_ms,
                        success=False, error=err,
                    )
                except Exception:
                    pass
                if attempt >= self.max_retries:
                    break
                retry_lines.append(f"Retrying… (Attempt {attempt + 1}/{self.max_retries})")
                await asyncio.sleep(base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.1))
                continue

            try:
                dur_ms = (time.perf_counter() - t0) * 1000
                get_telemetry().record_call(
                    server=server, tool=tool, role=self.actor.role,
                    patient_id=patient_id, duration_ms=dur_ms,
                    success=True, error=None,
                )
            except Exception:
                pass
            return result

        reason = str(last_exc)
        if retry_lines:
            reason = (
                f"{server} service unavailable.\n\n"
                + "\n".join(retry_lines)
                + f"\n\nLast error: {last_exc}"
            )
        raise ToolExecutionError(tool=tool, server=server, reason=reason)

    # ── Per-server call helpers ───────────────────────────────────────────────

    async def ehr_call(self, tool: str, args: dict[str, Any], patient_id: Optional[str] = None) -> Any:
        self.rbac.ensure_allowed(self.actor, "ehr", tool, patient_id)
        await self._ensure_connected()
        if not self._ehr:
            raise MCPConnectionError("ehr", "not connected")
        async with self._ehr_call_lock:
            return await self._retry(
                lambda: self._ehr.call_tool(tool, {**args, "role": self.actor.role}),
                tool, "ehr", patient_id=patient_id,
            )

    async def pharmacy_call(self, tool: str, args: dict[str, Any], patient_id: Optional[str] = None) -> Any:
        self.rbac.ensure_allowed(self.actor, "pharmacy", tool, patient_id)
        await self._ensure_connected()
        if not self._pharmacy:
            raise MCPConnectionError("pharmacy", "not connected")
        async with self._pharmacy_call_lock:
            return await self._retry(
                lambda: self._pharmacy.call_tool(tool, {**args, "role": self.actor.role}),
                tool, "pharmacy", patient_id=patient_id,
            )

    async def billing_call(self, tool: str, args: dict[str, Any], patient_id: Optional[str] = None) -> Any:
        self.rbac.ensure_allowed(self.actor, "billing", tool, patient_id)
        await self._ensure_connected()
        if not self._billing:
            raise MCPConnectionError("billing", "not connected")
        async with self._billing_call_lock:
            return await self._retry(
                lambda: self._billing.call_tool(tool, {**args, "role": self.actor.role}),
                tool, "billing", patient_id=patient_id,
            )

    async def security_call(self, tool: str, args: dict[str, Any], patient_id: Optional[str] = None) -> Any:
        """Call a tool on the Security (RBAC) MCP server.

        Falls back to direct Python call if the security server is not running,
        so the core discharge workflow is not blocked by an optional server.
        """
        self.rbac.ensure_allowed(self.actor, "security", tool, patient_id)
        await self._ensure_connected()
        if not self._security:
            # Fallback: call SecurityServer directly (in-process)
            from src.servers.security_server import get_security_server
            sec = get_security_server()
            fn = getattr(sec, tool, None)
            if fn is None:
                raise ToolExecutionError(tool=tool, server="security", reason="Security server not running and no in-process fallback")
            return fn(**args)
        async with self._security_call_lock:
            return await self._retry(
                lambda: self._security.call_tool(tool, args),
                tool, "security", patient_id=patient_id,
            )

    async def telemetry_call(self, tool: str, args: dict[str, Any], patient_id: Optional[str] = None) -> Any:
        """Call a tool on the Telemetry/Observability MCP server.

        Falls back to direct Python call if the telemetry server is not running.
        """
        self.rbac.ensure_allowed(self.actor, "telemetry", tool, patient_id)
        await self._ensure_connected()
        if not self._telemetry:
            from src.servers.telemetry_server import get_telemetry_server
            tel = get_telemetry_server()
            fn = getattr(tel, tool, None)
            if fn is None:
                raise ToolExecutionError(tool=tool, server="telemetry", reason="Telemetry server not running and no in-process fallback")
            return fn(**args)
        async with self._telemetry_call_lock:
            return await self._retry(
                lambda: self._telemetry.call_tool(tool, args),
                tool, "telemetry", patient_id=patient_id,
            )
