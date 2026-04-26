"""MCPDischarge FastMCP SSE servers.

Five MCP servers over HTTP SSE transport with RBAC + telemetry on every tool.

Ports:
  - EHR:       8001  (clinical source of truth, PHI-protected)
  - Pharmacy:  8002  (stock, pricing, dispensing)
  - Billing:   8003  (invoices, insurance, payment)
  - Security:  8004  (RBAC access-control tools)
  - Telemetry: 8005  (observability & workflow traces)
"""

from __future__ import annotations

import sys
import argparse
import logging
import threading
import time
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.servers.ehr_server import get_ehr_server
from src.servers.pharmacy_server import get_pharmacy_server
from src.servers.billing_server import get_billing_server
from src.servers.security_server import get_security_server
from src.servers.telemetry_server import get_telemetry_server
from src.utils.telemetry import MCPCallTimer, get_telemetry

logger = logging.getLogger(__name__)


def _require_fastmcp():
    try:
        from fastmcp import FastMCP  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "fastmcp is required. Install with: pip install -r requirements.txt"
        ) from exc
    return FastMCP


# ══════════════════════════════════════════════════════════════════════════════
# EHR SERVER  (port 8001)
# ══════════════════════════════════════════════════════════════════════════════

def create_ehr_mcp():
    FastMCP = _require_fastmcp()
    ehr = get_ehr_server()
    mcp = FastMCP("EHR-Server")

    # ── Core Clinical ─────────────────────────────────────────────────────────
    @mcp.tool()
    def get_patient_discharge_summary(patient_id: str, role: str = "discharge_coordinator", caller_role: Optional[str] = None) -> dict[str, Any]:
        """Get full patient discharge summary (PHI — restricted via RBAC)."""
        role = caller_role or role
        with MCPCallTimer("ehr", "get_patient_discharge_summary", role, patient_id):
            return ehr.get_patient_discharge_summary(patient_id, role)

    @mcp.tool()
    def get_discharge_medications(patient_id: str, role: str = "discharge_coordinator", caller_role: Optional[str] = None) -> list[dict]:
        """Get structured discharge medication list for a patient."""
        role = caller_role or role
        with MCPCallTimer("ehr", "get_discharge_medications", role, patient_id):
            return ehr.get_discharge_medications(patient_id, role)

    @mcp.tool()
    def get_diagnosis_codes(patient_id: str, role: str = "discharge_coordinator", caller_role: Optional[str] = None) -> dict[str, Any]:
        """Get ICD-10 diagnosis codes only (PHI-safe)."""
        role = caller_role or role
        with MCPCallTimer("ehr", "get_diagnosis_codes", role, patient_id):
            return ehr.get_diagnosis_codes(patient_id, role)

    @mcp.tool()
    def get_admission_info(patient_id: str, role: str = "discharge_coordinator", caller_role: Optional[str] = None) -> dict[str, Any]:
        """Get ward, admission date, discharge date, and length-of-stay."""
        role = caller_role or role
        with MCPCallTimer("ehr", "get_admission_info", role, patient_id):
            return ehr.get_admission_info(patient_id, role)

    @mcp.tool()
    def list_patients(role: str = "discharge_coordinator", caller_role: Optional[str] = None) -> list[str]:
        """List patient IDs (PHI-safe)."""
        role = caller_role or role
        with MCPCallTimer("ehr", "list_patients", role, None):
            return ehr.list_patients(role)

    # ── PHI-Safe ──────────────────────────────────────────────────────────────
    @mcp.tool()
    def get_billing_safe_summary(patient_id: str, role: str = "discharge_coordinator", caller_role: Optional[str] = None) -> dict[str, Any]:
        """Get billing-safe patient summary (all PHI fields stripped)."""
        role = caller_role or role
        with MCPCallTimer("ehr", "get_billing_safe_summary", role, patient_id):
            return ehr.get_billing_safe_summary(patient_id, role)

    # ── Clinical Validation ───────────────────────────────────────────────────
    @mcp.tool()
    def validate_prescription(patient_id: str, role: str = "discharge_coordinator", caller_role: Optional[str] = None) -> dict[str, Any]:
        """Check if discharge prescriptions are complete and valid."""
        role = caller_role or role
        with MCPCallTimer("ehr", "validate_prescription", role, patient_id):
            return ehr.validate_prescription(patient_id, role)

    @mcp.tool()
    def check_drug_interactions(medications: list[dict], role: str = "discharge_coordinator", caller_role: Optional[str] = None) -> dict[str, Any]:
        """Detect harmful drug interactions in a medication list."""
        role = caller_role or role
        with MCPCallTimer("ehr", "check_drug_interactions", role, None):
            return ehr.check_drug_interactions(medications, role)

    @mcp.tool()
    def check_dose_validity(drug_name: str, dose: str, role: str = "discharge_coordinator", caller_role: Optional[str] = None) -> dict[str, Any]:
        """Validate prescribed dose against formulary standards."""
        role = caller_role or role
        with MCPCallTimer("ehr", "check_dose_validity", role, None):
            return ehr.check_dose_validity(drug_name, dose, role)

    # ── Update / Workflow ─────────────────────────────────────────────────────
    @mcp.tool()
    def update_prescription(patient_id: str, updated_med_list: list[dict], role: str = "clinical_agent", caller_role: Optional[str] = None) -> dict[str, Any]:
        """Update discharge medications after doctor re-prescribes."""
        role = caller_role or role
        with MCPCallTimer("ehr", "update_prescription", role, patient_id):
            return ehr.update_prescription(patient_id, updated_med_list, role)

    @mcp.tool()
    def mark_patient_ready_for_discharge(patient_id: str, role: str = "clinical_agent", caller_role: Optional[str] = None) -> dict[str, Any]:
        """Mark patient discharge status as ready."""
        role = caller_role or role
        with MCPCallTimer("ehr", "mark_patient_ready_for_discharge", role, patient_id):
            return ehr.mark_patient_ready_for_discharge(patient_id, role)

    # ── Audit / Logging ───────────────────────────────────────────────────────
    @mcp.tool()
    def get_patient_history(patient_id: str, role: str = "discharge_coordinator", caller_role: Optional[str] = None) -> dict[str, Any]:
        """Get past prescriptions and diagnosis history for a patient."""
        role = caller_role or role
        with MCPCallTimer("ehr", "get_patient_history", role, patient_id):
            return ehr.get_patient_history(patient_id, role)

    # ── Real-World Edge Cases ─────────────────────────────────────────────────
    @mcp.tool()
    def mark_urgent_request(patient_id: str, role: str = "discharge_coordinator", caller_role: Optional[str] = None) -> dict[str, Any]:
        """Flag a patient request as urgent for prioritized processing."""
        role = caller_role or role
        with MCPCallTimer("ehr", "mark_urgent_request", role, patient_id):
            return ehr.mark_urgent_request(patient_id, role)

    @mcp.tool()
    def escalate_to_doctor(patient_id: str, issue: str, role: str = "discharge_coordinator", caller_role: Optional[str] = None) -> dict[str, Any]:
        """Trigger doctor review/escalation for a clinical issue."""
        role = caller_role or role
        with MCPCallTimer("ehr", "escalate_to_doctor", role, patient_id):
            return ehr.escalate_to_doctor(patient_id, issue, role)

    @mcp.tool()
    def request_represcription(patient_id: str, drug_name: str, reason: str, role: str = "discharge_coordinator", caller_role: Optional[str] = None) -> dict[str, Any]:
        """Request doctor re-prescription when no suitable alternative exists."""
        role = caller_role or role
        with MCPCallTimer("ehr", "request_represcription", role, patient_id):
            return ehr.request_represcription(patient_id, drug_name, reason, role)

    # ── Notifications ─────────────────────────────────────────────────────────
    @mcp.tool()
    def notify_patient(message: str, patient_id: Optional[str] = None, role: str = "discharge_coordinator", caller_role: Optional[str] = None) -> dict[str, Any]:
        """Send a notification message to the patient via the SMS portal."""
        role = caller_role or role
        with MCPCallTimer("ehr", "notify_patient", role, patient_id):
            return ehr.notify_patient(message, patient_id, role)

    @mcp.tool()
    def notify_doctor(message: str, patient_id: str, role: str = "discharge_coordinator", caller_role: Optional[str] = None) -> dict[str, Any]:
        """Send a notification to the attending physician via the paging system."""
        role = caller_role or role
        with MCPCallTimer("ehr", "notify_doctor", role, patient_id):
            return ehr.notify_doctor(message, patient_id, role)

    # ── Data Validation ───────────────────────────────────────────────────────
    @mcp.tool()
    def validate_patient_id(patient_id: str, role: str = "discharge_coordinator", caller_role: Optional[str] = None) -> dict[str, Any]:
        """Check if a patient ID exists in the EHR system."""
        role = caller_role or role
        with MCPCallTimer("ehr", "validate_patient_id", role, patient_id):
            return ehr.validate_patient_id(patient_id, role)

    return mcp


