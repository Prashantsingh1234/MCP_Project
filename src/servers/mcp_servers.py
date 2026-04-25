"""MCPDischarge FastMCP SSE servers.

Implements three MCP servers (EHR/Pharmacy/Billing) using FastMCP with HTTP SSE
transport, each exposing production-style typed tools with RBAC + telemetry.

Ports:
  - EHR:      8001
  - Pharmacy: 8002
  - Billing:  8003
"""

from __future__ import annotations

import sys
import argparse
import logging
import threading
import time
from pathlib import Path
from typing import Any, Optional

# Ensure project root is on sys.path when running as a script:
#   python src/servers/mcp_servers.py --all
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.servers.ehr_server import get_ehr_server
from src.servers.pharmacy_server import get_pharmacy_server
from src.servers.billing_server import get_billing_server
from src.utils.telemetry import MCPCallTimer, get_telemetry

logger = logging.getLogger(__name__)


def _require_fastmcp():
    try:
        from fastmcp import FastMCP  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "fastmcp is required to run HTTP SSE servers. "
            "Install dependencies with: pip install -r requirements.txt"
        ) from exc
    return FastMCP


def create_ehr_mcp():
    FastMCP = _require_fastmcp()
    ehr = get_ehr_server()
    mcp = FastMCP("EHR-Server")

    @mcp.tool()
    def get_patient_discharge_summary(patient_id: str, role: str) -> dict[str, Any]:
        with MCPCallTimer("ehr", "get_patient_discharge_summary", role, patient_id):
            return ehr.get_patient_discharge_summary(patient_id, role)

    @mcp.tool()
    def get_discharge_medications(patient_id: str, role: str) -> list[dict]:
        with MCPCallTimer("ehr", "get_discharge_medications", role, patient_id):
            return ehr.get_discharge_medications(patient_id, role)

    @mcp.tool()
    def get_diagnosis_codes(patient_id: str, role: str) -> dict[str, Any]:
        with MCPCallTimer("ehr", "get_diagnosis_codes", role, patient_id):
            return ehr.get_diagnosis_codes(patient_id, role)

    @mcp.tool()
    def get_admission_info(patient_id: str, role: str) -> dict[str, Any]:
        with MCPCallTimer("ehr", "get_admission_info", role, patient_id):
            return ehr.get_admission_info(patient_id, role)

    @mcp.tool()
    def get_billing_safe_summary(patient_id: str, role: str) -> dict[str, Any]:
        with MCPCallTimer("ehr", "get_billing_safe_summary", role, patient_id):
            return ehr.get_billing_safe_summary(patient_id, role)

    return mcp


def create_pharmacy_mcp():
    FastMCP = _require_fastmcp()
    pharmacy = get_pharmacy_server()
    mcp = FastMCP("Pharmacy-Server")

    @mcp.tool()
    def check_stock(
        drug_name: str,
        quantity: int = 1,
        dose: Optional[str] = None,
        role: str = "discharge_coordinator",
    ) -> dict[str, Any]:
        with MCPCallTimer("pharmacy", "check_stock", role, None):
            return pharmacy.check_stock(drug_name, quantity=quantity, dose=dose, role=role)

    @mcp.tool()
    def get_alternative(drug_name: str, role: str = "discharge_coordinator") -> dict[str, Any]:
        with MCPCallTimer("pharmacy", "get_alternative", role, None):
            return pharmacy.get_alternative(drug_name, role=role)

    @mcp.tool()
    def get_price(
        drug_name: str, quantity: int = 1, role: str = "discharge_coordinator"
    ) -> dict[str, Any]:
        with MCPCallTimer("pharmacy", "get_price", role, None):
            return pharmacy.get_price(drug_name, quantity=quantity, role=role)

    @mcp.tool()
    def dispense_request(
        patient_id: str,
        drug_name: str,
        quantity: int,
        dose: str,
        frequency: str,
        days_supply: int,
        route: str,
        role: str = "discharge_coordinator",
    ) -> dict[str, Any]:
        with MCPCallTimer("pharmacy", "dispense_request", role, patient_id):
            return pharmacy.dispense_request(
                patient_id=patient_id,
                drug_name=drug_name,
                quantity=quantity,
                dose=dose,
                frequency=frequency,
                days_supply=days_supply,
                route=route,
                role=role,
            )

    return mcp


def create_billing_mcp():
    FastMCP = _require_fastmcp()
    billing = get_billing_server()
    mcp = FastMCP("Billing-Server")

    @mcp.tool()
    def get_charges(ward: str, los_days: int, role: str = "discharge_coordinator") -> dict[str, Any]:
        with MCPCallTimer("billing", "get_charges", role, None):
            return billing.get_charges(ward=ward, los_days=los_days, role=role)

    @mcp.tool()
    def get_insurance(patient_id: str, role: str = "discharge_coordinator") -> dict[str, Any]:
        with MCPCallTimer("billing", "get_insurance", role, patient_id):
            return billing.get_insurance(patient_id=patient_id, role=role)

    @mcp.tool()
    def generate_invoice(
        patient_id: str,
        billing_safe_ehr: dict[str, Any],
        drug_charges: list[dict[str, Any]],
        role: str = "discharge_coordinator",
    ) -> dict[str, Any]:
        with MCPCallTimer("billing", "generate_invoice", role, patient_id):
            return billing.generate_invoice(
                patient_id=patient_id,
                billing_safe_ehr=billing_safe_ehr,
                drug_charges=drug_charges,
                role=role,
            )

    return mcp


def run_one(server: str):
    server = server.lower().strip()
    apps = {
        "ehr": (create_ehr_mcp, 8001),
        "pharmacy": (create_pharmacy_mcp, 8002),
        "billing": (create_billing_mcp, 8003),
    }
    if server not in apps:
        raise SystemExit(f"Unknown server '{server}'. Choose from: ehr, pharmacy, billing.")

    factory, port = apps[server]
    app = factory()
    logger.info("Starting %s MCP server on port %s", server, port)
    app.run(transport="sse", host="0.0.0.0", port=port)


def run_all():
    threads: list[threading.Thread] = []

    def spawn(name: str, port: int, factory):
        app = factory()
        t = threading.Thread(target=app.run, kwargs={"transport": "sse", "host": "0.0.0.0", "port": port})
        t.daemon = True
        t.start()
        logger.info("Started %s MCP server: http://localhost:%s", name, port)
        threads.append(t)

    spawn("ehr", 8001, create_ehr_mcp)
    spawn("pharmacy", 8002, create_pharmacy_mcp)
    spawn("billing", 8003, create_billing_mcp)

    logger.info("All servers running. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("Shutting down.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", choices=["ehr", "pharmacy", "billing"])
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    get_telemetry()  # initialize telemetry early

    if args.all:
        run_all()
        return

    if args.server:
        run_one(args.server)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
