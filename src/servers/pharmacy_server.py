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
            "found": True,
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
            "total_price": (drug.get("price_per_unit_inr") or 0) * quantity
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
    
    def list_in_stock_drugs(self, role: str = "discharge_coordinator") -> list[dict]:
        """Return all drugs currently in stock with key details.

        Args:
            role: Role making the request.

        Returns:
            List of in-stock drug summaries (no PHI).
        """
        self.rbac.check_permission(role, "pharmacy", "check_stock")

        results = []
        for drug in self.data_loader.pharmacy_inventory:
            if drug.get("in_stock", False) and drug.get("stock_units", 0) > 0:
                results.append({
                    "drug_id": drug.get("drug_id"),
                    "generic_name": drug.get("generic_name"),
                    "brand_names": drug.get("brand_names", []),
                    "strengths": drug.get("strengths", []),
                    "stock_units": drug.get("stock_units"),
                    "price_per_unit_inr": drug.get("price_per_unit_inr"),
                    "controlled_substance": drug.get("controlled_substance", False),
                    "specialty_drug": drug.get("specialty_drug", False),
                })
        return results

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
        # Use substring matching so "loop diuretic", "sglt2 inhibitor", etc. all resolve correctly.
        aliases = [a.lower() for a in drug.get("semantic_aliases", [])]
        aliases_joined = " ".join(aliases)

        if "sglt2" in aliases_joined:
            return "SGLT2_INHIBITOR"
        elif "beta blocker" in aliases_joined:
            return "BETA_BLOCKER"
        elif "ace inhibitor" in aliases_joined:
            return "ACE_INHIBITOR"
        elif "diuretic" in aliases_joined:
            return "DIURETIC"
        elif "glp-1" in aliases_joined or "glp1" in aliases_joined:
            return "GLP1_AGONIST"
        elif "statin" in aliases_joined:
            return "STATIN"
        elif "ccb" in aliases_joined or "calcium channel" in aliases_joined:
            return "CCB"
        elif "tnf" in aliases_joined or "biologic" in aliases_joined or "biosimilar" in aliases_joined or "immunosuppressant" in aliases_joined:
            return "BIOLOGIC_IMMUNOSUPPRESSANT"
        elif "corticosteroid" in aliases_joined:
            return "CORTICOSTEROID"
        elif "antifolate" in aliases_joined or "dmard" in aliases_joined:
            return "DMARD"

        return drug.get("drug_id", "OTHER")
    
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
    
    # ========== Additional Stock & Availability Tools ==========
    
    def check_bulk_stock(self, drug_list: list[dict], role: str = "discharge_coordinator") -> dict[str, Any]:
        """Optimized check for multiple drugs.
        
        Args:
            drug_list: List of drugs to check [{drug_name, quantity, dose}].
            role: Role making the request.
            
        Returns:
            Bulk stock check results.
        """
        self.rbac.check_permission(role, "pharmacy", "check_stock")
        
        results = []
        all_available = True
        
        for item in drug_list:
            drug_name = item.get("drug_name", "")
            quantity = item.get("quantity", 1)
            dose = item.get("dose")
            
            stock_result = self.check_stock(drug_name, quantity=quantity, dose=dose, role=role)
            results.append(stock_result)
            
            if not stock_result.get("available", False):
                all_available = False
        
        return {
            "drug_count": len(drug_list),
            "all_available": all_available,
            "results": results
        }
    
    def get_all_alternatives(self, drug_name: str, role: str = "discharge_coordinator") -> dict[str, Any]:
        """Returns multiple alternative drug options.
        
        Args:
            drug_name: Drug name to find alternatives for.
            role: Role making the request.
            
        Returns:
            All alternative drug options.
        """
        self.rbac.check_permission(role, "pharmacy", "get_alternative")
        
        drug = self._resolve_drug(drug_name)
        
        if not drug:
            return {
                "drug_name": drug_name,
                "found": False,
                "alternatives": []
            }
        
        # Get primary alternatives
        primary_result = self.get_alternative(drug_name, role)
        alternatives = primary_result.get("alternatives", [])
        
        # Also check for alternatives in other therapeutic classes
        other_alternatives = []
        drug_class = self._get_drug_class(drug)
        
        for item in self.data_loader.pharmacy_inventory:
            if item.get("drug_id") == drug.get("drug_id"):
                continue
            if self._get_drug_class(item) != drug_class and item.get("in_stock", False):
                other_alternatives.append({
                    "drug_id": item.get("drug_id"),
                    "generic_name": item.get("generic_name"),
                    "brand_names": item.get("brand_names"),
                    "therapeutic_class": self._get_drug_class(item),
                    "stock_units": item.get("stock_units"),
                    "price_per_unit_inr": item.get("price_per_unit_inr")
                })
        
        return {
            "original_drug": drug_name,
            "generic_name": drug.get("generic_name"),
            "primary_alternatives": alternatives,
            "other_class_alternatives": other_alternatives,
            "total_alternatives": len(alternatives) + len(other_alternatives)
        }
    
    def check_therapeutic_equivalence(self, drug_a: str, drug_b: str, role: str = "discharge_coordinator") -> dict[str, Any]:
        """Validate if alternative is clinically equivalent.
        
        Args:
            drug_a: First drug name.
            drug_b: Second drug name.
            role: Role making the request.
            
        Returns:
            Therapeutic equivalence result.
        """
        self.rbac.check_permission(role, "pharmacy", "get_alternative")
        
        drug1 = self._resolve_drug(drug_a)
        drug2 = self._resolve_drug(drug_b)
        
        if not drug1 or not drug2:
            return {
                "drug_a": drug_a,
                "drug_b": drug_b,
                "found": False,
                "equivalent": False,
                "message": "One or both drugs not found"
            }
        
        class_a = self._get_drug_class(drug1)
        class_b = self._get_drug_class(drug2)
        
        equivalent = class_a == class_b
        
        return {
            "drug_a": drug_a,
            "drug_b": drug_b,
            "class_a": class_a,
            "class_b": class_b,
            "equivalent": equivalent,
            "message": "Therapeutically equivalent" if equivalent else "Different therapeutic classes"
        }
    
    # ========== Drug Matching Tools ==========
    
    def resolve_drug_name_alias(self, input_name: str, role: str = "discharge_coordinator") -> dict[str, Any]:
        """Convert brand ↔ generic drug names.
        
        Args:
            input_name: Input drug name.
            role: Role making the request.
            
        Returns:
            Resolved drug name mapping.
        """
        self.rbac.check_permission(role, "pharmacy", "check_stock")
        
        drug = self._resolve_drug(input_name)
        
        if not drug:
            return {
                "input_name": input_name,
                "resolved": False,
                "message": f"Drug '{input_name}' not found"
            }
        
        return {
            "input_name": input_name,
            "resolved": True,
            "generic_name": drug.get("generic_name"),
            "brand_names": drug.get("brand_names"),
            "strengths": drug.get("strengths"),
            "formulations": drug.get("formulations")
        }
    
    def semantic_drug_search(self, query: str, role: str = "discharge_coordinator") -> dict[str, Any]:
        """Fuzzy match drug names.
        
        Args:
            query: Search query.
            role: Role making the request.
            
        Returns:
            Search results.
        """
        self.rbac.check_permission(role, "pharmacy", "check_stock")
        
        query_lower = query.lower()
        results = []
        
        for drug in self.data_loader.pharmacy_inventory:
            generic = drug.get("generic_name", "").lower()
            brands = [b.lower() for b in drug.get("brand_names", [])]
            aliases = [a.lower() for a in drug.get("semantic_aliases", [])]
            
            # Check for matches
            if (query_lower in generic or 
                any(query_lower in b for b in brands) or 
                any(query_lower in a for a in aliases)):
                results.append({
                    "drug_id": drug.get("drug_id"),
                    "generic_name": drug.get("generic_name"),
                    "brand_names": drug.get("brand_names"),
                    "in_stock": drug.get("in_stock"),
                    "stock_units": drug.get("stock_units"),
                    "price_per_unit_inr": drug.get("price_per_unit_inr")
                })
        
        return {
            "query": query,
            "results": results,
            "count": len(results)
        }
    
    # ========== Pricing Tools ==========
    
    def get_bulk_price(self, drug_list: list[dict], role: str = "discharge_coordinator") -> dict[str, Any]:
        """Returns cost for all drugs.
        
        Args:
            drug_list: List of drugs [{drug_name, quantity}].
            role: Role making the request.
            
        Returns:
            Bulk pricing result.
        """
        self.rbac.check_permission(role, "pharmacy", "get_price")
        
        results = []
        total_cost = 0
        
        for item in drug_list:
            drug_name = item.get("drug_name", "")
            quantity = item.get("quantity", 1)
            
            try:
                price_result = self.get_price(drug_name, quantity=quantity, role=role)
                results.append(price_result)
                total_cost += price_result.get("total_price_inr", 0)
            except ValueError as e:
                results.append({
                    "drug_name": drug_name,
                    "error": str(e)
                })
        
        return {
            "drug_count": len(drug_list),
            "items": results,
            "total_cost_inr": total_cost
        }
    
    # ========== Dispensing Tools ==========
    
    def create_dispense_request(self, patient_id: str, drug_list: list[dict], role: str = "discharge_coordinator") -> dict[str, Any]:
        """Initiate dispensing request.
        
        Args:
            patient_id: Patient identifier.
            drug_list: List of drugs to dispense.
            role: Role making the request.
            
        Returns:
            Dispense request confirmation.
        """
        self.rbac.check_permission(role, "pharmacy", "dispense_request")
        
        requests = []
        for item in drug_list:
            try:
                result = self.dispense_request(
                    patient_id=patient_id,
                    drug_name=item.get("drug_name", ""),
                    quantity=item.get("quantity", 1),
                    dose=item.get("dose", ""),
                    frequency=item.get("frequency", ""),
                    days_supply=item.get("days_supply", 7),
                    route=item.get("route", "oral"),
                    role=role
                )
                requests.append(result)
            except Exception as e:
                requests.append({
                    "drug_name": item.get("drug_name"),
                    "status": "failed",
                    "error": str(e)
                })
        
        return {
            "patient_id": patient_id,
            "dispense_requests": requests,
            "total_items": len(drug_list),
            "successful": len([r for r in requests if r.get("status") == "DISPENSED"])
        }
    
    def confirm_dispense(self, patient_id: str, role: str = "discharge_coordinator") -> dict[str, Any]:
        """Confirm medicines issued.
        
        Args:
            patient_id: Patient identifier.
            role: Role making the request.
            
        Returns:
            Confirmation result.
        """
        self.rbac.check_permission(role, "pharmacy", "dispense_request")
        
        # Get recent dispense requests for patient
        requests = [r for r in self._dispense_requests if r.get("patient_id") == patient_id]
        
        if not requests:
            return {
                "patient_id": patient_id,
                "confirmed": False,
                "message": "No dispense requests found"
            }
        
        # Confirm all pending
        for req in requests:
            if req.get("status") == "DISPENSED":
                req["confirmed"] = True
                req["confirmed_at"] = datetime.utcnow().isoformat()
        
        return {
            "patient_id": patient_id,
            "confirmed": True,
            "items_confirmed": len(requests),
            "timestamp": datetime.utcnow().isoformat()
        }
    
    # ========== Inventory Management Tools ==========
    
    def update_stock(self, drug_name: str, quantity: int, role: str = "pharmacy_agent") -> dict[str, Any]:
        """Adjust inventory.
        
        Args:
            drug_name: Drug name.
            quantity: Quantity to add (positive) or remove (negative).
            role: Role making the request.
            
        Returns:
            Stock update confirmation.
        """
        self.rbac.check_permission(role, "pharmacy", "update_inventory")
        
        drug = self._resolve_drug(drug_name)
        
        if not drug:
            return {
                "drug_name": drug_name,
                "success": False,
                "message": f"Drug '{drug_name}' not found"
            }
        
        old_stock = drug.get("stock_units", 0)
        new_stock = old_stock + quantity
        drug["stock_units"] = max(0, new_stock)
        
        # Update in_stock status
        drug["in_stock"] = new_stock > 0
        
        self.telemetry.record_alert(
            "INFO",
            "Pharmacy",
            f"Stock updated for {drug_name}: {old_stock} -> {new_stock}"
        )
        
        return {
            "drug_name": drug_name,
            "success": True,
            "old_stock": old_stock,
            "new_stock": new_stock,
            "quantity_adjusted": quantity
        }
    
    def check_nearby_pharmacy_availability(self, drug_name: str, role: str = "discharge_coordinator") -> dict[str, Any]:
        """Check external pharmacy availability (real-world scenario).
        
        Args:
            drug_name: Drug name.
            role: Role making the request.
            
        Returns:
            External pharmacy availability.
        """
        self.rbac.check_permission(role, "pharmacy", "check_stock")
        
        # Check local stock first
        local_drug = self._resolve_drug(drug_name)
        local_available = local_drug.get("in_stock", False) if local_drug else False
        
        # Simulate external pharmacies
        external_pharmacies = [
            {"name": "City Pharmacy", "distance_km": 1.2, "available": True, "phone": "+91-9876543210"},
            {"name": "MedPlus", "distance_km": 2.5, "available": True, "phone": "+91-9876543211"},
            {"name": "Apollo", "distance_km": 3.0, "available": False, "phone": "+91-9876543212"},
        ]
        
        return {
            "drug_name": drug_name,
            "local_available": local_available,
            "external_pharmacies": external_pharmacies,
            "any_available": local_available or any(p.get("available") for p in external_pharmacies)
        }
    
    # ========== Alerts & Conflicts Tools ==========
    
    def detect_dose_conflict(self, drug_name: str, prescribed_dose: str, role: str = "discharge_coordinator") -> dict[str, Any]:
        """Flag dose mismatch.
        
        Args:
            drug_name: Drug name.
            prescribed_dose: Prescribed dose.
            role: Role making the request.
            
        Returns:
            Dose conflict detection result.
        """
        self.rbac.check_permission(role, "pharmacy", "check_stock")
        
        drug = self._resolve_drug(drug_name)
        
        if not drug:
            return {
                "drug_name": drug_name,
                "conflict": False,
                "message": "Drug not found"
            }
        
        conflict, detail = self._detect_dose_conflict(drug, prescribed_dose)
        
        return {
            "drug_name": drug_name,
            "prescribed_dose": prescribed_dose,
            "formulary_standard_dose": drug.get("formulary_standard_dose"),
            "available_strengths": drug.get("strengths"),
            "conflict": conflict,
            "detail": detail
        }
    
    def flag_controlled_substance(self, drug_name: str, role: str = "discharge_coordinator") -> dict[str, Any]:
        """Mark restricted/controlled drugs.
        
        Args:
            drug_name: Drug name.
            role: Role making the request.
            
        Returns:
            Controlled substance flag.
        """
        self.rbac.check_permission(role, "pharmacy", "check_stock")
        
        drug = self._resolve_drug(drug_name)
        
        if not drug:
            return {
                "drug_name": drug_name,
                "controlled_substance": False,
                "message": "Drug not found"
            }
        
        controlled = drug.get("controlled_substance", False)
        
        return {
            "drug_name": drug_name,
            "generic_name": drug.get("generic_name"),
            "controlled_substance": controlled,
            "schedule": "Schedule H" if controlled else "Unrestricted",
            "requires_license": controlled
        }
    
    # ========== Data Validation Tools ==========
    
    def validate_drug_name(self, drug_name: str, role: str = "discharge_coordinator") -> dict[str, Any]:
        """Check if drug exists.
        
        Args:
            drug_name: Drug name.
            role: Role making the request.
            
        Returns:
            Validation result.
        """
        self.rbac.check_permission(role, "pharmacy", "check_stock")
        
        drug = self._resolve_drug(drug_name)
        
        return {
            "drug_name": drug_name,
            "exists": drug is not None,
            "valid": drug is not None,
            "generic_name": drug.get("generic_name") if drug else None
        }


# Global Pharmacy Server instance
_pharmacy_server: Optional[PharmacyServer] = None


def get_pharmacy_server() -> PharmacyServer:
    """Get the global Pharmacy Server instance."""
    global _pharmacy_server
    if _pharmacy_server is None:
        _pharmacy_server = PharmacyServer()
    return _pharmacy_server