# ══════════════════════════════════════════════════════════════════════════════
# PHARMACY SERVER  (port 8002)
# ══════════════════════════════════════════════════════════════════════════════

def create_pharmacy_mcp():
    FastMCP = _require_fastmcp()
    pharmacy = get_pharmacy_server()
    mcp = FastMCP("Pharmacy-Server")

    # ── Stock & Availability ──────────────────────────────────────────────────
    @mcp.tool()
    def check_stock(drug_name: str, quantity: int = 1, dose: Optional[str] = None, role: str = "discharge_coordinator", caller_role: Optional[str] = None) -> dict[str, Any]:
        """Check stock availability for a single drug (generic or brand name)."""
        role = caller_role or role
        with MCPCallTimer("pharmacy", "check_stock", role, None):
            return pharmacy.check_stock(drug_name, quantity=quantity, dose=dose, role=role)

    @mcp.tool()
    def check_bulk_stock(drug_list: list[dict], role: str = "discharge_coordinator", caller_role: Optional[str] = None) -> dict[str, Any]:
        """Check stock for multiple drugs in one call."""
        role = caller_role or role
        with MCPCallTimer("pharmacy", "check_bulk_stock", role, None):
            return pharmacy.check_bulk_stock(drug_list, role=role)

    @mcp.tool()
    def list_in_stock_drugs(role: str = "discharge_coordinator", caller_role: Optional[str] = None) -> list[dict]:
        """List all drugs currently in stock (no patient ID required)."""
        role = caller_role or role
        with MCPCallTimer("pharmacy", "list_in_stock_drugs", role, None):
            return pharmacy.list_in_stock_drugs(role=role)

    # ── Alternative Handling ──────────────────────────────────────────────────
    @mcp.tool()
    def get_alternative(drug_name: str, role: str = "discharge_coordinator", caller_role: Optional[str] = None) -> dict[str, Any]:
        """Get the primary therapeutic alternative when a drug is unavailable."""
        role = caller_role or role
        with MCPCallTimer("pharmacy", "get_alternative", role, None):
            return pharmacy.get_alternative(drug_name, role=role)

    @mcp.tool()
    def get_all_alternatives(drug_name: str, role: str = "discharge_coordinator", caller_role: Optional[str] = None) -> dict[str, Any]:
        """Get all possible therapeutic alternatives for a drug."""
        role = caller_role or role
        with MCPCallTimer("pharmacy", "get_all_alternatives", role, None):
            return pharmacy.get_all_alternatives(drug_name, role=role)

    @mcp.tool()
    def check_therapeutic_equivalence(drug_a: str, drug_b: str, role: str = "discharge_coordinator", caller_role: Optional[str] = None) -> dict[str, Any]:
        """Validate whether two drugs are clinically therapeutically equivalent."""
        role = caller_role or role
        with MCPCallTimer("pharmacy", "check_therapeutic_equivalence", role, None):
            return pharmacy.check_therapeutic_equivalence(drug_a, drug_b, role=role)

    # ── Drug Matching ─────────────────────────────────────────────────────────
    @mcp.tool()
    def resolve_drug_name_alias(input_name: str, role: str = "discharge_coordinator", caller_role: Optional[str] = None) -> dict[str, Any]:
        """Convert between brand and generic drug names."""
        role = caller_role or role
        with MCPCallTimer("pharmacy", "resolve_drug_name_alias", role, None):
            return pharmacy.resolve_drug_name_alias(input_name, role=role)

    @mcp.tool()
    def semantic_drug_search(query: str, role: str = "discharge_coordinator", caller_role: Optional[str] = None) -> dict[str, Any]:
        """Fuzzy/semantic search for drugs by partial or misspelled name."""
        role = caller_role or role
        with MCPCallTimer("pharmacy", "semantic_drug_search", role, None):
            return pharmacy.semantic_drug_search(query, role=role)

    # ── Pricing ───────────────────────────────────────────────────────────────
    @mcp.tool()
    def get_price(drug_name: str, quantity: int = 1, role: str = "discharge_coordinator", caller_role: Optional[str] = None) -> dict[str, Any]:
        """Get unit and total price for a drug and quantity."""
        role = caller_role or role
        with MCPCallTimer("pharmacy", "get_price", role, None):
            return pharmacy.get_price(drug_name, quantity=quantity, role=role)

    @mcp.tool()
    def get_bulk_price(drug_list: list[dict], role: str = "discharge_coordinator", caller_role: Optional[str] = None) -> dict[str, Any]:
        """Get pricing for multiple drugs in one call."""
        role = caller_role or role
        with MCPCallTimer("pharmacy", "get_bulk_price", role, None):
            return pharmacy.get_bulk_price(drug_list, role=role)

    # ── Dispensing ────────────────────────────────────────────────────────────
    @mcp.tool()
    def dispense_request(patient_id: str, drug_name: str, quantity: int, dose: str, frequency: str, days_supply: int, route: str, role: str = "discharge_coordinator", caller_role: Optional[str] = None) -> dict[str, Any]:
        """Submit a single drug dispense request for a patient."""
        role = caller_role or role
        with MCPCallTimer("pharmacy", "dispense_request", role, patient_id):
            return pharmacy.dispense_request(
                patient_id=patient_id, drug_name=drug_name, quantity=quantity,
                dose=dose, frequency=frequency, days_supply=days_supply,
                route=route, role=role,
            )

    @mcp.tool()
    def create_dispense_request(patient_id: str, drug_list: list[dict], role: str = "discharge_coordinator", caller_role: Optional[str] = None) -> dict[str, Any]:
        """Submit a bulk dispense request for a patient's full medication list."""
        role = caller_role or role
        with MCPCallTimer("pharmacy", "create_dispense_request", role, patient_id):
            return pharmacy.create_dispense_request(patient_id, drug_list, role=role)

    @mcp.tool()
    def confirm_dispense(patient_id: str, role: str = "discharge_coordinator", caller_role: Optional[str] = None) -> dict[str, Any]:
        """Confirm that medicines have been issued to the patient."""
        role = caller_role or role
        with MCPCallTimer("pharmacy", "confirm_dispense", role, patient_id):
            return pharmacy.confirm_dispense(patient_id, role=role)

    # ── Inventory Management ──────────────────────────────────────────────────
    @mcp.tool()
    def update_stock(drug_name: str, quantity: int, role: str = "pharmacy_agent", caller_role: Optional[str] = None) -> dict[str, Any]:
        """Adjust inventory level for a drug (add or remove units)."""
        role = caller_role or role
        with MCPCallTimer("pharmacy", "update_stock", role, None):
            return pharmacy.update_stock(drug_name, quantity, role=role)

    @mcp.tool()
    def check_nearby_pharmacy_availability(drug_name: str, role: str = "discharge_coordinator", caller_role: Optional[str] = None) -> dict[str, Any]:
        """Check availability at external/nearby pharmacies when local stock is out."""
        role = caller_role or role
        with MCPCallTimer("pharmacy", "check_nearby_pharmacy_availability", role, None):
            return pharmacy.check_nearby_pharmacy_availability(drug_name, role=role)

    # ── Alerts & Conflicts ────────────────────────────────────────────────────
    @mcp.tool()
    def detect_dose_conflict(drug_name: str, prescribed_dose: str, role: str = "discharge_coordinator", caller_role: Optional[str] = None) -> dict[str, Any]:
        """Flag a mismatch between prescribed dose and formulary standard dose."""
        role = caller_role or role
        with MCPCallTimer("pharmacy", "detect_dose_conflict", role, None):
            return pharmacy.detect_dose_conflict(drug_name, prescribed_dose, role=role)

    @mcp.tool()
    def flag_controlled_substance(drug_name: str, role: str = "discharge_coordinator", caller_role: Optional[str] = None) -> dict[str, Any]:
        """Check and flag if a drug is a controlled substance."""
        role = caller_role or role
        with MCPCallTimer("pharmacy", "flag_controlled_substance", role, None):
            return pharmacy.flag_controlled_substance(drug_name, role=role)

    # ── Data Validation ───────────────────────────────────────────────────────
    @mcp.tool()
    def validate_drug_name(drug_name: str, role: str = "discharge_coordinator", caller_role: Optional[str] = None) -> dict[str, Any]:
        """Check if a drug name exists in the pharmacy formulary."""
        role = caller_role or role
        with MCPCallTimer("pharmacy", "validate_drug_name", role, None):
            return pharmacy.validate_drug_name(drug_name, role=role)

    return mcp


