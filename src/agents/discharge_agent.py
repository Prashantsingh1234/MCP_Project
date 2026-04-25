"""DischargeCoordinationAgent (async MCP client).

Orchestrates discharge workflow across three MCP servers running locally via
FastMCP HTTP SSE transport:
  - EHR (8001)
  - Pharmacy (8002)
  - Billing (8003)
"""

from __future__ import annotations

import sys
import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

# Ensure project root is on sys.path when running as a script:
#   python src/agents/discharge_agent.py PAT-001
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.exceptions import MCPConnectionError, ToolExecutionError

logger = logging.getLogger("MCPDischargeAgent")


def _extract_tool_result(result: Any) -> Any:
    """Normalize MCP SDK tool results into plain Python values."""
    if isinstance(result, (dict, list, str, int, float, bool)) or result is None:
        return result

    # Common shapes in MCP Python SDK implementations
    content = getattr(result, "content", None) or getattr(result, "contents", None)
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict):
            if first.get("type") == "json" and "json" in first:
                return first["json"]
            if first.get("type") == "text" and "text" in first:
                try:
                    return json.loads(first["text"])
                except Exception:
                    return first["text"]
        # pydantic models / typed objects
        if hasattr(first, "type"):
            if getattr(first, "type") == "json" and hasattr(first, "json"):
                return getattr(first, "json")
            if getattr(first, "type") == "text" and hasattr(first, "text"):
                text = getattr(first, "text")
                try:
                    return json.loads(text)
                except Exception:
                    return text

    # As a last resort, try to JSON-serialize typed objects
    if hasattr(result, "model_dump"):
        return result.model_dump()
    if hasattr(result, "dict"):
        return result.dict()
    return result


class AsyncMCPToolClient:
    """Async MCP client for a single FastMCP SSE server."""

    def __init__(self, sse_url: str):
        self.sse_url = sse_url
        self._session_cm = None
        self._session = None

    async def __aenter__(self):
        try:
            # Preferred: official MCP Python SDK
            from mcp.client.sse import sse_client  # type: ignore
            from mcp.client.session import ClientSession  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise MCPConnectionError(
                server=self.sse_url,
                reason="Missing MCP client dependencies. Install with: pip install -r requirements.txt",
            ) from exc

        self._session_cm = sse_client(self.sse_url)
        read, write = await self._session_cm.__aenter__()  # type: ignore[attr-defined]
        self._session = ClientSession(read, write)
        await self._session.__aenter__()  # type: ignore[attr-defined]
        await self._session.initialize()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._session is not None:
            await self._session.__aexit__(exc_type, exc, tb)  # type: ignore[attr-defined]
        if self._session_cm is not None:
            await self._session_cm.__aexit__(exc_type, exc, tb)  # type: ignore[attr-defined]

    async def call_tool(self, tool: str, arguments: dict[str, Any]) -> Any:
        if self._session is None:
            raise RuntimeError("Client session is not initialized")
        try:
            raw = await self._session.call_tool(tool, arguments)  # type: ignore[attr-defined]
            return _extract_tool_result(raw)
        except Exception as exc:
            raise ToolExecutionError(tool=tool, server=self.sse_url, reason=str(exc)) from exc


@dataclass
class AgentMetrics:
    mcp_calls: int = 0
    retries: int = 0
    failures: int = 0
    successes: int = 0


