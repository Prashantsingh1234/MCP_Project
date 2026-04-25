"""MCPDischarge demo scenarios.

Prereq: start all MCP servers (FastMCP SSE):
  python src/servers/mcp_servers.py --all

Then run:
  python demo/demo.py
  python demo/demo.py --scenario 2
"""

from __future__ import annotations

import sys
import argparse
import asyncio
import json
import logging
from pathlib import Path
from typing import Any

# Ensure project root is on sys.path when running as a script:
#   python demo/demo.py
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agents.discharge_agent import DischargeCoordinationAgent, AsyncMCPToolClient


async def scenario_normal():
    agent = DischargeCoordinationAgent(role="discharge_coordinator")
    result = await agent.orchestrate_discharge("PAT-002")
    return {"scenario": "normal_flow", "result": result}


async def scenario_out_of_stock():
    agent = DischargeCoordinationAgent(role="discharge_coordinator")
    result = await agent.orchestrate_discharge("PAT-001")
    return {"scenario": "out_of_stock", "result": result}


async def scenario_rbac_violation():
    # billing_agent is NOT allowed to call EHR.get_discharge_medications (clinical scope)
    agent = DischargeCoordinationAgent(role="billing_agent")
    try:
        await agent.orchestrate_discharge("PAT-006")
        return {"scenario": "rbac_violation", "unexpected": "RBAC did not block as expected"}
    except Exception as exc:
        return {"scenario": "rbac_violation", "blocked": True, "error": str(exc)}


async def scenario_phi_boundary_violation():
    # Billing must reject payloads containing PHI fields anywhere.
    async with AsyncMCPToolClient("http://localhost:8003/sse") as billing:
        payload = {
            "patient_id": "PAT-001",
            "billing_safe_ehr": {
                "patient_id": "PAT-001",
                "name": "SHOULD_NOT_BE_HERE",
                "ward": "Cardiology",
                "los_days": 5,
                "diagnosis_icd10": ["I50.20"],
            },
            "drug_charges": [{"drug_name": "Dapagliflozin", "total_price_inr": 100}],
            "role": "discharge_coordinator",
        }
        try:
            invoice = await billing.call_tool("generate_invoice", payload)
            return {"scenario": "phi_boundary_violation", "unexpected": invoice}
        except Exception as exc:
            return {"scenario": "phi_boundary_violation", "blocked": True, "error": str(exc)}


SCENARIOS: list[tuple[str, Any]] = [
    ("normal_flow", scenario_normal),
    ("out_of_stock", scenario_out_of_stock),
    ("rbac_violation", scenario_rbac_violation),
    ("phi_boundary_violation", scenario_phi_boundary_violation),
]


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", type=int, choices=[1, 2, 3, 4])
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.scenario:
        name, fn = SCENARIOS[args.scenario - 1]
        out = await fn()
        print(json.dumps(out, indent=2))
        return

    results = []
    for _, fn in SCENARIOS:
        results.append(await fn())
    print(json.dumps({"scenarios": results}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
