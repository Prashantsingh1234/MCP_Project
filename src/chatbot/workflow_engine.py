"""Workflow engine implementing the full discharge flow."""

from __future__ import annotations

import time as _time
from typing import Any

from src.chatbot.mcp_client import MCPClient
from src.utils.langsmith_tracing import traceable_safe, process_inputs_workflow, process_outputs_workflow


class WorkflowEngine:
    @traceable_safe(
        name="WorkflowEngine.discharge",
        run_type="chain",
        process_inputs=process_inputs_workflow,
        process_outputs=process_outputs_workflow,
    )
    async def discharge(
        self,
        client: MCPClient,
        patient_id: str,
        *,
        include_invoice: bool,
        on_trace: Any = None,
        on_step: Any = None,
        step_counter: list[int] | None = None,
    ) -> dict[str, Any]:
        _counter = step_counter if step_counter is not None else [0]

        def _trace(server: str, tool: str, label: str = "") -> int:
            _counter[0] += 1
            _cur = _counter[0]
            if on_trace is not None:
                try:
                    on_trace({
                        "type": "trace",
                        "step": _cur,
                        "server": server,
                        "tool": tool,
                        "label": label,
                        "message": f"Calling {server} → {tool}" + (f" ({label})" if label else ""),
                    })
                except Exception:
                    pass
            return _cur

        def _step_done(step: int, server: str, tool: str, dur: float, ok: bool, err: str | None = None) -> None:
            if on_step is None:
                return
            entry: dict[str, Any] = {"step": step, "server": server, "tool": tool, "duration_ms": dur, "success": ok}
            if err:
                entry["error"] = err
            try:
                on_step(entry)
            except Exception:
                pass

        alerts: list[dict[str, Any]] = []
        substitutions: list[dict[str, Any]] = []
        conflicts: list[dict[str, Any]] = []

        _cur = _trace("EHR", "get_discharge_medications", patient_id)
        _t0 = _time.perf_counter()
        meds = await client.ehr_call("get_discharge_medications", {"patient_id": patient_id}, patient_id=patient_id)
        _step_done(_cur, "EHR", "get_discharge_medications", round((_time.perf_counter() - _t0) * 1000, 1), True)

        # Build a stock-check style summary for downstream formatting without re-calling tools.
        available: list[dict[str, Any]] = []
        unavailable: list[dict[str, Any]] = []
        alternatives_summary: list[dict[str, Any]] = []
        dose_conflicts: list[dict[str, Any]] = []
        name_mismatches: list[dict[str, Any]] = []

        pharmacy_results: list[dict[str, Any]] = []
        for med in meds:
            drug_query = med.get("brand") or med.get("drug_name")
            dose = med.get("dose")

            _cur = _trace("Pharmacy", "check_stock", str(drug_query or ""))
            _t0 = _time.perf_counter()
            stock = await client.pharmacy_call(
                "check_stock", {"drug_name": drug_query, "quantity": 1, "dose": dose}, patient_id=patient_id
            )
            _step_done(_cur, "Pharmacy", "check_stock", round((_time.perf_counter() - _t0) * 1000, 1), True)

            if not stock.get("found", True):
                alerts.append(
                    {"type": "DRUG_NOT_FOUND", "severity": "HIGH", "drug": drug_query, "message": "Drug not found"}
                )
                unavailable.append({"drug": drug_query, "dose": dose, "resolved_generic": None})
                alternatives_summary.append({"drug": drug_query, "suggested": None, "count": 0})
                pharmacy_results.append({"med": med, "stock": stock, "alternative": None})
                continue

            if stock.get("dose_conflict"):
                conflicts.append(
                    {
                        "type": "DOSE_CONFLICT",
                        "severity": "HIGH",
                        "drug": drug_query,
                        "detail": stock.get("dose_conflict_detail"),
                    }
                )
                dose_conflicts.append(
                    {
                        "drug": drug_query,
                        "detail": stock.get("dose_conflict_detail"),
                        "prescribed": dose,
                        "standard": None,
                    }
                )

            brand = med.get("brand")
            generic = stock.get("generic_name")
            if brand and generic and str(brand).lower() != str(generic).lower():
                name_mismatches.append({"from": brand, "to": generic})

            alternative = None
            if not stock.get("available"):
                _cur = _trace("Pharmacy", "get_alternative", str(drug_query or ""))
                _t0 = _time.perf_counter()
                alternative = await client.pharmacy_call("get_alternative", {"drug_name": drug_query}, patient_id=patient_id)
                _step_done(_cur, "Pharmacy", "get_alternative", round((_time.perf_counter() - _t0) * 1000, 1), True)
                alternatives = (alternative or {}).get("alternatives", [])
                if alternatives:
                    chosen = alternatives[0]
                    substitutions.append(
                        {
                            "from": drug_query,
                            "to": chosen.get("generic_name"),
                            "reason": "OUT_OF_STOCK",
                        }
                    )
                    alerts.append(
                        {
                            "type": "OUT_OF_STOCK",
                            "severity": "HIGH",
                            "drug": drug_query,
                            "message": f"{drug_query} was out of stock",
                        }
                    )
                    unavailable.append({"drug": drug_query, "dose": dose, "resolved_generic": stock.get("generic_name")})
                    alternatives_summary.append({"drug": drug_query, "suggested": chosen.get("generic_name"), "count": len(alternatives)})
                else:
                    alerts.append(
                        {
                            "type": "OUT_OF_STOCK_NO_ALTERNATIVE",
                            "severity": "HIGH",
                            "drug": drug_query,
                            "message": f"{drug_query} is unavailable and no alternative found",
                        }
                    )
                    unavailable.append({"drug": drug_query, "dose": dose, "resolved_generic": stock.get("generic_name")})
                    alternatives_summary.append({"drug": drug_query, "suggested": None, "count": 0})
            else:
                available.append(
                    {
                        "drug": drug_query,
                        "dose": dose,
                        "units_available": stock.get("stock_units"),
                        "resolved_generic": stock.get("generic_name"),
                    }
                )

            pharmacy_results.append({"med": med, "stock": stock, "alternative": alternative})

        _cur = _trace("EHR", "get_billing_safe_summary", patient_id)
        _t0 = _time.perf_counter()
        billing_safe = await client.ehr_call("get_billing_safe_summary", {"patient_id": patient_id}, patient_id=patient_id)
        _step_done(_cur, "EHR", "get_billing_safe_summary", round((_time.perf_counter() - _t0) * 1000, 1), True)

        invoice: dict[str, Any] | None = None
        if include_invoice:
            drug_charges: list[dict[str, Any]] = []
            for pr in pharmacy_results:
                med = pr["med"]
                stock = pr["stock"] or {}
                alt = pr.get("alternative") or {}
                qty = int(med.get("days_supply", 1))

                if not stock.get("found", True):
                    drug_charges.append({"total_price_inr": 0, "dispensing_fee": 0})
                    continue

                drug_name = stock.get("generic_name") or med.get("drug_name") or med.get("brand")
                if not stock.get("available"):
                    alternatives = alt.get("alternatives", [])
                    if alternatives:
                        drug_name = alternatives[0].get("generic_name") or drug_name

                _cur = _trace("Pharmacy", "get_price", str(drug_name or ""))
                _t0 = _time.perf_counter()
                try:
                    price = await client.pharmacy_call(
                        "get_price", {"drug_name": drug_name, "quantity": qty}, patient_id=patient_id
                    )
                    _step_done(_cur, "Pharmacy", "get_price", round((_time.perf_counter() - _t0) * 1000, 1), True)
                    drug_charges.append(
                        {
                            "total_price_inr": price.get("total_price_inr", 0),
                            "dispensing_fee": price.get("dispensing_fee", 0),
                        }
                    )
                except Exception:
                    _step_done(_cur, "Pharmacy", "get_price", round((_time.perf_counter() - _t0) * 1000, 1), False, "price_lookup_failed")
                    drug_charges.append({"total_price_inr": 0, "dispensing_fee": 0})
                    alerts.append(
                        {
                            "type": "PRICE_UNAVAILABLE",
                            "severity": "MEDIUM",
                            "drug": drug_name,
                            "message": f"Price lookup failed for {drug_name}; invoice uses 0 INR",
                        }
                    )

            _cur = _trace("Billing", "generate_invoice", patient_id)
            _t0 = _time.perf_counter()
            invoice = await client.billing_call(
                "generate_invoice",
                {"patient_id": patient_id, "billing_safe_ehr": billing_safe, "drug_charges": drug_charges},
                patient_id=patient_id,
            )
            _step_done(_cur, "Billing", "generate_invoice", round((_time.perf_counter() - _t0) * 1000, 1), True)

        return {
            "patient_id": patient_id,
            "medications": meds,
            "stock_check": {
                "available": available,
                "unavailable": unavailable,
                "alternatives": alternatives_summary,
                "dose_conflicts": dose_conflicts,
                "name_mismatches": name_mismatches,
            },
            "substitutions": substitutions,
            "alerts": alerts,
            "conflicts": conflicts,
            "invoice": invoice or {},
            "billing_safe_summary": billing_safe,
        }

    @traceable_safe(
        name="WorkflowEngine.get_medications",
        run_type="chain",
        process_inputs=process_inputs_workflow,
        process_outputs=process_outputs_workflow,
    )
    async def get_medications(self, client: MCPClient, patient_id: str) -> list[dict[str, Any]]:
        return await client.ehr_call("get_discharge_medications", {"patient_id": patient_id}, patient_id=patient_id)

    @traceable_safe(
        name="WorkflowEngine.check_stock_for_list",
        run_type="chain",
        process_inputs=process_inputs_workflow,
        process_outputs=process_outputs_workflow,
    )
    async def check_stock_for_list(self, client: MCPClient, patient_id: str, meds: list[dict[str, Any]]) -> dict[str, Any]:
        available: list[dict[str, Any]] = []
        unavailable: list[dict[str, Any]] = []
        alternatives: list[dict[str, Any]] = []
        dose_conflicts: list[dict[str, Any]] = []
        name_mismatches: list[dict[str, Any]] = []

        for med in meds:
            drug_query = med.get("brand") or med.get("drug_name")
            dose = med.get("dose")

            stock = await client.pharmacy_call(
                "check_stock",
                {"drug_name": drug_query, "quantity": 1, "dose": dose},
                patient_id=patient_id,
            )

            if not stock.get("found", True):
                unavailable.append({"drug": drug_query, "dose": dose, "resolved_generic": None})
                alternatives.append({"drug": drug_query, "suggested": None, "count": 0})
                continue

            if stock.get("dose_conflict"):
                dose_conflicts.append(
                    {
                        "drug": drug_query,
                        "detail": stock.get("dose_conflict_detail"),
                        "prescribed": dose,
                        "standard": None,
                    }
                )

            # Brand vs generic mismatch
            brand = med.get("brand")
            generic = stock.get("generic_name")
            if brand and generic and str(brand).lower() != str(generic).lower():
                name_mismatches.append({"from": brand, "to": generic})

            if stock.get("available"):
                available.append(
                    {
                        "drug": drug_query,
                        "dose": dose,
                        "units_available": stock.get("stock_units"),
                        "resolved_generic": stock.get("generic_name"),
                    }
                )
                continue

            unavailable.append({"drug": drug_query, "dose": dose, "resolved_generic": stock.get("generic_name")})
            alt = await client.pharmacy_call("get_alternative", {"drug_name": drug_query}, patient_id=patient_id)
            alts = (alt or {}).get("alternatives", [])
            if alts:
                alternatives.append({"drug": drug_query, "suggested": alts[0].get("generic_name"), "count": len(alts)})
            else:
                alternatives.append({"drug": drug_query, "suggested": None, "count": 0})

        return {
            "available": available,
            "unavailable": unavailable,
            "alternatives": alternatives,
            "dose_conflicts": dose_conflicts,
            "name_mismatches": name_mismatches,
        }

    @traceable_safe(
        name="WorkflowEngine.discharge_with_invoice",
        run_type="chain",
        process_inputs=process_inputs_workflow,
        process_outputs=process_outputs_workflow,
    )
    async def discharge_with_invoice(
        self,
        client: MCPClient,
        patient_id: str,
        *,
        on_trace: Any = None,
        on_step: Any = None,
        step_counter: list[int] | None = None,
    ) -> dict[str, Any]:
        return await self.discharge(
            client, patient_id,
            include_invoice=True,
            on_trace=on_trace,
            on_step=on_step,
            step_counter=step_counter,
        )