class DischargeCoordinationAgent:
    """End-to-end discharge orchestrator using MCP tool calls."""

    def __init__(
        self,
        ehr_sse_url: str = "http://localhost:8001/sse",
        pharmacy_sse_url: str = "http://localhost:8002/sse",
        billing_sse_url: str = "http://localhost:8003/sse",
        role: str = "discharge_coordinator",
        max_retries: int = 3,
    ):
        self.ehr_sse_url = ehr_sse_url
        self.pharmacy_sse_url = pharmacy_sse_url
        self.billing_sse_url = billing_sse_url
        self.role = role
        self.max_retries = max_retries
        self.metrics = AgentMetrics()

    async def _retry_call(self, client: AsyncMCPToolClient, tool: str, arguments: dict[str, Any]) -> Any:
        base_delay = 0.2
        for attempt in range(1, self.max_retries + 1):
            self.metrics.mcp_calls += 1
            try:
                result = await client.call_tool(tool, arguments)
                self.metrics.successes += 1
                return result
            except Exception as exc:
                self.metrics.failures += 1
                if attempt >= self.max_retries:
                    raise
                self.metrics.retries += 1
                jitter = random.uniform(0, 0.1)
                delay = base_delay * (2 ** (attempt - 1)) + jitter
                logger.warning("Retrying %s (attempt %s/%s): %s", tool, attempt, self.max_retries, exc)
                await asyncio.sleep(delay)

    async def orchestrate_discharge(self, patient_id: str) -> dict[str, Any]:
        start = time.perf_counter()

        async with AsyncMCPToolClient(self.ehr_sse_url) as ehr, AsyncMCPToolClient(
            self.pharmacy_sse_url
        ) as pharmacy, AsyncMCPToolClient(self.billing_sse_url) as billing:
            # 1) EHR → medications
            meds = await self._retry_call(
                ehr,
                "get_discharge_medications",
                {"patient_id": patient_id, "role": self.role},
            )

            # 2) Pharmacy → stock + alternatives (if needed)
            pharmacy_results: list[dict[str, Any]] = []
            for med in meds:
                drug_name = med.get("brand") or med.get("drug_name")
                dose = med.get("dose")
                quantity = int(med.get("days_supply", 1))

                stock = await self._retry_call(
                    pharmacy,
                    "check_stock",
                    {"drug_name": drug_name, "quantity": 1, "dose": dose, "role": self.role},
                )

                alternative: Optional[dict[str, Any]] = None
                if stock.get("found") and not stock.get("available"):
                    alternative = await self._retry_call(
                        pharmacy,
                        "get_alternative",
                        {"drug_name": drug_name, "role": self.role},
                    )

                pharmacy_results.append(
                    {
                        "ehr_med": med,
                        "stock": stock,
                        "alternative": alternative,
                    }
                )

            # 3) EHR → billing-safe summary
            billing_safe = await self._retry_call(
                ehr, "get_billing_safe_summary", {"patient_id": patient_id, "role": self.role}
            )

            # 4) Pharmacy → price for each drug (use generic name if available)
            drug_charges: list[dict[str, Any]] = []
            for pr in pharmacy_results:
                med = pr["ehr_med"]
                qty = int(med.get("days_supply", 1))
                stock = pr["stock"] or {}
                alt = pr.get("alternative") or {}

                # Prefer pricing the dispensible option:
                # - if not found: skip pricing and continue
                # - if out of stock: price the first alternative when available
                drug_name: Optional[str] = stock.get("generic_name") or med.get("drug_name") or med.get("brand")
                if not stock.get("found", True):
                    drug_charges.append({"total_price_inr": 0, "dispensing_fee": 0})
                    continue

                if stock.get("found") and not stock.get("available"):
                    alternatives = alt.get("alternatives", [])
                    if alternatives:
                        drug_name = alternatives[0].get("generic_name") or drug_name

                try:
                    price = await self._retry_call(
                        pharmacy,
                        "get_price",
                        {"drug_name": drug_name, "quantity": qty, "role": self.role},
                    )
                    drug_charges.append(price)
                except Exception:
                    drug_charges.append({"total_price_inr": 0, "dispensing_fee": 0})

            # 5) Billing → invoice
            # Billing should only receive ICD/LOS/ward and drug pricing fields (no clinical context).
            billing_drug_charges = [
                {
                    "total_price_inr": d.get("total_price_inr"),
                    "dispensing_fee": d.get("dispensing_fee", 0),
                }
                for d in drug_charges
            ]
            invoice = await self._retry_call(
                billing,
                "generate_invoice",
                {
                    "patient_id": patient_id,
                    "billing_safe_ehr": billing_safe,
                    "drug_charges": billing_drug_charges,
                    "role": self.role,
                },
            )

        latency_ms = (time.perf_counter() - start) * 1000
        alerts: list[dict[str, Any]] = []
        conflicts: list[dict[str, Any]] = []
        for idx, pr in enumerate(pharmacy_results):
            med = pr["ehr_med"]
            stock = pr["stock"]
            drug = med.get("brand") or med.get("drug_name")

            if not stock.get("found", True):
                alerts.append(
                    {
                        "type": "DRUG_NOT_FOUND",
                        "severity": "HIGH",
                        "drug": drug,
                        "message": f"{drug} not found in pharmacy formulary",
                    }
                )

            if stock.get("found") and not stock.get("available"):
                alt = pr.get("alternative") or {}
                alternatives = alt.get("alternatives", [])
                alt_name = alternatives[0]["generic_name"] if alternatives else None
                alerts.append(
                    {
                        "type": "OUT_OF_STOCK",
                        "severity": "HIGH",
                        "drug": drug,
                        "message": f"{drug} is OUT OF STOCK" + (f"; alternative: {alt_name}" if alt_name else ""),
                    }
                )

            if stock.get("dose_conflict"):
                conflicts.append(
                    {
                        "type": "DOSE_CONFLICT",
                        "severity": "HIGH",
                        "drug": drug,
                        "detail": stock.get("dose_conflict_detail"),
                    }
                )

            if med.get("brand") and stock.get("generic_name") and med.get("brand") != stock.get("generic_name"):
                alerts.append(
                    {
                        "type": "NAME_MISMATCH",
                        "severity": "MEDIUM",
                        "drug": med.get("brand"),
                        "message": f"EHR brand '{med.get('brand')}' resolved to generic '{stock.get('generic_name')}'",
                    }
                )

            # If pricing was unavailable (tool error), it will show up as a zeroed charge.
            charge = drug_charges[idx] if idx < len(drug_charges) else {}
            if stock.get("found", True) and charge.get("total_price_inr", 0) == 0 and med.get("days_supply", 0) not in (0, None):
                # Avoid flagging legitimate free items (not expected here) by requiring days_supply
                if stock.get("available") or pr.get("alternative"):
                    alerts.append(
                        {
                            "type": "PRICE_UNAVAILABLE",
                            "severity": "MEDIUM",
                            "drug": drug,
                            "message": f"Price lookup failed for {drug}; invoice uses 0 INR for this line item",
                        }
                    )

        total_calls = self.metrics.mcp_calls
        success_rate = (self.metrics.successes / total_calls) if total_calls else 0.0
        return {
            "patient_id": patient_id,
            "role": self.role,
            "orchestration_complete": True,
            "total_latency_ms": round(latency_ms, 1),
            "mcp_tool_calls_total": total_calls,
            "mcp_tool_calls_success": self.metrics.successes,
            "mcp_tool_calls_failed": self.metrics.failures,
            "tool_call_success_rate": round(success_rate, 3),
            "alerts": alerts,
            "data_integrity_conflicts": conflicts,
            "discharge_medications": meds,
            "pharmacy_results": pharmacy_results,
            "billing_safe_summary": billing_safe,
            "phi_boundary_enforced": True,
            "phi_blocked_fields": billing_safe.get("blocked_fields", []),
            "drug_charges": drug_charges,
            "invoice": invoice,
        }


