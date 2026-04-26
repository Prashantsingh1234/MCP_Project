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
        self._payments: List[dict] = []
        logger.info("Billing Server initialized")
    
    # ========== Core Billing Methods ==========
    
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
        insurer_id = patient_ins.get("insurer_id")
        if not insurer_id:
            return {
                "patient_id": patient_id,
                "has_insurance": True,
                "insurer_id": None,
                "policy_number": patient_ins.get("policy_number"),
                "message": "No insurer ID on file"
            }
        
        insurer = self.data_loader.get_insurance(insurer_id)
        
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
                        # Never include raw payload values in exceptions/logs.
                        sample = {"keys": list(value.keys())[:3]}
                        raise PHIBoundaryViolationError(field, source, sample)
                for v in value.values():
                    walk(v)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        walk(data)
    
    # ========== Additional Core Billing Tools ==========
    
    def get_total_cost(self, patient_id: str, role: str = "discharge_coordinator") -> dict[str, Any]:
        """Compute total bill for a patient.
        
        Args:
            patient_id: Patient identifier.
            role: Role making the request.
            
        Returns:
            Total cost breakdown.
        """
        self.rbac.check_permission(role, "billing", "generate_invoice")
        
        # Get billing safe summary from EHR
        from src.servers.ehr_server import get_ehr_server
        ehr = get_ehr_server()
        
        try:
            billing_safe = ehr.get_billing_safe_summary(patient_id, role)
        except Exception:
            billing_safe = {"ward": "General", "los_days": 1, "diagnosis_icd10": []}
        
        ward = billing_safe.get("ward", "General")
        los_days = billing_safe.get("los_days", 1)
        
        # Get charges
        charges = self.get_charges(ward, los_days, role)
        
        # Get insurance
        insurance = self.get_insurance(patient_id, role)
        
        # Calculate totals
        subtotal = charges.get("subtotal", 0)
        
        # Apply insurance
        covered_amount = 0
        patient_responsibility = subtotal
        
        if insurance.get("has_insurance"):
            icd_codes = billing_safe.get("diagnosis_icd10", [])
            primary_icd = icd_codes[0] if icd_codes else ""
            prefix = primary_icd[0] if primary_icd else ""
            
            if prefix in insurance.get("covered_icd10_prefixes", []):
                max_covered = insurance.get("max_covered_per_admission_inr", 0)
                covered_amount = min(subtotal, max_covered)
                patient_responsibility = subtotal - covered_amount
            
            deductible = insurance.get("deductible_inr", 0)
            copay = insurance.get("copay_inr", 0)
            patient_responsibility = max(0, patient_responsibility - deductible) + copay
        
        return {
            "patient_id": patient_id,
            "ward": ward,
            "los_days": los_days,
            "ward_charges": charges.get("ward_total", 0),
            "lab_charges": charges.get("lab_total", 0),
            "subtotal": subtotal,
            "insurance_covered": covered_amount,
            "patient_responsibility": patient_responsibility,
            "currency": "INR"
        }
    
    def calculate_insurance_coverage(self, patient_id: str, charges: dict[str, Any], 
                                     role: str = "discharge_coordinator") -> dict[str, Any]:
        """Apply insurance logic to calculate coverage.
        
        Args:
            patient_id: Patient identifier.
            charges: Charge breakdown dict.
            role: Role making the request.
            
        Returns:
            Insurance coverage calculation.
        """
        self.rbac.check_permission(role, "billing", "get_insurance")
        
        insurance = self.get_insurance(patient_id, role)
        
        if not insurance.get("has_insurance"):
            return {
                "patient_id": patient_id,
                "has_insurance": False,
                "covered_amount": 0,
                "patient_responsibility": charges.get("subtotal", 0)
            }
        
        subtotal = charges.get("subtotal", 0)
        icd_codes = charges.get("diagnosis_icd10", [])
        
        # Check ICD coverage
        primary_icd = icd_codes[0] if icd_codes else ""
        prefix = primary_icd[0] if primary_icd else ""
        
        covered = False
        if prefix in insurance.get("covered_icd10_prefixes", []):
            covered = True
        
        max_covered = insurance.get("max_covered_per_admission_inr", 0)
        covered_amount = min(subtotal, max_covered) if covered else 0
        
        # Apply deductible and copay
        deductible = insurance.get("deductible_inr", 0)
        copay = insurance.get("copay_inr", 0)
        
        patient_responsibility = subtotal - covered_amount
        patient_responsibility = max(0, patient_responsibility - deductible) + copay
        
        return {
            "patient_id": patient_id,
            "has_insurance": True,
            "insurer_name": insurance.get("insurer_name"),
            "plan_type": insurance.get("plan_type"),
            "icd_covered": covered,
            "covered_amount": covered_amount,
            "deductible": deductible,
            "copay": copay,
            "patient_responsibility": patient_responsibility,
            "subtotal": subtotal
        }
    
    def validate_insurance(self, patient_id: str, role: str = "discharge_coordinator") -> dict[str, Any]:
        """Check coverage eligibility.
        
        Args:
            patient_id: Patient identifier.
            role: Role making the request.
            
        Returns:
            Insurance validation result.
        """
        self.rbac.check_permission(role, "billing", "get_insurance")
        
        insurance = self.get_insurance(patient_id, role)
        
        if not insurance.get("has_insurance"):
            return {
                "patient_id": patient_id,
                "eligible": False,
                "reason": "No insurance on file"
            }
        
        # Check if insurer is valid
        insurer_name = insurance.get("insurer_name")
        
        return {
            "patient_id": patient_id,
            "eligible": insurer_name is not None,
            "insurer_name": insurer_name,
            "plan_type": insurance.get("plan_type"),
            "policy_number": insurance.get("policy_number"),
            "pa_required": insurance.get("pa_required", False)
        }
    
    # ========== Payment Handling Tools ==========
    
    def generate_payment_link(self, patient_id: str, role: str = "discharge_coordinator") -> dict[str, Any]:
        """Create payment request link.
        
        Args:
            patient_id: Patient identifier.
            role: Role making the request.
            
        Returns:
            Payment link details.
        """
        self.rbac.check_permission(role, "billing", "generate_invoice")
        
        # Get patient's latest invoice
        invoices = self.get_patient_invoices(patient_id, role)
        
        if not invoices:
            return {
                "patient_id": patient_id,
                "success": False,
                "message": "No invoice found for patient"
            }
        
        latest_invoice = invoices[-1]
        amount = latest_invoice.get("patient_responsibility", 0)
        
        # Generate payment link (simulated)
        payment_id = f"PAY-{patient_id}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        
        payment = {
            "payment_id": payment_id,
            "patient_id": patient_id,
            "invoice_id": latest_invoice.get("invoice_id"),
            "amount_inr": amount,
            "status": "PENDING",
            "created_at": datetime.utcnow().isoformat(),
            "payment_link": f"https://payments.hospital.example.com/pay/{payment_id}",
            "qr_code": f"PAY:{payment_id}:{amount}"
        }
        
        self._payments.append(payment)
        
        return payment
    
    def mark_invoice_paid(self, patient_id: str, role: str = "discharge_coordinator") -> dict[str, Any]:
        """Update payment status to paid.
        
        Args:
            patient_id: Patient identifier.
            role: Role making the request.
            
        Returns:
            Payment confirmation.
        """
        self.rbac.check_permission(role, "billing", "generate_invoice")
        
        # Get patient's latest invoice
        invoices = self.get_patient_invoices(patient_id, role)
        
        if not invoices:
            return {
                "patient_id": patient_id,
                "success": False,
                "message": "No invoice found"
            }
        
        latest_invoice = invoices[-1]
        latest_invoice["status"] = "PAID"
        latest_invoice["paid_at"] = datetime.utcnow().isoformat()
        
        # Update payment record
        for payment in self._payments:
            if payment.get("patient_id") == patient_id and payment.get("status") == "PENDING":
                payment["status"] = "COMPLETED"
                payment["completed_at"] = datetime.utcnow().isoformat()
        
        self.telemetry.record_alert("INFO", "Billing", f"Invoice paid for patient {patient_id}")
        
        return {
            "patient_id": patient_id,
            "invoice_id": latest_invoice.get("invoice_id"),
            "status": "PAID",
            "paid_at": latest_invoice.get("paid_at")
        }
    
    # ========== Billing Validation Tools ==========
    
    def validate_billing_data(self, billing_safe_data: dict[str, Any], role: str = "discharge_coordinator") -> dict[str, Any]:
        """Ensure no PHI leakage in billing data.
        
        Args:
            billing_safe_data: Data to validate.
            role: Role making the request.
            
        Returns:
            Validation result.
        """
        self.rbac.check_permission(role, "billing", "generate_invoice")
        
        try:
            self._validate_no_phi_recursive(billing_safe_data, "billing_safe_data")
            return {
                "valid": True,
                "message": "No PHI detected in billing data"
            }
        except PHIBoundaryViolationError as e:
            return {
                "valid": False,
                "message": f"PHI detected: {e.field}",
                "phi_field": e.field
            }
    
    def audit_invoice(self, patient_id: str, role: str = "discharge_coordinator") -> dict[str, Any]:
        """Audit trail for compliance.
        
        Args:
            patient_id: Patient identifier.
            role: Role making the request.
            
        Returns:
            Audit trail.
        """
        self.rbac.check_permission(role, "billing", "generate_invoice")
        
        invoices = self.get_patient_invoices(patient_id, role)
        
        audit_trail = []
        for inv in invoices:
            audit_trail.append({
                "invoice_id": inv.get("invoice_id"),
                "generated_at": inv.get("generated_at"),
                "status": inv.get("status"),
                "amount": inv.get("patient_responsibility"),
                "paid_at": inv.get("paid_at")
            })
        
        return {
            "patient_id": patient_id,
            "invoice_count": len(audit_trail),
            "audit_trail": audit_trail
        }
    
    # ========== Additional Billing Tools ==========
    
    def get_charges_by_icd(self, icd_codes: List[str], role: str = "discharge_coordinator") -> dict[str, Any]:
        """Map diagnosis to billing codes.
        
        Args:
            icd_codes: List of ICD-10 codes.
            role: Role making the request.
            
        Returns:
            Charge mapping by diagnosis.
        """
        self.rbac.check_permission(role, "billing", "get_charges")
        
        # Map ICD prefixes to charge codes
        icd_charge_map = {
            "I": {"code": "CARD-PROCS", "description": "Cardiology procedures"},
            "N": {"code": "NEPH-PROCS", "description": "Nephrology procedures"},
            "M": {"code": "RHEU-PROCS", "description": "Rheumatology procedures"},
            "C": {"code": "ONCO-PROCS", "description": "Oncology procedures"},
            "G": {"code": "NEUR-PROCS", "description": "Neurology procedures"},
        }
        
        charges = []
        for icd in icd_codes:
            prefix = icd[0] if icd else ""
            charge_info = icd_charge_map.get(prefix, {"code": "GEN-PROCS", "description": "General procedures"})
            charge = self.data_loader.get_charge(charge_info["code"])
            if charge:
                charges.append({
                    "icd_code": icd,
                    "charge_code": charge.get("charge_code"),
                    "description": charge.get("description"),
                    "rate_inr": charge.get("rate_inr")
                })
        
        return {
            "icd_codes": icd_codes,
            "charges": charges,
            "total_diagnostic_charges": sum(c.get("rate_inr", 0) for c in charges)
        }


# Global Billing Server instance
_billing_server: Optional[BillingServer] = None


def get_billing_server() -> BillingServer:
    """Get the global Billing Server instance."""
    global _billing_server
    if _billing_server is None:
        _billing_server = BillingServer()
    return _billing_server
