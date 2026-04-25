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

# PHI fields that must be stripped before sharing across servers
PHI_FIELDS = [
    "name",
    "dob", 
    "mrn",
    "discharge_note",
    "attending_physician",
    "patient_name",
    "date_of_birth",
    "medical_record_number",
    "ssn",
    "address",
    "phone",
    "email",
]

# Tool to permission mapping
TOOL_PERMISSIONS = {
    # EHR Tools
    "get_patient_discharge_summary": "read_discharge_note",
    "get_discharge_medications": "read_medications",
    "get_diagnosis_codes": "read_diagnosis_codes",
    "get_admission_info": "read_admission_dates",
    "get_billing_safe_summary": "read_diagnosis_codes",
    "get_patient_demographics": "read_patient_demographics",
    # Pharmacy Tools
    "check_stock": "check_stock",
    "get_alternative": "get_alternatives",
    "get_price": "get_drug_price",
    "dispense_request": "submit_dispense_request",
    # Billing Tools
    "get_charges": "read_charge_codes",
    "get_insurance": "read_insurance_contract",
    "generate_invoice": "generate_invoice",
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
        return {
            "discharge_coordinator": {
                "ehr": ["read_discharge_note", "read_medications", "read_diagnosis_codes",
                        "read_patient_demographics", "read_admission_dates"],
                "pharmacy": ["check_stock", "get_alternatives", "get_drug_price", 
                            "submit_dispense_request"],
                "billing": ["read_charge_codes", "generate_invoice", "read_insurance_contract"]
            },
            "billing_agent": {
                "ehr": ["read_diagnosis_codes", "read_admission_dates", "read_ward"],
                "pharmacy": ["read_drug_price"],
                "billing": ["read_charge_codes", "generate_invoice", "read_insurance_contract",
                           "submit_claim"]
            },
            "pharmacy_agent": {
                "ehr": ["read_medications", "read_diagnosis_codes"],
                "pharmacy": ["check_stock", "get_alternatives", "get_drug_price",
                            "submit_dispense_request", "update_inventory"],
                "billing": []
            },
            "clinical_agent": {
                "ehr": ["read_discharge_note", "read_medications", "read_diagnosis_codes",
                        "read_patient_demographics", "read_admission_dates", "update_discharge_note"],
                "pharmacy": ["check_stock"],
                "billing": []
            }
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