class WorkflowMetrics:
    """Compare traditional manual workflow vs MCP automated workflow."""

    TRADITIONAL_MANUAL_STEPS = {
        "total_manual_handoffs": 15,
        "avg_time_minutes": 45,
        "error_rate_pct": 18,
    }

    @staticmethod
    def compute(result: dict[str, Any]) -> dict[str, Any]:
        tool_calls = int(result.get("mcp_tool_calls_total", 0))
        latency_s = float(result.get("total_latency_ms", 0)) / 1000.0
        return {
            "mcp_tool_calls": tool_calls,
            "mcp_latency_seconds": latency_s,
            "manual_handoffs_replaced": WorkflowMetrics.TRADITIONAL_MANUAL_STEPS["total_manual_handoffs"],
            "time_saved_minutes": WorkflowMetrics.TRADITIONAL_MANUAL_STEPS["avg_time_minutes"] - (latency_s / 60.0),
            "tool_call_success_rate": float(result.get("tool_call_success_rate", 0.0)),
            "data_integrity_conflicts_detected": len(result.get("data_integrity_conflicts", [])),
            "stock_alerts": sum(1 for a in result.get("alerts", []) if a.get("type") == "OUT_OF_STOCK"),
            "phi_boundary_enforced": bool(result.get("phi_boundary_enforced", False)),
        }


def test_scope_violation(patient_id: str = "PAT-006") -> dict[str, Any]:
    """RBAC + PHI boundary demonstration (uses MCP over SSE)."""

    async def _run():
        results: dict[str, Any] = {}
        async with AsyncMCPToolClient("http://localhost:8001/sse") as ehr, AsyncMCPToolClient(
            "http://localhost:8003/sse"
        ) as billing:
            # Allowed: billing-safe summary for billing_agent
            try:
                safe = await ehr.call_tool(
                    "get_billing_safe_summary", {"patient_id": patient_id, "role": "billing_agent"}
                )
                results["billing_safe_summary"] = {"success": True, "fields": list(safe.keys())}
            except Exception as exc:
                results["billing_safe_summary"] = {"success": False, "error": str(exc)}

            # Blocked: billing_agent cannot read discharge medications
            try:
                _ = await ehr.call_tool("get_discharge_medications", {"patient_id": patient_id, "role": "billing_agent"})
                results["clinical_medications"] = {"success": True, "ALERT": "RBAC should have blocked this"}
            except Exception as exc:
                results["clinical_medications"] = {"success": False, "rbac_blocked": True, "error": str(exc)}

            # Blocked: pharmacy_agent cannot access billing charges
            try:
                _ = await billing.call_tool(
                    "get_charges", {"ward": "Cardiology", "los_days": 1, "role": "pharmacy_agent"}
                )
                results["pharmacy_billing_access"] = {"success": True, "ALERT": "RBAC should have blocked this"}
            except Exception as exc:
                results["pharmacy_billing_access"] = {"success": False, "rbac_blocked": True, "error": str(exc)}

        return results

    return asyncio.run(_run())


async def _main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("patient_id", help="e.g. PAT-001")
    parser.add_argument("--role", default="discharge_coordinator")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    agent = DischargeCoordinationAgent(role=args.role)
    result = await agent.orchestrate_discharge(args.patient_id)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(_main())
