"""Pharmacy Server for MCPDischarge.

Provides medication inventory management, pricing, and dispensing.
"""

import logging
from typing import Optional, Any, List
from datetime import datetime
import re

from src.utils.data_loader import get_data_loader
from src.utils.rbac import get_rbac_engine
from src.utils.telemetry import get_telemetry
from src.utils.exceptions import RBACError, StockUnavailableError, DoseConflictError

logger = logging.getLogger(__name__)


class PharmacyServer:
    """Pharmacy Server for medication management."""
    
    def __init__(self):
        """Initialize Pharmacy Server."""
        self.data_loader = get_data_loader()
        self.rbac = get_rbac_engine()
        self.telemetry = get_telemetry()
        self._dispense_requests: List[dict] = []
        logger.info("Pharmacy Server initialized")
    
    def check_stock(
        self,
        drug_name: str,
        quantity: int = 1,
        dose: Optional[str] = None,
        role: str = "discharge_coordinator",
    ) -> dict[str, Any]:
        """Check if a drug is in stock.
        
        Args:
            drug_name: Drug name (generic or brand).
            quantity: Required quantity.
            role: Role making the request.
            
        Returns:
            Stock status information.
            
        Raises:
            RBACError: If role lacks permission.
        """
        self.rbac.check_permission(role, "pharmacy", "check_stock")
        
        # Resolve drug name to inventory item
        drug = self._resolve_drug(drug_name)
        
        if not drug:
            return {
                "drug_name": drug_name,
                "found": False,
                "message": f"Drug '{drug_name}' not found in pharmacy inventory"
            }
        
        in_stock = drug.get("in_stock", False)
        stock_units = drug.get("stock_units", 0)
        available = bool(in_stock) and stock_units >= quantity

        dose_conflict, dose_conflict_detail = self._detect_dose_conflict(drug, dose)

        result = {
            "drug_name": drug_name,
            "generic_name": drug.get("generic_name"),
            "brand_names": drug.get("brand_names"),
            "in_stock": in_stock,
            "stock_units": stock_units,
            "available": available,
            "requested_quantity": quantity,
            "dose": dose,
            "dose_conflict": dose_conflict,
            "dose_conflict_detail": dose_conflict_detail,
            "reorder_threshold": drug.get("reorder_threshold"),
            "requires_refrigeration": drug.get("requires_refrigeration")
        }
        
        if not available:
            self.telemetry.record_alert(
                "WARNING",
                "Pharmacy",
                f"Stock low for {drug_name}: {stock_units} units",
                result
            )
        
        return result
    
    def get_alternative(self, drug_name: str, role: str = "discharge_coordinator") -> dict[str, Any]:
        """Get alternative drugs when primary is unavailable.
        
        Args:
            drug_name: Drug name to find alternatives for.
            role: Role making the request.
            
        Returns:
            Alternative drug options.
            
        Raises:
            RBACError: If role lacks permission.
        """
        self.rbac.check_permission(role, "pharmacy", "get_alternative")
        
        drug = self._resolve_drug(drug_name)
        
        if not drug:
            return {
                "drug_name": drug_name,
                "found": False,
                "alternatives": []
            }
        
        # Find alternatives in same therapeutic class
        alternatives = []
        
        # Check semantic aliases for therapeutic class
        drug_class = self._get_drug_class(drug)
        
        for item in self.data_loader.pharmacy_inventory:
            if item.get("drug_id") == drug.get("drug_id"):
                continue
            
            # Check if in same class and in stock
            if self._get_drug_class(item) == drug_class and item.get("in_stock", False):
                alternatives.append({
                    "drug_id": item.get("drug_id"),
                    "generic_name": item.get("generic_name"),
                    "brand_names": item.get("brand_names"),
                    "strengths": item.get("strengths"),
                    "formulations": item.get("formulations"),
                    "stock_units": item.get("stock_units"),
                    "price_per_unit_inr": item.get("price_per_unit_inr"),
                    "formulary_dose": item.get("formulary_standard_dose")
                })
        
        return {
            "original_drug": drug_name,
            "generic_name": drug.get("generic_name"),
            "drug_class": drug_class,
            "alternatives": alternatives,
            "count": len(alternatives)
        }
    
    def get_price(self, drug_name: str, quantity: int = 1,
                 role: str = "discharge_coordinator") -> dict[str, Any]:
        """Get drug pricing information.
        
        Args:
            drug_name: Drug name.
            quantity: Quantity for pricing.
            role: Role making the request.
            
        Returns:
            Pricing information.
            
        Raises:
            RBACError: If role lacks permission.
        """
        self.rbac.check_permission(role, "pharmacy", "get_price")
        
        drug = self._resolve_drug(drug_name)
        
        if not drug:
            raise ValueError(f"Drug '{drug_name}' not found")
        
        unit_price = drug.get("price_per_unit_inr", 0)
        total_price = unit_price * quantity
        controlled = drug.get("controlled_substance", False)
        specialty = drug.get("specialty_drug", False)
        
        return {
            "drug_name": drug_name,
            "generic_name": drug.get("generic_name"),
            "unit_price_inr": unit_price,
            "quantity": quantity,
            "total_price_inr": total_price,
            "controlled_substance": controlled,
            "specialty_drug": specialty,
            "dispensing_fee": 500 if specialty else 0
        }
    
    def dispense_request(self, patient_id: str, drug_name: str, 
                        quantity: int, dose: str, frequency: str,
                        days_supply: int, route: str,
                        role: str = "discharge_coordinator") -> dict[str, Any]:
        """Submit a dispense request.
        
        Args:
            patient_id: Patient identifier.
            drug_name: Drug name.
            quantity: Quantity to dispense.
            dose: Dose amount.
            frequency: Dosing frequency.
            days_supply: Days supply.
            route: Administration route.
            role: Role making the request.
            
        Returns:
            Dispense request confirmation.
            
        Raises:
            RBACError: If role lacks permission.
            StockUnavailableError: If drug not in stock.
        """
        self.rbac.check_permission(role, "pharmacy", "dispense_request")
        
        # Resolve drug
        drug = self._resolve_drug(drug_name)
        
        if not drug:
            raise StockUnavailableError(drug_name, patient_id, 
                                       ["Please consult pharmacy for alternatives"])
        
        if not drug.get("in_stock", False) or drug.get("stock_units", 0) < quantity:
            alternatives = self.get_alternative(drug_name, role)
            raise StockUnavailableError(
                drug_name, patient_id,
                [a["generic_name"] for a in alternatives.get("alternatives", [])]
            )
        
        # Check for dose conflicts (formulary vs prescribed)
        dose_conflict, dose_conflict_detail = self._detect_dose_conflict(drug, dose)
        if dose_conflict:
            raise DoseConflictError(
                drug.get("generic_name") or drug_name,
                "formulary_standard_dose",
                dose_conflict_detail or "Dose mismatch vs formulary standard dose",
            )
        
        # Create dispense request
        request = {
            "request_id": f"DISP-{len(self._dispense_requests) + 1:05d}",
            "patient_id": patient_id,
            "drug_id": drug.get("drug_id"),
            "drug_name": drug.get("generic_name"),
            "brand_dispensed": drug.get("brand_names", [None])[0],
            "quantity": quantity,
            "dose": dose,
            "frequency": frequency,
            "days_supply": days_supply,
            "route": route,
            "status": "DISPENSED",
            "timestamp": datetime.utcnow().isoformat(),
            "unit_price": drug.get("price_per_unit_inr"),
            "total_price": drug.get("price_per_unit_inr") * quantity
        }
        
        self._dispense_requests.append(request)
        
        # Update inventory
        drug["stock_units"] -= quantity
        
        self.telemetry.record_alert(
            "INFO",
            "Pharmacy",
            f"Dispensed {quantity} x {drug_name} for patient {patient_id}",
            request
        )
        
        return request
    
    def get_dispense_history(self, patient_id: Optional[str] = None,
                            role: str = "discharge_coordinator") -> List[dict]:
        """Get dispense request history.
        
        Args:
            patient_id: Optional patient ID to filter by.
            role: Role making the request.
            
        Returns:
            List of dispense requests.
        """
        self.rbac.check_permission(role, "pharmacy", "dispense_request")
        
        if patient_id:
            return [r for r in self._dispense_requests if r["patient_id"] == patient_id]
        return self._dispense_requests.copy()
    
    def _resolve_drug(self, drug_name: str) -> Optional[dict]:
        """Resolve drug name to inventory item.
        
        Args:
            drug_name: Drug name (generic, brand, or alias).
            
        Returns:
            Drug inventory item or None.
        """
        return self.data_loader.get_drug_by_name(drug_name)
    
    def _get_drug_class(self, drug: dict) -> str:
        """Get therapeutic class of a drug.
        
        Args:
            drug: Drug inventory item.
            
        Returns:
            Therapeutic class string.
        """
        # Map based on semantic aliases
        aliases = [a.lower() for a in drug.get("semantic_aliases", [])]
        
        if "sglt2" in aliases:
            return "SGLT2_INHIBITOR"
        elif "beta blocker" in aliases:
            return "BETA_BLOCKER"
        elif "ace inhibitor" in aliases:
            return "ACE_INHIBITOR"
        elif "diuretic" in aliases:
            return "DIURETIC"
        elif "glp-1" in aliases:
            return "GLP1_AGONIST"
        elif "statin" in aliases:
            return "STATIN"
        elif "ccb" in aliases or "calcium channel" in aliases:
            return "CCB"
        
        return "OTHER"
    
    @staticmethod
    def _parse_strength_mg(value: Optional[str]) -> Optional[float]:
        if not value:
            return None
        match = re.search(r"(\d+(?:\.\d+)?)\s*mg", value.lower())
        if not match:
            return None
        try:
            return float(match.group(1))
        except ValueError:
            return None

    def _detect_dose_conflict(self, drug: dict, prescribed_dose: Optional[str]) -> tuple[bool, str]:
        """Detect mismatch between prescribed dose and formulary standard dose/strengths."""
        if not prescribed_dose:
            return False, ""

        prescribed_mg = self._parse_strength_mg(prescribed_dose)
        if prescribed_mg is None:
            return False, ""

        strengths = drug.get("strengths", [])
        strengths_mg = [self._parse_strength_mg(s) for s in strengths]
        strengths_mg = [s for s in strengths_mg if s is not None]

        formulary_dose = drug.get("formulary_standard_dose")
        formulary_mg = self._parse_strength_mg(formulary_dose)

        # If prescribed strength isn't even in available strengths, flag immediately.
        if strengths_mg and prescribed_mg not in strengths_mg:
            return True, (
                f"Prescribed strength {prescribed_mg}mg not in pharmacy strengths {sorted(set(strengths))}"
            )

        # If formulary dose exists and differs materially (>25%), flag.
        if formulary_mg is not None:
            if formulary_mg == 0:
                return False, ""
            delta_ratio = abs(prescribed_mg - formulary_mg) / formulary_mg
            if delta_ratio >= 0.25:
                return True, (
                    f"Prescribed {prescribed_mg}mg differs from formulary standard {formulary_dose}"
                )

        return False, ""


# Global Pharmacy Server instance
_pharmacy_server: Optional[PharmacyServer] = None


def get_pharmacy_server() -> PharmacyServer:
    """Get the global Pharmacy Server instance."""
    global _pharmacy_server
    if _pharmacy_server is None:
        _pharmacy_server = PharmacyServer()
    return _pharmacy_server
