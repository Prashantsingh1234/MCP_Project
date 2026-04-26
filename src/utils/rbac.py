"""RBAC Engine for MCPDischarge.

Enforces role-based access control across EHR, Pharmacy, and Billing servers.
"""

import json
import logging
from pathlib import Path
from typing import Optional
from datetime import datetime

from src.utils.exceptions import RBACError
from src.utils.telemetry import get_telemetry

logger = logging.getLogger(__name__)

# PHI fields that must be stripped before sharing across servers.
# README contract: billing must never receive these fields.
PHI_FIELDS = ["name", "dob", "mrn", "discharge_note", "attending_physician"]

# Tool to permission mapping — covers all tools across all 5 servers.
TOOL_PERMISSIONS = {
    # ── EHR: Core Clinical ──
    "get_patient_discharge_summary": "read_discharge_note",
    "get_discharge_medications": "read_medications",
    "get_diagnosis_codes": "read_diagnosis_codes",
    "get_admission_info": "read_admission_dates",
    "list_patients": "read_admission_dates",
    "get_billing_safe_summary": "read_diagnosis_codes",
    "get_patient_demographics": "read_patient_demographics",
    "get_patient_history": "read_discharge_note",
    # ── EHR: Clinical Validation ──
    "validate_prescription": "validate_clinical",
    "check_drug_interactions": "validate_clinical",
    "check_dose_validity": "validate_clinical",
    # ── EHR: Update / Workflow ──
    "update_prescription": "update_discharge_note",
    "mark_patient_ready_for_discharge": "update_discharge_note",
    # ── EHR: Edge Cases ──
    "mark_urgent_request": "read_discharge_note",
    "escalate_to_doctor": "read_discharge_note",
    "request_represcription": "read_medications",
    # ── EHR: Data Validation ──
    "validate_patient_id": "read_diagnosis_codes",
    # ── EHR: Notifications ──
    "notify_patient": "send_notification",
    "notify_doctor": "send_notification",
    # ── Pharmacy: Stock ──
    "check_stock": "check_stock",
    "check_bulk_stock": "check_stock",
    "list_in_stock_drugs": "check_stock",
    # ── Pharmacy: Alternatives ──
    "get_alternative": "get_alternatives",
    "get_all_alternatives": "get_alternatives",
    "check_therapeutic_equivalence": "get_alternatives",
    # ── Pharmacy: Drug Matching ──
    "resolve_drug_name_alias": "check_stock",
    "semantic_drug_search": "check_stock",
    # ── Pharmacy: Pricing ──
    "get_price": "get_drug_price",
    "get_bulk_price": "get_drug_price",
    # ── Pharmacy: Dispensing ──
    "dispense_request": "submit_dispense_request",
    "create_dispense_request": "submit_dispense_request",
    "confirm_dispense": "submit_dispense_request",
    "get_dispense_history": "check_stock",
    # ── Pharmacy: Inventory ──
    "update_stock": "update_inventory",
    "check_nearby_pharmacy_availability": "get_alternatives",
    # ── Pharmacy: Alerts ──
    "detect_dose_conflict": "check_stock",
    "flag_controlled_substance": "check_stock",
    # ── Pharmacy: Data Validation ──
    "validate_drug_name": "validate_drug",
    # ── Billing: Core ──
    "get_charges": "read_charge_codes",
    "get_charges_by_icd": "read_charge_codes",
    "get_total_cost": "read_charge_codes",
    # ── Billing: Insurance ──
    "get_insurance": "read_insurance_contract",
    "calculate_insurance_coverage": "read_insurance_contract",
    "validate_insurance": "read_insurance_contract",
    # ── Billing: Payment ──
    "generate_payment_link": "manage_payment",
    "mark_invoice_paid": "manage_payment",
    # ── Billing: Validation ──
    "validate_billing_data": "audit_billing",
    "audit_invoice": "audit_billing",
    # ── Billing: Invoice ──
    "generate_invoice": "generate_invoice",
    # ── Security (RBAC) ──
    "check_access": "read_access_control",
    "get_role_permissions": "read_access_control",
    "log_rbac_violation": "read_access_control",
    "get_access_logs": "read_access_control",
    # ── Telemetry / Observability ──
    "get_mcp_call_count": "read_telemetry",
    "get_alerts": "read_telemetry",
    "get_system_health": "read_telemetry",
    "trace_workflow": "read_telemetry",
}


