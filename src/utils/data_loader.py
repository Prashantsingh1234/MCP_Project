"""Data loader for MCPDischarge.

Loads and provides access to all JSON data files.
"""

import json
import logging
from pathlib import Path
from typing import Optional, Any
from datetime import datetime

logger = logging.getLogger(__name__)

# Default data directory
DATA_DIR = Path(__file__).parent.parent.parent / "data"


class DataLoader:
    """Loads and caches data from JSON files."""
    
    _instance: Optional['DataLoader'] = None
    
    def __new__(cls):
        """Singleton pattern for data loader."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self, data_dir: Optional[Path] = None):
        """Initialize data loader.
        
        Args:
            data_dir: Path to data directory.
        """
        if self._initialized:
            return
            
        self._initialized = True
        self.data_dir = data_dir or DATA_DIR
        self._data: dict[str, Any] = {}
        self._load_all()
    
    def _load_all(self):
        """Load all JSON data files."""
        self._data = {}
        
        files = [
            "ehr_patients.json",
            "pharmacy_inventory.json", 
            "billing_rate_cards.json",
            "insurance_contracts.json",
            "patient_insurance_map.json",
            "icd10_billing_codes.json",
            "rbac_policies.json"
        ]
        
        for filename in files:
            filepath = self.data_dir / filename
            key = filename.replace(".json", "")
            try:
                with open(filepath, 'r') as f:
                    self._data[key] = json.load(f)
                logger.info(f"Loaded {filename}: {len(self._data[key])} records")
            except FileNotFoundError:
                logger.warning(f"Data file not found: {filepath}")
                self._data[key] = [] if "codes" not in key else {}
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse {filename}: {e}")
                self._data[key] = [] if "codes" not in key else {}
    
    def reload(self):
        """Reload all data files."""
        self._load_all()
    
    @property
    def patients(self) -> list[dict]:
        """Get all patients."""
        return self._data.get("ehr_patients", [])
    
    @property
    def pharmacy_inventory(self) -> list[dict]:
        """Get pharmacy inventory."""
        return self._data.get("pharmacy_inventory", [])
    
    @property
    def billing_rate_cards(self) -> list[dict]:
        """Get billing rate cards."""
        return self._data.get("billing_rate_cards", [])
    
    @property
    def insurance_contracts(self) -> list[dict]:
        """Get insurance contracts."""
        return self._data.get("insurance_contracts", [])
    
    @property
    def patient_insurance_map(self) -> dict:
        """Get patient to insurance mapping."""
        return self._data.get("patient_insurance_map", {})
    
    @property
    def icd10_billing_codes(self) -> dict:
        """Get ICD10 billing codes."""
        return self._data.get("icd10_billing_codes", {})
    
    @property
    def rbac_policies(self) -> dict:
        """Get RBAC policies."""
        return self._data.get("rbac_policies", {})
    
    def get_patient(self, patient_id: str) -> Optional[dict]:
        """Get a specific patient by ID."""
        for patient in self.patients:
            if patient.get("patient_id") == patient_id:
                return patient
        return None
    
    def get_patients_by_ward(self, ward: str) -> list[dict]:
        """Get all patients in a specific ward."""
        return [p for p in self.patients if p.get("ward") == ward]
    
    def get_drug(self, drug_id: str) -> Optional[dict]:
        """Get a specific drug by ID."""
        for drug in self.pharmacy_inventory:
            if drug.get("drug_id") == drug_id:
                return drug
        return None
    
    def get_drug_by_name(self, drug_name: str) -> Optional[dict]:
        """Get a drug by name (generic or brand)."""
        search_name = drug_name.lower()
        for drug in self.pharmacy_inventory:
            # Check generic name
            if drug.get("generic_name", "").lower() == search_name:
                return drug
            # Check brand names
            for brand in drug.get("brand_names", []):
                if brand.lower() == search_name:
                    return drug
            # Check semantic aliases
            for alias in drug.get("semantic_aliases", []):
                if alias.lower() == search_name:
                    return drug
        return None
    
    def get_drugs_in_stock(self) -> list[dict]:
        """Get all drugs currently in stock."""
        return [d for d in self.pharmacy_inventory if d.get("in_stock", False)]
    
    def get_charge(self, charge_code: str) -> Optional[dict]:
        """Get a specific charge by code."""
        for charge in self.billing_rate_cards:
            if charge.get("charge_code") == charge_code:
                return charge
        return None
    
    def get_insurance(self, insurer_id: str) -> Optional[dict]:
        """Get insurance contract by ID."""
        for contract in self.insurance_contracts:
            if contract.get("insurer_id") == insurer_id:
                return contract
        return None
    
    def get_patient_insurance(self, patient_id: str) -> Optional[dict]:
        """Get insurance info for a patient."""
        return self.patient_insurance_map.get(patient_id)
    
    def get_charges_by_ward(self, ward: str) -> list[dict]:
        """Get charge codes for a specific ward type."""
        ward_code_map = {
            "Cardiology": "WRD-CARD",
            "Nephrology": "WRD-NEPH", 
            "Rheumatology": "WRD-RHEU",
            "Oncology": "WRD-ONCO",
            "Neurology": "WRD-NEUR"
        }
        ward_code = ward_code_map.get(ward)
        if ward_code:
            return [c for c in self.billing_rate_cards if c.get("charge_code") == ward_code]
        return []


# Global data loader instance
_data_loader: Optional[DataLoader] = None


def get_data_loader() -> DataLoader:
    """Get the global data loader instance."""
    global _data_loader
    if _data_loader is None:
        _data_loader = DataLoader()
    return _data_loader