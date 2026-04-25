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
        
        return {
            "patient_id": patient_id,
            "diagnosis_icd10": patient["diagnosis_icd10"],
            "diagnosis_labels": patient["diagnosis_labels"]
        }
    
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
            "diagnosis_labels": patient.get("diagnosis_labels", []),
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


# Global EHR Server instance
_ehr_server: Optional[EHRServer] = None


def get_ehr_server() -> EHRServer:
    """Get the global EHR Server instance."""
    global _ehr_server
    if _ehr_server is None:
        _ehr_server = EHRServer()
    return _ehr_server