class RBACEngine:
    """Role-Based Access Control Engine for MCPDischarge."""
    
    def __init__(self, policies_path: Optional[Path] = None):
        """Initialize RBAC engine with policies.
        
        Args:
            policies_path: Path to RBAC policies JSON file.
        """
        self.policies_path = policies_path or Path(__file__).parent.parent.parent / "data" / "rbac_policies.json"
        self.policies = self._load_policies()
        self.violation_log: list[dict] = []
        logger.info(f"RBAC Engine initialized with {len(self.policies)} roles")
    
    def _load_policies(self) -> dict:
        """Load RBAC policies from JSON file."""
        try:
            with open(self.policies_path, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            logger.warning(f"RBAC policies not found at {self.policies_path}, using default policies")
            return self._default_policies()
    
    def _default_policies(self) -> dict:
        """Return default RBAC policies if file not found."""
        _security_ro = ["read_access_control"]
        _telemetry_ro = ["read_telemetry"]
        return {
            "discharge_coordinator": {
                "ehr": ["read_discharge_note", "read_medications", "read_diagnosis_codes",
                        "read_patient_demographics", "read_admission_dates",
                        "validate_clinical", "update_discharge_note", "send_notification"],
                "pharmacy": ["check_stock", "get_alternatives", "get_drug_price",
                             "submit_dispense_request", "update_inventory", "validate_drug"],
                "billing": ["read_charge_codes", "generate_invoice", "read_insurance_contract",
                            "submit_claim", "manage_payment", "audit_billing"],
                "security": _security_ro,
                "telemetry": _telemetry_ro,
            },
            "billing_agent": {
                "ehr": ["read_diagnosis_codes", "read_admission_dates"],
                "pharmacy": ["get_drug_price"],
                "billing": ["read_charge_codes", "generate_invoice", "read_insurance_contract",
                            "submit_claim", "manage_payment", "audit_billing"],
                "security": _security_ro,
                "telemetry": _telemetry_ro,
            },
            "pharmacy_agent": {
                "ehr": ["read_medications", "read_diagnosis_codes"],
                "pharmacy": ["check_stock", "get_alternatives", "get_drug_price",
                             "submit_dispense_request", "update_inventory", "validate_drug"],
                "billing": [],
                "security": _security_ro,
                "telemetry": _telemetry_ro,
            },
            "clinical_agent": {
                "ehr": ["read_discharge_note", "read_medications", "read_diagnosis_codes",
                        "read_patient_demographics", "read_admission_dates",
                        "validate_clinical", "update_discharge_note", "send_notification"],
                "pharmacy": ["check_stock", "get_alternatives", "get_drug_price", "validate_drug"],
                "billing": [],
                "security": _security_ro,
                "telemetry": _telemetry_ro,
            },
        }
    
    def check_permission(self, role: str, server: str, tool: str, 
                        patient_id: Optional[str] = None) -> bool:
        """Check if a role has permission to call a tool on a server.
        
        Args:
            role: The role making the request.
            server: The target server (ehr, pharmacy, billing).
            tool: The tool being called.
            patient_id: Optional patient ID for audit logging.
            
        Returns:
            True if permission granted.
            
        Raises:
            RBACError: If permission is denied.
        """
        # Map tool to permission
        permission = TOOL_PERMISSIONS.get(tool, tool)
        
        # Get role permissions
        role_perms = self.policies.get(role, {})
        server_perms = role_perms.get(server, [])
        
        if permission not in server_perms:
            self._log_violation(role, server, tool, patient_id)
            get_telemetry().record_rbac_violation(role, server, tool, patient_id)
            raise RBACError(
                f"Role '{role}' cannot call '{tool}' on '{server}' server. "
                f"Required permission: '{permission}'",
                role=role,
                tool=tool,
                server=server
            )
        
        logger.debug(f"RBAC check passed: {role} -> {server}.{tool}")
        return True
    
    def _log_violation(self, role: str, server: str, tool: str, 
                      patient_id: Optional[str] = None):
        """Log an RBAC violation."""
        violation = {
            "timestamp": datetime.utcnow().isoformat(),
            "role": role,
            "server": server,
            "tool": tool,
            "patient_id": patient_id,
            "action": "DENIED"
        }
        self.violation_log.append(violation)
        logger.warning(f"RBAC VIOLATION: {violation}")
    
    def get_violations(self) -> list[dict]:
        """Get all logged RBAC violations."""
        return self.violation_log.copy()
    
    def clear_violations(self):
        """Clear the violation log."""
        self.violation_log.clear()
    
    @staticmethod
    def strip_phi(data: dict) -> dict:
        """Strip PHI fields from a dictionary.
        
        Args:
            data: Input dictionary potentially containing PHI.
            
        Returns:
            Dictionary with PHI fields removed.
        """
        return {k: v for k, v in data.items() if k.lower() not in [f.lower() for f in PHI_FIELDS]}
    
    @staticmethod
    def validate_no_phi(data: dict, server: str) -> bool:
        """Validate that no PHI fields are present in data.
        
        Args:
            data: Dictionary to validate.
            server: The server receiving the data.
            
        Returns:
            True if no PHI detected.
            
        Raises:
            RBACError: If PHI field detected.
        """
        for field in PHI_FIELDS:
            if field.lower() in [k.lower() for k in data.keys()]:
                raise RBACError(
                    f"PHI field '{field}' detected in {server} payload",
                    server=server,
                    tool="validate_no_phi"
                )
        return True
    
    def get_allowed_tools(self, role: str, server: str) -> list[str]:
        """Get list of tools a role can access on a server.
        
        Args:
            role: The role to check.
            server: The target server.
            
        Returns:
            List of allowed tool names.
        """
        role_perms = self.policies.get(role, {})
        server_perms = role_perms.get(server, [])
        
        # Reverse map permissions to tools
        allowed = []
        for tool, perm in TOOL_PERMISSIONS.items():
            if perm in server_perms:
                allowed.append(tool)
        return allowed


# Global RBAC engine instance
_rbac_engine: Optional[RBACEngine] = None


def get_rbac_engine() -> RBACEngine:
    """Get the global RBAC engine instance."""
    global _rbac_engine
    if _rbac_engine is None:
        _rbac_engine = RBACEngine()
    return _rbac_engine
