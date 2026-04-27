"""Telemetry / Observability MCP Server for MCPDischarge.

Exposes runtime metrics, system health, and workflow traces as MCP tools so
the LLM and agents can query observability data without direct Python access.
"""

import logging
import socket
from typing import Any, Optional
from datetime import datetime

from src.utils.telemetry import get_telemetry

logger = logging.getLogger(__name__)

MCP_PORTS = {"ehr": 8001, "pharmacy": 8002, "billing": 8003,
             "security": 8004, "telemetry": 8005}


class TelemetryServer:
    """Telemetry server: call metrics, alerts, system health, workflow traces."""

    def __init__(self):
        self.telemetry = get_telemetry()
        logger.info("Telemetry Server initialized")

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _tcp_ok(port: int, timeout: float = 0.4) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=timeout):
                return True
        except OSError:
            return False

    # ── Public tools ──────────────────────────────────────────────────────────

    def get_mcp_call_count(self, patient_id: Optional[str] = None) -> dict[str, Any]:
        """Return the number of MCP tool calls recorded, optionally filtered by patient.

        Args:
            patient_id: If provided, count only calls for this patient.

        Returns:
            Dict with total calls, per-server breakdown, and success/failure counts.
        """
        calls = self.telemetry.get_calls(limit=0)  # all calls
        if patient_id:
            calls = [c for c in calls if getattr(c, "patient_id", None) == patient_id]

        by_server: dict[str, int] = {}
        success = 0
        failure = 0
        for c in calls:
            srv = getattr(c, "server", "unknown")
            by_server[srv] = by_server.get(srv, 0) + 1
            if getattr(c, "success", True):
                success += 1
            else:
                failure += 1

        return {
            "total_calls": len(calls),
            "success_count": success,
            "failure_count": failure,
            "by_server": by_server,
            "patient_id_filter": patient_id,
            "queried_at": datetime.now().isoformat(),
        }

    def get_alerts(self, patient_id: Optional[str] = None,
                   level: Optional[str] = None) -> dict[str, Any]:
        """Return system alerts, optionally filtered by patient or severity level.

        Args:
            patient_id: Filter alerts that mention this patient_id.
            level: Filter by severity (INFO, WARNING, ERROR, CRITICAL, URGENT).

        Returns:
            Dict with matching alerts and counts by level.
        """
        alerts = self.telemetry.get_alerts(level=level)

        if patient_id:
            alerts = [
                a for a in alerts
                if patient_id in (getattr(a, "message", "") or "")
                or patient_id in str(getattr(a, "details", "") or "")
            ]

        counts: dict[str, int] = {}
        for a in alerts:
            lvl = getattr(a, "level", "INFO")
            counts[lvl] = counts.get(lvl, 0) + 1

        return {
            "total": len(alerts),
            "counts_by_level": counts,
            "alerts": [a.__dict__ for a in alerts[-100:]],
            "patient_id_filter": patient_id,
            "level_filter": level,
        }

    def get_system_health(self) -> dict[str, Any]:
        """Return system health: MCP server availability, call summary, uptime.

        Returns:
            Dict with per-server status and aggregate metrics.
        """
        servers = {name: self._tcp_ok(port) for name, port in MCP_PORTS.items()}
        summary = self.telemetry.get_summary()
        return {
            "servers": {
                name: {"port": port, "reachable": servers[name]}
                for name, port in MCP_PORTS.items()
            },
            "all_healthy": all(servers.values()),
            "uptime_seconds": summary.get("uptime_seconds", 0),
            "total_calls": summary.get("total_calls", 0),
            "total_alerts": summary.get("total_alerts", 0),
            "total_rbac_violations": summary.get("total_rbac_violations", 0),
            "checked_at": datetime.now().isoformat(),
        }

    def get_summary(self) -> dict[str, Any]:
        """Return the raw telemetry summary (counts, averages, breakdowns)."""
        return self.telemetry.get_summary()

    def record_chat_trace(
        self,
        *,
        conversation_id: Optional[str],
        role: Optional[str],
        patient_id: Optional[str],
        latency_ms: float,
        success: bool,
        mcp_calls: int = 0,
        rbac_violations: int = 0,
        needs_clarification: bool = False,
        clarification_type: Optional[str] = None,
        error: Optional[str] = None,
    ) -> dict[str, Any]:
        """Record a PHI-safe chat trace for the Logs UI (no raw user text)."""
        self.telemetry.record_chat_trace(
            conversation_id=conversation_id,
            role=role,
            patient_id=patient_id,
            latency_ms=float(latency_ms or 0),
            success=bool(success),
            mcp_calls=int(mcp_calls or 0),
            rbac_violations=int(rbac_violations or 0),
            needs_clarification=bool(needs_clarification),
            clarification_type=str(clarification_type) if clarification_type else None,
            error=str(error) if error else None,
        )
        return {"ok": True}

    def get_chat_traces(self, limit: int = 100) -> dict[str, Any]:
        """Return recent PHI-safe chat traces for UI display."""
        limit = int(limit or 100)
        if limit < 1:
            limit = 1
        if limit > 500:
            limit = 500
        rows = [c.__dict__ for c in self.telemetry.get_chat_traces(limit=limit)]
        return {"chat": rows, "limit": limit}

    def get_recent_calls(self, limit: int = 100) -> dict[str, Any]:
        """Return recent tool calls, RBAC violations, and alerts for UI display."""
        limit = int(limit or 100)
        if limit < 1:
            limit = 1
        if limit > 500:
            limit = 500
        telem = self.telemetry
        summary = telem.get_summary()
        calls = [c.__dict__ for c in telem.get_calls(limit=limit)]
        chat = [c.__dict__ for c in telem.get_chat_traces(limit=limit)]
        return {
            "summary": summary,
            "chat": chat,
            "calls": calls,
            "rbac_violations": telem.get_rbac_violations()[-limit:],
            "alerts": [a.__dict__ for a in telem.get_alerts()][-limit:],
        }

    def trace_workflow(self, patient_id: str) -> dict[str, Any]:
        """Return a full execution trace for a patient's workflow.

        This reconstructs the ordered sequence of MCP tool calls made for the
        given patient from the telemetry store, grouped by server.

        Args:
            patient_id: Patient ID to trace (e.g. PAT-001).

        Returns:
            Dict with ordered call trace and per-server summaries.
        """
        calls = [
            c for c in self.telemetry.get_calls(limit=0)
            if getattr(c, "patient_id", None) == patient_id
        ]
        calls_sorted = sorted(calls, key=lambda c: getattr(c, "timestamp", ""))

        by_server: dict[str, list[dict]] = {}
        for c in calls_sorted:
            srv = getattr(c, "server", "unknown")
            if srv not in by_server:
                by_server[srv] = []
            by_server[srv].append({
                "tool": getattr(c, "tool", ""),
                "timestamp": getattr(c, "timestamp", ""),
                "duration_ms": getattr(c, "duration_ms", 0),
                "success": getattr(c, "success", True),
                "error": getattr(c, "error", None),
            })

        alerts = [
            a.__dict__ for a in self.telemetry.get_alerts()
            if patient_id in (getattr(a, "message", "") or "")
        ]

        return {
            "patient_id": patient_id,
            "total_calls": len(calls_sorted),
            "ordered_trace": [
                {
                    "step": i + 1,
                    "server": getattr(c, "server", ""),
                    "tool": getattr(c, "tool", ""),
                    "role": getattr(c, "role", ""),
                    "timestamp": getattr(c, "timestamp", ""),
                    "duration_ms": round(getattr(c, "duration_ms", 0), 1),
                    "success": getattr(c, "success", True),
                }
                for i, c in enumerate(calls_sorted)
            ],
            "by_server_summary": {
                srv: {"calls": len(v), "failures": sum(1 for x in v if not x["success"])}
                for srv, v in by_server.items()
            },
            "related_alerts": alerts,
        }


_telemetry_server: Optional[TelemetryServer] = None


def get_telemetry_server() -> TelemetryServer:
    global _telemetry_server
    if _telemetry_server is None:
        _telemetry_server = TelemetryServer()
    return _telemetry_server