# ══════════════════════════════════════════════════════════════════════════════
# BILLING SERVER  (port 8003)
# ══════════════════════════════════════════════════════════════════════════════

def create_billing_mcp():
    FastMCP = _require_fastmcp()
    billing = get_billing_server()
    mcp = FastMCP("Billing-Server")

    # ── Core Billing ──────────────────────────────────────────────────────────
    @mcp.tool()
    def get_charges(ward: str, los_days: int, role: str = "discharge_coordinator", caller_role: Optional[str] = None) -> dict[str, Any]:
        """Get ward and lab charges based on ward type and length of stay."""
        role = caller_role or role
        with MCPCallTimer("billing", "get_charges", role, None):
            return billing.get_charges(ward=ward, los_days=los_days, role=role)

    @mcp.tool()
    def get_charges_by_icd(icd_codes: list[str], role: str = "discharge_coordinator", caller_role: Optional[str] = None) -> dict[str, Any]:
        """Map ICD-10 diagnosis codes to billing charges."""
        role = caller_role or role
        with MCPCallTimer("billing", "get_charges_by_icd", role, None):
            return billing.get_charges_by_icd(icd_codes, role=role)

    @mcp.tool()
    def get_total_cost(patient_id: str, role: str = "discharge_coordinator", caller_role: Optional[str] = None) -> dict[str, Any]:
        """Compute complete bill breakdown for a patient."""
        role = caller_role or role
        with MCPCallTimer("billing", "get_total_cost", role, patient_id):
            return billing.get_total_cost(patient_id, role=role)

    # ── Insurance ─────────────────────────────────────────────────────────────
    @mcp.tool()
    def get_insurance(patient_id: str, role: str = "discharge_coordinator", caller_role: Optional[str] = None) -> dict[str, Any]:
        """Get insurance details and coverage information for a patient."""
        role = caller_role or role
        with MCPCallTimer("billing", "get_insurance", role, patient_id):
            return billing.get_insurance(patient_id=patient_id, role=role)

    @mcp.tool()
    def calculate_insurance_coverage(patient_id: str, charges: dict[str, Any], role: str = "discharge_coordinator", caller_role: Optional[str] = None) -> dict[str, Any]:
        """Apply insurance logic to calculate covered amount and patient liability."""
        role = caller_role or role
        with MCPCallTimer("billing", "calculate_insurance_coverage", role, patient_id):
            return billing.calculate_insurance_coverage(patient_id, charges, role=role)

    @mcp.tool()
    def validate_insurance(patient_id: str, role: str = "discharge_coordinator", caller_role: Optional[str] = None) -> dict[str, Any]:
        """Check insurance coverage eligibility for a patient."""
        role = caller_role or role
        with MCPCallTimer("billing", "validate_insurance", role, patient_id):
            return billing.validate_insurance(patient_id, role=role)

    # ── Payment Handling ──────────────────────────────────────────────────────
    @mcp.tool()
    def generate_payment_link(patient_id: str, role: str = "discharge_coordinator", caller_role: Optional[str] = None) -> dict[str, Any]:
        """Generate a payment request link for the patient."""
        role = caller_role or role
        with MCPCallTimer("billing", "generate_payment_link", role, patient_id):
            return billing.generate_payment_link(patient_id, role=role)

    @mcp.tool()
    def mark_invoice_paid(patient_id: str, role: str = "discharge_coordinator", caller_role: Optional[str] = None) -> dict[str, Any]:
        """Mark a patient's invoice as paid."""
        role = caller_role or role
        with MCPCallTimer("billing", "mark_invoice_paid", role, patient_id):
            return billing.mark_invoice_paid(patient_id, role=role)

    # ── Billing Validation ────────────────────────────────────────────────────
    @mcp.tool()
    def validate_billing_data(billing_safe_data: dict[str, Any], role: str = "discharge_coordinator", caller_role: Optional[str] = None) -> dict[str, Any]:
        """Validate billing payload to ensure no PHI leakage."""
        role = caller_role or role
        with MCPCallTimer("billing", "validate_billing_data", role, None):
            return billing.validate_billing_data(billing_safe_data, role=role)

    @mcp.tool()
    def audit_invoice(patient_id: str, role: str = "discharge_coordinator", caller_role: Optional[str] = None) -> dict[str, Any]:
        """Generate compliance audit trail for a patient invoice."""
        role = caller_role or role
        with MCPCallTimer("billing", "audit_invoice", role, patient_id):
            return billing.audit_invoice(patient_id, role=role)

    # ── Invoice Generation ────────────────────────────────────────────────────
    @mcp.tool()
    def generate_invoice(patient_id: str, billing_safe_ehr: dict[str, Any], drug_charges: list[dict[str, Any]], role: str = "discharge_coordinator", caller_role: Optional[str] = None) -> dict[str, Any]:
        """Create final patient invoice using PHI-safe EHR summary and drug costs."""
        role = caller_role or role
        with MCPCallTimer("billing", "generate_invoice", role, patient_id):
            return billing.generate_invoice(
                patient_id=patient_id,
                billing_safe_ehr=billing_safe_ehr,
                drug_charges=drug_charges,
                role=role,
            )

    return mcp


