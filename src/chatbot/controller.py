"""Controller: accepts user input, routes to workflows, formats response.

Supports multi-turn flows via an in-memory ConversationManager (non-PHI only):
- Fetch prescribed discharge medications for a patient
- Follow up to check stock for "these medicines" without repeating the patient id
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional

from src.chatbot.conversation_manager import ConversationManager
from src.chatbot.intent_classifier import classify_intent, is_prompt_injection
from src.chatbot.metrics import RequestMetrics
from src.chatbot.mcp_client import MCPClient, MCPServerURLs
from src.chatbot.phi_guard import PHIError, deny_if_phi_requested, strip_phi
from src.chatbot.rbac_guard import ActorContext, RBACGuard
from src.chatbot.response_formatter import (
    format_access_denied,
    format_medication_list,
    format_observability,
    format_phi_denied,
    format_stock_check_list,
    format_success_discharge,
)
from src.chatbot.validator import ValidationError, validate_message, resolves_to_previous_patient
from src.chatbot.workflow_engine import WorkflowEngine
from src.utils.telemetry import get_telemetry
from src.utils.exceptions import RBACError
from src.utils.exceptions import ToolExecutionError, MCPConnectionError
from src.utils.data_loader import get_data_loader
from src.utils.langsmith_tracing import (
    traceable_safe,
    process_inputs_controller,
    process_outputs_controller,
)


@dataclass
class ControllerResponse:
    success: bool
    answer: str
    data: Optional[dict[str, Any]] = None


class ChatController:
    def __init__(self):
        self.rbac_guard = RBACGuard()
        self.workflow = WorkflowEngine()
        self.conversations = ConversationManager()

        self.default_role = os.getenv("CHATBOT_DEFAULT_ROLE", "discharge_coordinator")
        self.allow_role_override = os.getenv("ALLOW_ROLE_OVERRIDE", "false").lower() == "true"

    def _actor(self, requested_role: Optional[str]) -> ActorContext:
        # NEVER trust user-provided role unless explicitly enabled (dev only)
        if self.allow_role_override and requested_role:
            return ActorContext(role=requested_role)
        return ActorContext(role=self.default_role)

    @traceable_safe(
        name="ChatController.handle_message",
        run_type="chain",
        process_inputs=process_inputs_controller,
        process_outputs=process_outputs_controller,
    )
    async def handle_message(
        self,
        user_text: str,
        requested_role: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> ControllerResponse:
        conv_key = (conversation_id or "").strip() or "default"
        state = self.conversations.get(conv_key)
        last_patient_id = state.last_patient_id

        intent = classify_intent(user_text)

        if intent == "invalid_input":
            return ControllerResponse(success=False, answer="Please provide a valid message.")

        # PHI request denial up-front (example-driven behavior)
        try:
            deny_if_phi_requested(user_text)
        except PHIError:
            return ControllerResponse(success=False, answer=format_phi_denied())

        if intent == "rbac_sensitive_request":
            if is_prompt_injection(user_text):
                return ControllerResponse(
                    success=False,
                    answer="Request denied.\n\nSystem security policies prevent access to restricted data.",
                )
            return ControllerResponse(success=False, answer=format_access_denied())

        actor = self._actor(requested_role)
        metrics = RequestMetrics()
        urls = MCPServerURLs()

        if intent == "observability_query":
            patient_ids = validate_message(user_text, "ambiguous_query").patient_ids
            pid = patient_ids[0] if patient_ids else None
            telem = get_telemetry()
            calls = telem.get_calls()
            if pid:
                calls = [c for c in calls if c.patient_id == pid]
            alerts = telem.get_alerts()
            if pid:
                alerts = [a for a in alerts if isinstance(a.details, dict) and a.details.get("patient_id") == pid]
            rbac_v = telem.get_rbac_violations()
            if pid:
                rbac_v = [v for v in rbac_v if v.get("patient_id") == pid]
            summary = {
                "total_calls": len(calls),
                "alerts": len(alerts),
                "rbac_violations": len(rbac_v),
            }
            return ControllerResponse(success=True, answer=format_observability(pid, summary), data=summary)

        try:
            validation = validate_message(user_text, intent)
        except ValidationError as exc:
            # Follow-up support: use last_patient_id when user references "first/previous/same patient"
            if last_patient_id and resolves_to_previous_patient(user_text) and "provide a valid patient id" in str(exc).lower():
                validation = validate_message(f"{user_text} {last_patient_id}", intent)
            else:
                return ControllerResponse(success=False, answer=str(exc))

        patient_id = validation.patient_ids[0] if validation.patient_ids else None
        metrics.patient_id = patient_id

        if patient_id and get_data_loader().get_patient(patient_id) is None:
            return ControllerResponse(success=False, answer="Patient not found. Please verify ID.")

        # --- Multi-turn medication flow ---
        if intent == "meds_fetch":
            if not patient_id:
                return ControllerResponse(success=False, answer="Please provide a valid patient ID to proceed.")
            try:
                async with MCPClient(urls, actor, metrics) as client:
                    meds = await self.workflow.get_medications(client, patient_id)
                # Store non-PHI state for follow-ups
                state.last_patient_id = patient_id
                state.medications = meds or []
                return ControllerResponse(
                    success=True,
                    answer=format_medication_list(patient_id, meds or []),
                    data={"patient_id": patient_id, "medications": meds or []},
                )
            except RBACError:
                return ControllerResponse(success=False, answer=format_access_denied())
            except (ToolExecutionError, MCPConnectionError) as exc:
                reason = exc.details.get("reason") if isinstance(exc, ToolExecutionError) else None
                return ControllerResponse(success=False, answer=reason or str(exc))

        if intent == "meds_stock_check":
            pid = patient_id or last_patient_id
            meds = state.medications

            # If user provides patient id but meds aren't cached (or patient changed), refresh first.
            if patient_id and (not meds or state.last_patient_id != patient_id):
                try:
                    async with MCPClient(urls, actor, metrics) as client:
                        meds = await self.workflow.get_medications(client, patient_id)
                    state.last_patient_id = patient_id
                    state.medications = meds or []
                    if not (meds or []):
                        return ControllerResponse(success=True, answer="No prescribed medications found for this patient.")
                except RBACError:
                    return ControllerResponse(success=False, answer=format_access_denied())
                except (ToolExecutionError, MCPConnectionError) as exc:
                    reason = exc.details.get("reason") if isinstance(exc, ToolExecutionError) else None
                    return ControllerResponse(success=False, answer=reason or str(exc))

            if not pid or not meds:
                return ControllerResponse(success=False, answer="Please provide a patient ID or medication list.")

            summary_only = any(k in (user_text or "").lower() for k in ["everything ok", "everything okay", "everything fine"])
            try:
                async with MCPClient(urls, actor, metrics) as client:
                    result = await self.workflow.check_stock_for_list(client, pid, meds)
                return ControllerResponse(
                    success=True,
                    answer=format_stock_check_list(pid, result, summary_only=summary_only),
                    data={"patient_id": pid, "stock_check": result},
                )
            except RBACError:
                return ControllerResponse(success=False, answer=format_access_denied())
            except (ToolExecutionError, MCPConnectionError) as exc:
                reason = exc.details.get("reason") if isinstance(exc, ToolExecutionError) else None
                return ControllerResponse(
                    success=False,
                    answer=reason or "Pharmacy service is temporarily unavailable. Please try again later.",
                )

        # --- Discharge / invoice workflow ---
        if intent in {"discharge_workflow", "invoice_generation"}:
            try:
                async with MCPClient(urls, actor, metrics) as client:
                    result = await self.workflow.discharge_with_invoice(client, patient_id)  # type: ignore[arg-type]
                    safe_data = strip_phi(result)
                    # Cache non-PHI medication list for follow-ups like "check these medicines"
                    state.last_patient_id = patient_id
                    state.medications = safe_data.get("medications", []) or []
                    return ControllerResponse(success=True, answer=format_success_discharge(safe_data), data=safe_data)
            except RBACError:
                return ControllerResponse(success=False, answer=format_access_denied())
            except (ToolExecutionError, MCPConnectionError) as exc:
                reason = exc.details.get("reason") if isinstance(exc, ToolExecutionError) else None
                return ControllerResponse(success=False, answer=reason or str(exc))

        if intent == "bulk_request":
            patient_ids = validation.patient_ids
            if not patient_ids:
                return ControllerResponse(success=False, answer="Please provide valid patient IDs like PAT-001.")

            async def _one(pid: str):
                m = RequestMetrics(patient_id=pid)
                async with MCPClient(urls, actor, m) as client:
                    r = await self.workflow.discharge_with_invoice(client, pid)
                    return {"patient_id": pid, "result": strip_phi(r)}

            results: list[dict[str, Any]] = []
            for pid in patient_ids:
                try:
                    results.append(await _one(pid))
                except Exception as exc:
                    results.append({"patient_id": pid, "error": str(exc)})

            answer_lines = ["Bulk request completed.\n"]
            for r in results:
                if r.get("error"):
                    answer_lines.append(f"✖ {r['patient_id']}: {r['error']}")
                else:
                    inv = r["result"]["invoice"]
                    answer_lines.append(f"✔ {r['patient_id']}: ₹{int(inv.get('subtotal_inr', 0)):,}")
            return ControllerResponse(success=True, answer="\n".join(answer_lines), data={"results": results})

        if intent == "stock_check":
            if not validation.drug_name:
                # Follow-up convenience: "check availability" after meds_fetch should use cached meds.
                if state.medications and (patient_id or last_patient_id):
                    pid = patient_id or last_patient_id
                    try:
                        async with MCPClient(urls, actor, metrics) as client:
                            result = await self.workflow.check_stock_for_list(client, pid, state.medications)  # type: ignore[arg-type]
                        return ControllerResponse(
                            success=True,
                            answer=format_stock_check_list(pid, result),
                            data={"patient_id": pid, "stock_check": result},
                        )
                    except RBACError:
                        return ControllerResponse(success=False, answer=format_access_denied())
                    except (ToolExecutionError, MCPConnectionError) as exc:
                        reason = exc.details.get("reason") if isinstance(exc, ToolExecutionError) else None
                        return ControllerResponse(
                            success=False,
                            answer=reason or "Pharmacy service is temporarily unavailable. Please try again later.",
                        )
                return ControllerResponse(success=False, answer="Include a drug name to check stock.")
            async with MCPClient(urls, actor, metrics) as client:
                stock = await client.pharmacy_call(
                    "check_stock",
                    {"drug_name": validation.drug_name, "quantity": 1, "dose": validation.dose},
                )

                if stock.get("dose_conflict"):
                    # Example-driven formatting
                    detail = stock.get("dose_conflict_detail") or ""
                    prescribed = None
                    standard = None
                    import re

                    m = re.search(r"prescribed\s+(\d+(?:\.\d+)?mg).*standard\s+(.+)$", detail, re.IGNORECASE)
                    if m:
                        prescribed = m.group(1)
                        standard = m.group(2).strip()
                    return ControllerResponse(
                        success=True,
                        answer=(
                            "Dose conflict detected.\n\n"
                            + (f"Prescribed: {prescribed}\nStandard: {standard}\n\n" if prescribed and standard else "")
                            + "Clinical review required"
                        ),
                        data={"stock": stock},
                    )

                generic = stock.get("generic_name")
                mapping = ""
                if generic and generic.lower() != validation.drug_name.lower():
                    mapping = f"Drug identified: {validation.drug_name} → {generic}\n"

                if stock.get("available"):
                    return ControllerResponse(success=True, answer=mapping + "✔ Available in stock", data={"stock": stock})

                # Out of stock / unavailable: try alternatives
                alt = await client.pharmacy_call("get_alternative", {"drug_name": validation.drug_name})
                alternatives = (alt or {}).get("alternatives", [])
                if not alternatives:
                    return ControllerResponse(
                        success=True,
                        answer=f"{validation.drug_name} is currently unavailable.\n\n⚠ No suitable alternative found\nEscalation required",
                        data={"stock": stock, "alternative": alt},
                    )
                return ControllerResponse(
                    success=True,
                    answer=(
                        f"{validation.drug_name} is currently unavailable.\n"
                        f"✔ Alternative available: {alternatives[0].get('generic_name')}"
                    ),
                    data={"stock": stock, "alternative": alt},
                )

        return ControllerResponse(success=False, answer="Ambiguous query. Try: discharge PAT-001, generate invoice, check stock for Humira.")
