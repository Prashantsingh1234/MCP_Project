"""Billing Server for MCPDischarge.

Provides billing, insurance, and invoice generation with PHI protection.
"""

import logging
from typing import Optional, Any, List
from datetime import datetime

from src.utils.data_loader import get_data_loader
from src.utils.rbac import get_rbac_engine, PHI_FIELDS
from src.utils.telemetry import get_telemetry
from src.utils.exceptions import RBACError, PHIBoundaryViolationError

logger = logging.getLogger(__name__)


class BillingServer:
    """Billing Server for charges, insurance, and invoices."""
    
    def __init__(self):
        """Initialize Billing Server."""
        self.data_loader = get_data_loader()
        self.rbac = get_rbac_engine()
        self.telemetry = get_telemetry()
        self._invoices: List[dict] = []
        logger.info("Billing Server initialized")
    
    def get_charges(self, ward: str, los_days: int, 
                   role: str = "discharge_coordinator") -> dict[str, Any]:
        """Get charges for a patient stay.
        
        Args:
            ward: Patient ward.
            los_days: Length of stay in days.
            role: Role making the request.
            
        Returns:
            Charge breakdown.
            
        Raises:
            RBACError: If role lacks permission.
        """
        self.rbac.check_permission(role, "billing", "get_charges")
        
        # Get ward per diem
        ward_charge = self._get_ward_charge(ward)
        
        # Calculate charges
        ward_total = ward_charge.get("rate_inr", 0) * los_days
        
        # Add standard charges
        lab_charge = self._get_charge("INV-LAB")
        lab_total = lab_charge.get("rate_inr", 0) * los_days
        
        return {
            "ward": ward,
            "los_days": los_days,
            "ward_charge_code": ward_charge.get("charge_code"),
            "ward_rate_per_day": ward_charge.get("rate_inr"),
            "ward_total": ward_total,
            "lab_charge_code": lab_charge.get("charge_code"),
            "lab_rate_per_day": lab_charge.get("rate_inr"),
            "lab_total": lab_total,
            "subtotal": ward_total + lab_total
        }
    
    def get_insurance(self, patient_id: str, 
                     role: str = "discharge_coordinator") -> dict[str, Any]:
        """Get insurance information for a patient.
        
        Args:
            patient_id: Patient identifier.
            role: Role making the request.
            
        Returns:
            Insurance information.
            
        Raises:
            RBACError: If role lacks permission.
        """
        self.rbac.check_permission(role, "billing", "get_insurance")
        
        # Get patient insurance mapping
        patient_ins = self.data_loader.get_patient_insurance(patient_id)
        
        if not patient_ins:
            return {
                "patient_id": patient_id,
                "has_insurance": False,
                "message": "No insurance on file"
            }
        
        # Get insurance contract
        insurer = self.data_loader.get_insurance(patient_ins.get("insurer_id"))
        
        if not insurer:
            return {
                "patient_id": patient_id,
                "has_insurance": True,
                "insurer_id": patient_ins.get("insurer_id"),
                "policy_number": patient_ins.get("policy_number"),
                "message": "Insurance contract not found"
            }
        
        return {
            "patient_id": patient_id,
            "has_insurance": True,
            "insurer_id": insurer.get("insurer_id"),
            "insurer_name": insurer.get("insurer_name"),
            "plan_type": insurer.get("plan_type"),
            "policy_number": patient_ins.get("policy_number"),
            "pa_required": patient_ins.get("pa_required"),
            "copay_inr": insurer.get("copay_inr"),
            "deductible_inr": insurer.get("deductible_inr"),
            "max_covered_per_admission_inr": insurer.get("max_covered_per_admission_inr"),
            "covered_icd10_prefixes": insurer.get("covered_icd10_prefixes"),
            "specialty_drug_covered": insurer.get("specialty_drug_covered"),
            "specialty_drug_copay_pct": insurer.get("specialty_drug_copay_pct")
        }
    
    def generate_invoice(self, patient_id: str, 
                        billing_safe_ehr: dict[str, Any],
                        drug_charges: List[dict[str, Any]],
                        role: str = "discharge_coordinator") -> dict[str, Any]:
        """Generate patient invoice.
        
        Args:
            patient_id: Patient identifier.
            billing_safe_ehr: Billing-safe EHR data (PHI stripped).
            drug_charges: List of drug charges from pharmacy.
            role: Role making the request.
            
        Returns:
            Generated invoice.
            
        Raises:
            RBACError: If role lacks permission.
            PHIBoundaryViolationError: If PHI detected in payload.
        """
        self.rbac.check_permission(role, "billing", "generate_invoice")
        
        # Validate no PHI in billing_safe_ehr
        self._validate_no_phi_recursive(billing_safe_ehr, "billing_safe_ehr")
        self._validate_no_phi_recursive({"drug_charges": drug_charges}, "drug_charges")
        
        # Extract billing info from EHR
        ward = billing_safe_ehr.get("ward", "General")
        los_days = billing_safe_ehr.get("los_days", 1)
        icd_codes = billing_safe_ehr.get("diagnosis_icd10", [])
        
        # Get base charges
        charges = self.get_charges(ward, los_days, role)
        
        # Calculate drug charges
        drug_subtotal = sum(d.get("total_price_inr", 0) for d in drug_charges)
        drug_fee = sum(d.get("dispensing_fee", 0) for d in drug_charges)
        
        # Get insurance
        insurance = self.get_insurance(patient_id, role)
        
        # Calculate totals
        subtotal = charges["subtotal"] + drug_subtotal + drug_fee
        
        # Apply insurance
        covered_amount = 0
        patient_responsibility = subtotal
        
        if insurance.get("has_insurance"):
            # Check if ICD codes are covered
            primary_icd = icd_codes[0] if icd_codes else ""
            prefix = primary_icd[0] if primary_icd else ""
            
            if prefix in insurance.get("covered_icd10_prefixes", []):
                max_covered = insurance.get("max_covered_per_admission_inr", 0)
                covered_amount = min(subtotal, max_covered)
                patient_responsibility = subtotal - covered_amount
            
            # Apply deductible and copay
            deductible = insurance.get("deductible_inr", 0)
            copay = insurance.get("copay_inr", 0)
            
            patient_responsibility = max(0, patient_responsibility - deductible) + copay
        
        # Create invoice
        invoice = {
            "invoice_id": f"INV-{patient_id}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
            "patient_id": patient_id,
            "generated_at": datetime.utcnow().isoformat(),
            "ward": ward,
            "los_days": los_days,
            "diagnosis_icd10": icd_codes,
            "charges": {
                "ward": charges["ward_total"],
                "lab": charges["lab_total"],
                "drugs": drug_subtotal,
                "dispensing_fees": drug_fee
            },
            "subtotal": subtotal,
            "subtotal_inr": subtotal,
            "insurance": {
                "applied": insurance.get("has_insurance", False),
                "insurer_name": insurance.get("insurer_name"),
                "covered_amount": covered_amount
            },
            "patient_responsibility": patient_responsibility,
            "patient_liability_inr": patient_responsibility,
            "status": "PENDING_PAYMENT"
        }
        
        self._invoices.append(invoice)
        
        self.telemetry.record_alert(
            "INFO",
            "Billing",
            f"Invoice generated for patient {patient_id}",
            {"invoice_id": invoice["invoice_id"], "amount": patient_responsibility}
        )
        
        return invoice
    
    def get_invoice(self, invoice_id: str, 
                   role: str = "discharge_coordinator") -> Optional[dict]:
        """Get a specific invoice.
        
        Args:
            invoice_id: Invoice identifier.
            role: Role making the request.
            
        Returns:
            Invoice data or None.
        """
        self.rbac.check_permission(role, "billing", "generate_invoice")
        
        for inv in self._invoices:
            if inv.get("invoice_id") == invoice_id:
                return inv
        return None
    
    def get_patient_invoices(self, patient_id: str,
                            role: str = "discharge_coordinator") -> List[dict]:
        """Get all invoices for a patient.
        
        Args:
            patient_id: Patient identifier.
            role: Role making the request.
            
        Returns:
            List of patient invoices.
        """
        self.rbac.check_permission(role, "billing", "generate_invoice")
        
        return [inv for inv in self._invoices if inv.get("patient_id") == patient_id]
    
    def _get_ward_charge(self, ward: str) -> dict:
        """Get ward charge rate."""
        ward_code_map = {
            "Cardiology": "WRD-CARD",
            "Nephrology": "WRD-NEPH",
            "Rheumatology": "WRD-RHEU",
            "Oncology": "WRD-ONCO",
            "Neurology": "WRD-NEUR"
        }
        
        charge_code = ward_code_map.get(ward, "WRD-CARD")
        return self.data_loader.get_charge(charge_code) or {
            "charge_code": charge_code,
            "rate_inr": 3000,
            "description": "General ward"
        }
    
    def _get_charge(self, charge_code: str) -> dict:
        """Get charge by code."""
        return self.data_loader.get_charge(charge_code) or {
            "charge_code": charge_code,
            "rate_inr": 0
        }
    
    def _validate_no_phi_recursive(self, data: Any, source: str):
        """Reject payloads containing PHI keys anywhere (defense-in-depth)."""

        def walk(value: Any):
            if isinstance(value, dict):
                keys_lower = {k.lower() for k in value.keys()}
                for field in PHI_FIELDS:
                    if field.lower() in keys_lower:
                        sample = {k: value[k] for k in list(value.keys())[:3]}
                        raise PHIBoundaryViolationError(field, source, sample)
                for v in value.values():
                    walk(v)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        walk(data)


# Global Billing Server instance
_billing_server: Optional[BillingServer] = None


def get_billing_server() -> BillingServer:
    """Get the global Billing Server instance."""
    global _billing_server
    if _billing_server is None:
        _billing_server = BillingServer()
    return _billing_server