# ══════════════════════════════════════════════════════════════════════════════
# SECURITY SERVER  (port 8004)
# ══════════════════════════════════════════════════════════════════════════════

def create_security_mcp():
    FastMCP = _require_fastmcp()
    security = get_security_server()
    mcp = FastMCP("Security-Server")

    @mcp.tool()
    def check_access(role: str, server: str, tool: str) -> dict[str, Any]:
        """Check whether a role is permitted to call a tool on a server."""
        with MCPCallTimer("security", "check_access", role, None):
            return security.check_access(role, server, tool)

    @mcp.tool()
    def get_role_permissions(role: str) -> dict[str, Any]:
        """Return all permissions and callable tools for a given role."""
        with MCPCallTimer("security", "get_role_permissions", role, None):
            return security.get_role_permissions(role)

    @mcp.tool()
    def log_rbac_violation(role: str, tool: str, server: str, patient_id: Optional[str] = None) -> dict[str, Any]:
        """Manually log an RBAC violation for auditing or testing."""
        with MCPCallTimer("security", "log_rbac_violation", role, patient_id):
            return security.log_rbac_violation(role, tool, server, patient_id)

    @mcp.tool()
    def get_access_logs(limit: int = 50) -> dict[str, Any]:
        """Retrieve recent RBAC violation audit logs."""
        with MCPCallTimer("security", "get_access_logs", "system", None):
            return security.get_access_logs(limit=limit)

    return mcp


