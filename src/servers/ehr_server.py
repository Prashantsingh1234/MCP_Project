"""EHR Server for MCPDischarge.

Provides patient data access with PHI protection and RBAC enforcement.
"""

import logging
from typing import Optional, Any
from datetime import datetime

from src.utils.data_loader import get_data_loader
from src.utils.rbac import get_rbac_engine, PHI_FIELDS
from src.utils.telemetry import get_telemetry
from src.utils.exceptions import RBACError, PHIBoundaryViolationError

logger = logging.getLogger(__name__)


class EHRServer:
    """Electronic Health Records Server."""
    
    def __init__(self):
        """Initialize EHR Server."""
        self.data_loader = get_data_loader()
        self.rbac = get_rbac_engine()
        self.telemetry = get_telemetry()
        logger.info("EHR Server initialized")
    
    def get_patient_discharge_summary(self, patient_id: str, role: str) -> dict[str, Any]:
        """Get full patient discharge summary.
        
        Args:
            patient_id: Patient identifier.
            role: Role making the request.
            
        Returns:
            Patient discharge summary including PHI.
            
        Raises:
            RBACError: If role lacks permission.
        """
        self.rbac.check_permission(role, "ehr", "get_patient_discharge_summary", patient_id)
        
        patient = self.data_loader.get_patient(patient_id)
        if not patient:
            raise ValueError(f"Patient {patient_id} not found")
        
        self.telemetry.record_alert("INFO", "EHR", f"Discharge summary accessed for {patient_id}")
        
        return {
            "patient_id": patient["patient_id"],
            "mrn": patient["mrn"],
            "name": patient["name"],
            "dob": patient["dob"],
            "ward": patient["ward"],
            "admission_date": patient["admission_date"],
            "discharge_date": patient["discharge_date"],
            "los_days": patient["los_days"],
            "attending_physician": patient["attending_physician"],
            "diagnosis_icd10": patient["diagnosis_icd10"],
            "diagnosis_labels": patient["diagnosis_labels"],
            "discharge_note": patient["discharge_note"],
            "discharge_medications": patient["discharge_medications"],
            "special_instructions": patient["special_instructions"]
        }
    
    def get_discharge_medications(self, patient_id: str, role: str) -> list[dict]:
        """Get discharge medications for a patient.
        
        Args:
            patient_id: Patient identifier.
            role: Role making the request.
            
        Returns:
            List of discharge medications.
            
        Raises:
            RBACError: If role lacks permission.
        """
        self.rbac.check_permission(role, "ehr", "get_discharge_medications", patient_id)
        
        patient = self.data_loader.get_patient(patient_id)
        if not patient:
            raise ValueError(f"Patient {patient_id} not found")
        
        return patient.get("discharge_medications", [])
    
    def get_diagnosis_codes(self, patient_id: str, role: str) -> dict[str, Any]:
        """Get diagnosis codes for a patient.
        
        Args:
            patient_id: Patient identifier.
            role: Role making the request.
            
        Returns:
            Diagnosis codes and labels.
            
        Raises:
            RBACError: If role lacks permission.
        """
        self.rbac.check_permission(role, "ehr", "get_diagnosis_codes", patient_id)
        
        patient = self.data_loader.get_patient(patient_id)
        if not patient:
            raise ValueError(f"Patient {patient_id} not found")
        
        # README contract: ICD-10 only (no free-text clinical notes, no PHI).
        return {"patient_id": patient_id, "diagnosis_icd10": patient.get("diagnosis_icd10", [])}
    
    def get_admission_info(self, patient_id: str, role: str) -> dict[str, Any]:
        """Get admission information for a patient.
        
        Args:
            patient_id: Patient identifier.
            role: Role making the request.
            
        Returns:
            Admission information.
            
        Raises:
            RBACError: If role lacks permission.
        """
        self.rbac.check_permission(role, "ehr", "get_admission_info", patient_id)
        
        patient = self.data_loader.get_patient(patient_id)
        if not patient:
            raise ValueError(f"Patient {patient_id} not found")
        
        return {
            "patient_id": patient_id,
            "ward": patient["ward"],
            "admission_date": patient["admission_date"],
            "discharge_date": patient["discharge_date"],
            "los_days": patient["los_days"],
        }
    
    def get_billing_safe_summary(self, patient_id: str, role: str) -> dict[str, Any]:
        """Get billing-safe patient summary (PHI stripped).
        
        Args:
            patient_id: Patient identifier.
            role: Role making the request.
            
        Returns:
            Patient summary without PHI fields.
            
        Raises:
            RBACError: If role lacks permission.
        """
        self.rbac.check_permission(role, "ehr", "get_billing_safe_summary", patient_id)
        
        patient = self.data_loader.get_patient(patient_id)
        if not patient:
            raise ValueError(f"Patient {patient_id} not found")
        
        # Build billing-safe summary with explicit allow-list (defense-in-depth)
        patient_keys_lower = {k.lower() for k in patient.keys()}
        blocked = sorted({f for f in PHI_FIELDS if f.lower() in patient_keys_lower})

        safe = {
            "patient_id": patient_id,
            "ward": patient.get("ward"),
            "admission_date": patient.get("admission_date"),
            "discharge_date": patient.get("discharge_date"),
            "los_days": patient.get("los_days"),
            "diagnosis_icd10": patient.get("diagnosis_icd10", []),
            "phi_stripped": True,
            "blocked_fields": blocked,
        }

        logger.info("Billing-safe summary generated for %s", patient_id)
        return safe
    
    def get_patient_demographics(self, patient_id: str, role: str) -> dict[str, Any]:
        """Get patient demographics (PHI).
        
        Args:
            patient_id: Patient identifier.
            role: Role making the request.
            
        Returns:
            Patient demographics including PHI.
            
        Raises:
            RBACError: If role lacks permission.
        """
        self.rbac.check_permission(role, "ehr", "get_patient_demographics", patient_id)
        
        patient = self.data_loader.get_patient(patient_id)
        if not patient:
            raise ValueError(f"Patient {patient_id} not found")
        
        return {
            "patient_id": patient["patient_id"],
            "mrn": patient["mrn"],
            "name": patient["name"],
            "dob": patient["dob"],
            "blood_group": patient.get("blood_group")
        }
    
    def get_all_patients(self, role: str) -> list[dict]:
        """Get all patients (admin function).
        
        Args:
            role: Role making the request.
            
        Returns:
            List of all patients.
        """
        self.rbac.check_permission(role, "ehr", "get_patient_discharge_summary")
        
        return self.data_loader.patients
    
    def get_patients_by_ward(self, ward: str, role: str) -> list[dict]:
        """Get patients in a specific ward.
        
        Args:
            ward: Ward name.
            role: Role making the request.
            
        Returns:
            List of patients in the ward.
        """
        self.rbac.check_permission(role, "ehr", "get_admission_info")
        
        return self.data_loader.get_patients_by_ward(ward)

    def list_patients(self, role: str) -> list[str]:
        """List patient IDs (PHI-safe).

        Returns only patient identifiers so callers can request downstream
        PHI-safe summaries per patient without exposing demographics.
        """
        self.rbac.check_permission(role, "ehr", "get_admission_info")
        out: list[str] = []
        for p in (self.data_loader.patients or []):
            try:
                pid = str((p or {}).get("patient_id") or "").strip().upper()
            except Exception:
                pid = ""
            if pid and pid not in out:
                out.append(pid)
        return out
    
    # ========== Clinical Validation Tools ==========
    
    def validate_prescription(self, patient_id: str, role: str = "discharge_coordinator") -> dict[str, Any]:
        """Validate if prescriptions are complete and valid.
        
        Args:
            patient_id: Patient identifier.
            role: Role making the request.
            
        Returns:
            Validation result with completeness check.
        """
        self.rbac.check_permission(role, "ehr", "get_discharge_medications", patient_id)
        
        patient = self.data_loader.get_patient(patient_id)
        if not patient:
            raise ValueError(f"Patient {patient_id} not found")
        
        medications = patient.get("discharge_medications", [])
        
        issues = []
        for med in medications:
            med_name = med.get("drug_name", "")
            dose = med.get("dose", "")
            frequency = med.get("frequency", "")
            route = med.get("route", "")
            days_supply = med.get("days_supply")
            
            if not med_name:
                issues.append({"field": "drug_name", "message": "Drug name is missing"})
            if not dose:
                issues.append({"field": "dose", "message": "Dose is missing"})
            if not frequency:
                issues.append({"field": "frequency", "message": "Frequency is missing"})
            if not route:
                issues.append({"field": "route", "message": "Route is missing"})
            if not days_supply or days_supply <= 0:
                issues.append({"field": "days_supply", "message": "Days supply is missing or invalid"})
        
        return {
            "patient_id": patient_id,
            "medication_count": len(medications),
            "is_valid": len(issues) == 0,
            "issues": issues,
            "all_medications": medications
        }
    
    def check_drug_interactions(self, medications: list[dict], role: str = "discharge_coordinator") -> dict[str, Any]:
        """Check for harmful drug interactions.
        
        Args:
            medications: List of medication dicts with drug_name, dose, etc.
            role: Role making the request.
            
        Returns:
            Interaction check results.
        """
        self.rbac.check_permission(role, "ehr", "get_discharge_medications")
        
        # Known interaction pairs (simplified for demo)
        KNOWN_INTERACTIONS = {
            ("Dapagliflozin", "Insulin"): "Increased risk of hypoglycemia",
            ("Bisoprolol", "Diltiazem"): "Risk of bradycardia and heart block",
            ("Metformin", "Contrast dye"): "Risk of lactic acidosis",
            ("Warfarin", "Aspirin"): "Increased bleeding risk",
            ("Lisinopril", "Potassium"): "Risk of hyperkalemia",
            ("Simvastatin", "Erythromycin"): "Risk of rhabdomyolysis",
        }
        
        interactions_found = []
        drug_names = [m.get("drug_name", "").lower() for m in medications]
        
        for (drug1, drug2), severity_desc in KNOWN_INTERACTIONS.items():
            if drug1.lower() in drug_names and drug2.lower() in drug_names:
                interactions_found.append({
                    "drug_1": drug1,
                    "drug_2": drug2,
                    "severity": "HIGH",
                    "description": severity_desc
                })
        
        return {
            "medication_count": len(medications),
            "interactions_found": len(interactions_found),
            "interactions": interactions_found,
            "safe": len(interactions_found) == 0
        }
    
    def check_dose_validity(self, drug_name: str, dose: str, role: str = "discharge_coordinator") -> dict[str, Any]:
        """Validate dose against clinical standards.
        
        Args:
            drug_name: Drug name.
            dose: Prescribed dose.
            role: Role making the request.
            
        Returns:
            Dose validity check result.
        """
        self.rbac.check_permission(role, "pharmacy", "check_stock")
        
        # Standard doses from pharmacy inventory
        drug = self._resolve_drug_from_inventory(drug_name)
        
        if not drug:
            return {
                "drug_name": drug_name,
                "dose": dose,
                "valid": False,
                "message": f"Drug '{drug_name}' not found in formulary"
            }
        
        formulary_dose = drug.get("formulary_standard_dose", "")
        strengths = drug.get("strengths", [])
        
        # Check if dose matches any available strength
        dose_clean = dose.upper().replace("MG", "").replace("ML", "").strip()
        valid = any(dose_clean in s.upper().replace("MG", "").replace("ML", "").strip() for s in strengths)
        
        return {
            "drug_name": drug_name,
            "prescribed_dose": dose,
            "formulary_standard_dose": formulary_dose,
            "available_strengths": strengths,
            "valid": valid,
            "message": "Dose is within formulary standards" if valid else "Dose may require doctor review"
        }
    
    def _resolve_drug_from_inventory(self, drug_name: str) -> Optional[dict]:
        """Resolve drug name to inventory item."""
        from src.utils.data_loader import get_data_loader
        loader = get_data_loader()
        
        drug_lower = drug_name.lower()
        
        for drug in loader.pharmacy_inventory:
            if drug.get("generic_name", "").lower() == drug_lower:
                return drug
            if drug_lower in [b.lower() for b in drug.get("brand_names", [])]:
                return drug
            if drug_lower in [a.lower() for a in drug.get("semantic_aliases", [])]:
                return drug
        
        return None
    
    # ========== Update / Workflow Tools ==========
    
    def update_prescription(self, patient_id: str, updated_med_list: list[dict], role: str = "clinical_agent") -> dict[str, Any]:
        """Update prescriptions after doctor re-prescribes.
        
        Args:
            patient_id: Patient identifier.
            updated_med_list: New medication list.
            role: Role making the request.
            
        Returns:
            Update confirmation.
        """
        self.rbac.check_permission(role, "ehr", "update_discharge_note", patient_id)
        
        patient = self.data_loader.get_patient(patient_id)
        if not patient:
            raise ValueError(f"Patient {patient_id} not found")
        
        # Update the patient's discharge medications
        patient["discharge_medications"] = updated_med_list
        
        self.telemetry.record_alert("INFO", "EHR", f"Prescription updated for {patient_id}")
        
        return {
            "patient_id": patient_id,
            "status": "prescription_updated",
            "medication_count": len(updated_med_list),
            "updated_at": datetime.utcnow().isoformat()
        }
    
    def mark_patient_ready_for_discharge(self, patient_id: str, role: str = "clinical_agent") -> dict[str, Any]:
        """Mark patient status as ready for discharge.
        
        Args:
            patient_id: Patient identifier.
            role: Role making the request.
            
        Returns:
            Status update confirmation.
        """
        self.rbac.check_permission(role, "ehr", "update_discharge_note", patient_id)
        
        patient = self.data_loader.get_patient(patient_id)
        if not patient:
            raise ValueError(f"Patient {patient_id} not found")
        
        patient["discharge_ready"] = True
        patient["discharge_ready_timestamp"] = datetime.utcnow().isoformat()
        
        self.telemetry.record_alert("INFO", "EHR", f"Patient {patient_id} marked ready for discharge")
        
        return {
            "patient_id": patient_id,
            "status": "ready_for_discharge",
            "ready": True,
            "timestamp": patient["discharge_ready_timestamp"]
        }
    
    # ========== Audit / Logging ==========
    
    def get_patient_history(self, patient_id: str, role: str = "discharge_coordinator") -> dict[str, Any]:
        """Get past prescriptions and conditions.
        
        Args:
            patient_id: Patient identifier.
            role: Role making the request.
            
        Returns:
            Patient history.
        """
        self.rbac.check_permission(role, "ehr", "get_patient_discharge_summary", patient_id)
        
        patient = self.data_loader.get_patient(patient_id)
        if not patient:
            raise ValueError(f"Patient {patient_id} not found")
        
        return {
            "patient_id": patient_id,
            "mrn": patient.get("mrn"),
            "past_diagnoses": patient.get("diagnosis_icd10", []),
            "current_medications": patient.get("discharge_medications", []),
            "admission_date": patient.get("admission_date"),
            "discharge_date": patient.get("discharge_date"),
            "attending_physician": patient.get("attending_physician"),
            "discharge_note": patient.get("discharge_note"),
            "special_instructions": patient.get("special_instructions")
        }
    
    # ========== Real-World Edge Case Tools ==========
    
    def mark_urgent_request(self, patient_id: str, role: str = "discharge_coordinator") -> dict[str, Any]:
        """Mark request as urgent for prioritization.
        
        Args:
            patient_id: Patient identifier.
            role: Role making the request.
            
        Returns:
            Urgent flag confirmation.
        """
        self.rbac.check_permission(role, "ehr", "get_patient_discharge_summary", patient_id)
        
        self.telemetry.record_alert("URGENT", "EHR", f"Urgent request flagged for patient {patient_id}")
        
        return {
            "patient_id": patient_id,
            "urgent": True,
            "priority": "HIGH",
            "timestamp": datetime.utcnow().isoformat()
        }
    
    def escalate_to_doctor(self, patient_id: str, issue: str, role: str = "discharge_coordinator") -> dict[str, Any]:
        """Trigger doctor review/escalation.
        
        Args:
            patient_id: Patient identifier.
            issue: Issue description.
            role: Role making the request.
            
        Returns:
            Escalation confirmation.
        """
        self.rbac.check_permission(role, "ehr", "get_patient_discharge_summary", patient_id)
        
        patient = self.data_loader.get_patient(patient_id)
        if not patient:
            raise ValueError(f"Patient {patient_id} not found")
        
        self.telemetry.record_alert("URGENT", "EHR", f"Escalated to doctor for patient {patient_id}: {issue}")
        
        return {
            "patient_id": patient_id,
            "escalated": True,
            "issue": issue,
            "attending_physician": patient.get("attending_physician"),
            "timestamp": datetime.utcnow().isoformat()
        }
    
    def request_represcription(self, patient_id: str, drug_name: str, reason: str, role: str = "discharge_coordinator") -> dict[str, Any]:
        """Request doctor re-prescription when no alternative exists.
        
        Args:
            patient_id: Patient identifier.
            drug_name: Drug that needs re-prescription.
            reason: Reason for re-prescription.
            role: Role making the request.
            
        Returns:
            Re-prescription request confirmation.
        """
        self.rbac.check_permission(role, "ehr", "get_discharge_medications", patient_id)
        
        patient = self.data_loader.get_patient(patient_id)
        if not patient:
            raise ValueError(f"Patient {patient_id} not found")
        
        self.telemetry.record_alert(
            "WARNING",
            "EHR",
            f"Re-prescription requested for {patient_id}: {drug_name} - {reason}"
        )
        
        return {
            "patient_id": patient_id,
            "drug_name": drug_name,
            "reason": reason,
            "represcription_requested": True,
            "attending_physician": patient.get("attending_physician"),
            "timestamp": datetime.utcnow().isoformat()
        }
    
    # ========== Notification Tools ==========

    def notify_patient(self, message: str, patient_id: Optional[str] = None, role: str = "discharge_coordinator") -> dict[str, Any]:
        """Inform patient via notification portal.

        Args:
            message: Message content to send.
            patient_id: Optional patient ID for audit trail.
            role: Role making the request.

        Returns:
            Notification confirmation.
        """
        self.rbac.check_permission(role, "ehr", "notify_patient", patient_id)
        self.telemetry.record_alert("INFO", "Notification", f"Patient notification sent: {message[:80]}", {"patient_id": patient_id})
        return {
            "notified": True,
            "channel": "sms_portal",
            "patient_id": patient_id,
            "message_preview": message[:100],
            "timestamp": datetime.utcnow().isoformat(),
        }

    def notify_doctor(self, message: str, patient_id: str, role: str = "discharge_coordinator") -> dict[str, Any]:
        """Inform attending physician via paging system.

        Args:
            message: Message content to send.
            patient_id: Patient ID whose doctor to notify.
            role: Role making the request.

        Returns:
            Notification confirmation.
        """
        self.rbac.check_permission(role, "ehr", "notify_doctor", patient_id)
        patient = self.data_loader.get_patient(patient_id)
        physician = patient.get("attending_physician", "Unknown") if patient else "Unknown"
        self.telemetry.record_alert("INFO", "Notification", f"Doctor notification sent: {message[:80]}", {"patient_id": patient_id})
        return {
            "notified": True,
            "channel": "paging_system",
            "patient_id": patient_id,
            "physician": physician,
            "message_preview": message[:100],
            "timestamp": datetime.utcnow().isoformat(),
        }

    # ========== Data Validation Tools ==========

    def validate_patient_id(self, patient_id: str, role: str = "discharge_coordinator") -> dict[str, Any]:
        """Check if patient exists.
        
        Args:
            patient_id: Patient identifier.
            role: Role making the request.
            
        Returns:
            Validation result.
        """
        self.rbac.check_permission(role, "ehr", "get_patient_discharge_summary")
        
        patient = self.data_loader.get_patient(patient_id)
        
        return {
            "patient_id": patient_id,
            "exists": patient is not None,
            "valid": patient is not None
        }


# Global EHR Server instance
_ehr_server: Optional[EHRServer] = None


def get_ehr_server() -> EHRServer:
    """Get the global EHR Server instance."""
    global _ehr_server
    if _ehr_server is None:
        _ehr_server = EHRServer()
    return _ehr_server