# ══════════════════════════════════════════════════════════════════════════════
# TELEMETRY SERVER  (port 8005)
# ══════════════════════════════════════════════════════════════════════════════

def create_telemetry_mcp():
    FastMCP = _require_fastmcp()
    telem = get_telemetry_server()
    mcp = FastMCP("Telemetry-Server")

    @mcp.tool()
    def get_mcp_call_count(patient_id: Optional[str] = None) -> dict[str, Any]:
        """Return total MCP tool call counts, optionally filtered by patient."""
        with MCPCallTimer("telemetry", "get_mcp_call_count", "system", patient_id):
            return telem.get_mcp_call_count(patient_id=patient_id)

    @mcp.tool()
    def get_alerts(patient_id: Optional[str] = None, level: Optional[str] = None) -> dict[str, Any]:
        """Return system alerts, optionally filtered by patient or severity level."""
        with MCPCallTimer("telemetry", "get_alerts", "system", patient_id):
            return telem.get_alerts(patient_id=patient_id, level=level)

    @mcp.tool()
    def get_system_health() -> dict[str, Any]:
        """Return health status of all MCP servers and aggregate metrics."""
        with MCPCallTimer("telemetry", "get_system_health", "system", None):
            return telem.get_system_health()

    @mcp.tool()
    def get_summary() -> dict[str, Any]:
        """Return raw telemetry summary (counts, rates, averages)."""
        with MCPCallTimer("telemetry", "get_summary", "system", None):
            return telem.get_summary()

    @mcp.tool()
    def get_recent_calls(limit: int = 100) -> dict[str, Any]:
        """Return recent tool calls, RBAC violations, and alerts for the UI."""
        with MCPCallTimer("telemetry", "get_recent_calls", "system", None):
            return telem.get_recent_calls(limit=limit)

    @mcp.tool()
    def trace_workflow(patient_id: str) -> dict[str, Any]:
        """Return the full ordered MCP tool call trace for a patient's workflow."""
        with MCPCallTimer("telemetry", "trace_workflow", "system", patient_id):
            return telem.trace_workflow(patient_id)

    return mcp


# ══════════════════════════════════════════════════════════════════════════════
# Launch helpers
# ══════════════════════════════════════════════════════════════════════════════

_SERVERS = {
    "ehr":       (create_ehr_mcp,       8001),
    "pharmacy":  (create_pharmacy_mcp,  8002),
    "billing":   (create_billing_mcp,   8003),
    "security":  (create_security_mcp,  8004),
    "telemetry": (create_telemetry_mcp, 8005),
}


def run_one(server: str):
    server = server.lower().strip()
    if server not in _SERVERS:
        raise SystemExit(f"Unknown server '{server}'. Choose from: {', '.join(_SERVERS)}")
    factory, port = _SERVERS[server]
    app = factory()
    logger.info("Starting %s MCP server on port %s", server, port)
    app.run(transport="sse", host="0.0.0.0", port=port)


def run_all():
    threads: list[threading.Thread] = []

    def spawn(name: str, port: int, factory):
        app = factory()
        t = threading.Thread(
            target=app.run,
            kwargs={"transport": "sse", "host": "0.0.0.0", "port": port},
            daemon=True,
        )
        t.start()
        logger.info("Started %s MCP server: http://localhost:%s", name, port)
        threads.append(t)

    for name, (factory, port) in _SERVERS.items():
        spawn(name, port, factory)

    logger.info("All 5 servers running. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("Shutting down.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", choices=list(_SERVERS.keys()))
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    get_telemetry()

    if args.all:
        run_all()
        return
    if args.server:
        run_one(args.server)
        return
    parser.print_help()


if __name__ == "__main__":
    main()
