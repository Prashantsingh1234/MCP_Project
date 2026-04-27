"""LLM-driven chatbot controller (replaces rule-based routing).

This controller:
- enforces PHI request denial and prompt injection defense up-front
- uses an LLM tool-calling agent to decide which MCP tools to call
- executes MCP calls with RBAC enforced by MCPClient
- maintains multi-turn memory keyed by conversation_id
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional

from src.chatbot.conversation_manager import ConversationManager
from src.chatbot.intent_classifier import is_prompt_injection
from src.chatbot.llm_agent import LLMToolCallingAgent
from src.chatbot.llm_provider import is_configured as llm_configured
from src.chatbot.mcp_client import MCPClient, MCPServerURLs
from src.chatbot.metrics import RequestMetrics
from src.chatbot.phi_guard import PHIError, deny_if_phi_requested, strip_phi
from src.chatbot.preprocessor import preprocess_user_text, sanitize_user_text
from src.chatbot.rbac_guard import ActorContext
from src.chatbot.response_formatter import (
    format_medication_list,
    format_medication_lists,
    format_all_patients_report,
    format_stock_check_list,
    format_success_discharge,
    format_unavailable_only,
    format_discharge_summary_safe,
)
from src.chatbot.validator import (
    extract_drug_name,
    extract_patient_ids,
    extract_patient_ordinal,
    patient_id_from_ordinal,
    resolves_to_previous_patient,
)
from src.chatbot.workflow_engine import WorkflowEngine
from src.utils.exceptions import RBACError, ToolExecutionError, MCPConnectionError
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


class LLMChatController:
    def __init__(self):
        self.conversations = ConversationManager()
        self.agent = LLMToolCallingAgent()
        self.workflow = WorkflowEngine()

        self.default_role = os.getenv("CHATBOT_DEFAULT_ROLE", "discharge_coordinator")

    @traceable_safe(
        name="LLMChatController._fallback",
        run_type="chain",
        process_inputs=process_inputs_controller,
        process_outputs=process_outputs_controller,
    )
    async def _fallback(self, *, user_text: str, state, client: MCPClient) -> ControllerResponse:
        """Deterministic fallback for core workflows when the LLM times out.

        This preserves the existing architecture by using the same MCP client
        (RBAC enforced) and only triggers for high-confidence user intents.
        """
        t = (user_text or "").lower()
        patient_ids = extract_patient_ids(user_text)
        meds_keywords = [
            "prescribed",
            "what medicines",
            "what medications",
            "show medicines",
            "show medications",
            "check medicines",
            "check medications",
            "medicines for",
            "medications for",
            "discharge medications",
            "discharge medicines",
        ]

        def _normalize_patient_ids(raw: Any) -> list[str]:
            """Best-effort normalize patient IDs from various tool return shapes."""
            out: list[str] = []

            def _add(val: Any) -> None:
                try:
                    s = str(val or "").strip()
                except Exception:
                    return
                if not s:
                    return
                ids = extract_patient_ids(s)
                if ids:
                    for pid in ids:
                        up = str(pid).strip().upper()
                        if up and up not in out:
                            out.append(up)
                    return
                up = s.upper()
                if up.startswith("PAT-") and up not in out:
                    out.append(up)

            if isinstance(raw, list):
                for item in raw:
                    if isinstance(item, dict):
                        _add(item.get("patient_id") or item.get("id"))
                    else:
                        _add(item)
                return out

            if isinstance(raw, dict):
                for k in ["patient_ids", "patients", "result", "data", "ehr_patients"]:
                    v = raw.get(k)
                    if isinstance(v, list):
                        for item in v:
                            if isinstance(item, dict):
                                _add(item.get("patient_id") or item.get("id"))
                            else:
                                _add(item)
                        if out:
                            return out
                if raw.get("patient_id"):
                    _add(raw.get("patient_id"))
                return out

            _add(raw)
            return out

        # 0) Report for all patients: discharge summary + invoice (PHI omitted).
        if any(k in t for k in ["report for all patients", "generate report for all patients", "all patients report"]):
            raw = await client.ehr_call("list_patients", {}, patient_id=None)
            pids = _normalize_patient_ids(raw)
            # Hard cap to avoid very large runs in demo mode.
            if len(pids) > 25:
                return ControllerResponse(
                    success=True,
                    answer=(
                        f"I found {len(pids)} patients. For performance, please specify a smaller set of patient IDs "
                        f"(up to 25) for the report."
                    ),
                    data={"needs_clarification": True, "clarification_type": "report_too_many_patients", "patient_count": len(pids)},
                )
            if not pids:
                # Last-resort fallback: use patient IDs observed recently in this chat.
                pids = list(state.patient_history or [])
            if not pids:
                return ControllerResponse(success=True, answer="No patients found.", data={"patients": []})

            sections: dict[str, str] = {}
            reports_data: dict[str, Any] = {}
            for pid in pids:
                discharge = await self.workflow.discharge(client, pid, include_invoice=True)
                safe = strip_phi(discharge)
                admission = await client.ehr_call("get_admission_info", {"patient_id": pid}, patient_id=pid)
                diagnosis = await client.ehr_call("get_diagnosis_codes", {"patient_id": pid}, patient_id=pid)

                sections[pid] = format_discharge_summary_safe(
                    pid,
                    admission_info=admission if isinstance(admission, dict) else None,
                    diagnosis_codes=diagnosis if isinstance(diagnosis, dict) else None,
                    medications=list(safe.get("medications") or []),
                    stock_check=safe.get("stock_check") if isinstance(safe.get("stock_check"), dict) else None,
                    substitutions=list(safe.get("substitutions") or []),
                    alerts=list(safe.get("alerts") or []),
                    conflicts=list(safe.get("conflicts") or []),
                    billing_safe_summary=safe.get("billing_safe_summary") if isinstance(safe.get("billing_safe_summary"), dict) else None,
                    invoice=safe.get("invoice") if isinstance(safe.get("invoice"), dict) else None,
                )

                reports_data[pid] = {
                    "patient_id": pid,
                    "invoice": safe.get("invoice") or {},
                    "alerts": safe.get("alerts") or [],
                    "conflicts": safe.get("conflicts") or [],
                    "substitutions": safe.get("substitutions") or [],
                }

            # Cache last patient context for follow-ups.
            state.last_patient_id = pids[-1]
            try:
                for pid in pids:
                    if pid and pid not in (state.patient_history or []):
                        state.patient_history.append(pid)
                if len(state.patient_history) > 20:
                    state.patient_history = state.patient_history[-20:]
            except Exception:
                pass

            return ControllerResponse(
                success=True,
                answer=format_all_patients_report(sections),
                data={"patients": pids, "reports_by_patient": reports_data},
            )

        # 0b) Invoice for all patients (PHI omitted).
        if any(k in t for k in ["invoice for all patients", "generate invoice for all patients", "invoices for all patients", "bill for all patients", "generate bill for all patients"]):
            raw = await client.ehr_call("list_patients", {}, patient_id=None)
            pids = _normalize_patient_ids(raw)
            if len(pids) > 25:
                return ControllerResponse(
                    success=True,
                    answer=(
                        f"I found {len(pids)} patients. For performance, please specify a smaller set of patient IDs "
                        f"(up to 25) to generate invoices."
                    ),
                    data={"needs_clarification": True, "clarification_type": "invoice_too_many_patients", "patient_count": len(pids)},
                )
            if not pids:
                pids = list(state.patient_history or [])
            if not pids:
                return ControllerResponse(success=True, answer="No patients found.", data={"patients": []})

            invoices_by_patient: dict[str, Any] = {}
            lines: list[str] = ["Invoices generated (PHI omitted).\n"]
            lines.append("Use the invoice buttons to download the PDF or preview each invoice.\n")
            for pid in pids:
                discharge = await self.workflow.discharge(client, pid, include_invoice=True)
                safe = strip_phi(discharge)
                inv = safe.get("invoice") if isinstance(safe.get("invoice"), dict) else {}
                invoices_by_patient[pid] = inv or {}
                total = inv.get("subtotal_inr") or inv.get("subtotal")
                if total is not None:
                    lines.append(f"- {pid}: total INR {total}")
                else:
                    lines.append(f"- {pid}: invoice ready")

            state.last_patient_id = pids[-1]
            try:
                for pid in pids:
                    if pid and pid not in (state.patient_history or []):
                        state.patient_history.append(pid)
                if len(state.patient_history) > 20:
                    state.patient_history = state.patient_history[-20:]
            except Exception:
                pass

            return ControllerResponse(
                success=True,
                answer="\n".join(lines).strip(),
                data={"patients": pids, "invoices_by_patient": invoices_by_patient},
            )

        def _resolve_pid_from_context(text: str) -> Optional[str]:
            if patient_ids:
                return patient_ids[0]

            n = extract_patient_ordinal(text)
            if n:
                return patient_id_from_ordinal(n)

            t2 = (text or "").lower()
            if ("second patient" in t2 or "the second patient" in t2 or "2nd patient" in t2) and len(state.patient_history or []) >= 2:
                return state.patient_history[1]
            if ("first patient" in t2 or "the first patient" in t2) and (state.patient_history or []):
                return state.patient_history[0]
            if resolves_to_previous_patient(text):
                return state.last_patient_id
            if any(k in t2 for k in ["his ", "her ", "their ", "that one", "the patient", "this patient", "that patient"]):
                return state.last_patient_id
            return state.last_patient_id

        pid = _resolve_pid_from_context(user_text)
        if pid and not patient_ids and resolves_to_previous_patient(user_text):
            # Update memory so subsequent turns resolve correctly.
            state.last_patient_id = pid

        # 0) Safe discharge summary (detailed, PHI omitted)
        if pid and any(k in t for k in ["discharge summary", "discharge details", "discharge report", "summary of discharge"]):
            want_invoice = any(k in t for k in ["invoice", "bill", "generate invoice", "create invoice", "make invoice"])

            # Run a unified discharge collection so we can summarize meds + blockers consistently.
            discharge = await self.workflow.discharge(client, pid, include_invoice=want_invoice)
            safe_data = strip_phi(discharge)

            admission = await client.ehr_call("get_admission_info", {"patient_id": pid}, patient_id=pid)
            diagnosis = await client.ehr_call("get_diagnosis_codes", {"patient_id": pid}, patient_id=pid)

            # Cache state for follow-ups
            state.last_patient_id = pid
            state.medications = list(safe_data.get("medications") or [])
            if isinstance(safe_data.get("billing_safe_summary"), dict):
                state.billing_safe_summary = dict(safe_data.get("billing_safe_summary") or {})
            if want_invoice and isinstance(safe_data.get("invoice"), dict):
                state.last_invoice = dict(safe_data.get("invoice") or {})

            try:
                state.last_discharge_context = {
                    "patient_id": pid,
                    "alerts": list(safe_data.get("alerts") or []),
                    "conflicts": list(safe_data.get("conflicts") or []),
                    "substitutions": list(safe_data.get("substitutions") or []),
                    "invoice_generated": bool(safe_data.get("invoice")),
                }
            except Exception:
                pass

            answer = format_discharge_summary_safe(
                pid,
                admission_info=admission if isinstance(admission, dict) else None,
                diagnosis_codes=diagnosis if isinstance(diagnosis, dict) else None,
                medications=safe_data.get("medications") if isinstance(safe_data.get("medications"), list) else None,
                stock_check=safe_data.get("stock_check") if isinstance(safe_data.get("stock_check"), dict) else None,
                substitutions=list(safe_data.get("substitutions") or []),
                alerts=list(safe_data.get("alerts") or []),
                conflicts=list(safe_data.get("conflicts") or []),
                billing_safe_summary=safe_data.get("billing_safe_summary") if isinstance(safe_data.get("billing_safe_summary"), dict) else None,
                invoice=safe_data.get("invoice") if (want_invoice and isinstance(safe_data.get("invoice"), dict)) else None,
            )

            data: dict[str, Any] = {"patient_id": pid, "discharge_summary_safe": True}
            if want_invoice and isinstance(safe_data.get("invoice"), dict):
                data["invoice"] = safe_data.get("invoice")
            return ControllerResponse(success=True, answer=answer, data=data)

        # 1) Discharge + invoice, or any explicit invoice generation request
        _invoice_phrases = ["generate invoice", "create invoice", "make invoice", "get invoice", "invoice for", "billing invoice"]
        _is_invoice_request = (
            (("discharge" in t or "complete discharge" in t) and ("invoice" in t or "bill" in t))
            or any(k in t for k in _invoice_phrases)
        )
        if pid and _is_invoice_request:
            result = await self.workflow.discharge(client, pid, include_invoice=True)
            safe_data = strip_phi(result)
            # Cache state for multi-turn follow-ups
            state.last_patient_id = pid
            state.medications = list(safe_data.get("medications") or [])
            if isinstance(safe_data.get("billing_safe_summary"), dict):
                state.billing_safe_summary = dict(safe_data.get("billing_safe_summary") or {})
            if isinstance(safe_data.get("invoice"), dict):
                state.last_invoice = dict(safe_data.get("invoice") or {})
            try:
                state.last_discharge_context = {
                    "patient_id": pid,
                    "alerts": list(safe_data.get("alerts") or []),
                    "conflicts": list(safe_data.get("conflicts") or []),
                    "substitutions": list(safe_data.get("substitutions") or []),
                    "invoice_generated": bool(safe_data.get("invoice")),
                }
            except Exception:
                pass
            return ControllerResponse(success=True, answer=format_success_discharge(safe_data), data=safe_data)

        # 1a) Blockers summary should come from chat context (avoid re-running discharge).
        if "blocker" in t and "discharge" in t:
            ctx = getattr(state, "last_discharge_context", None) or {}
            if isinstance(ctx, dict) and ctx.get("patient_id"):
                alerts = list(ctx.get("alerts") or [])
                conflicts = list(ctx.get("conflicts") or [])
                subs = list(ctx.get("substitutions") or [])
                blockers: list[str] = []
                for a in alerts:
                    if not isinstance(a, dict):
                        continue
                    typ = a.get("type") or "ALERT"
                    drug = a.get("drug")
                    msg = a.get("message")
                    if drug and msg:
                        blockers.append(f"- {drug}: {msg} ({typ})")
                    elif drug:
                        blockers.append(f"- {drug}: {typ}")
                    elif msg:
                        blockers.append(f"- {msg}")
                for c in conflicts:
                    if not isinstance(c, dict):
                        continue
                    drug = c.get("drug") or "Medication"
                    detail = c.get("detail") or "Clinical review required"
                    blockers.append(f"- {drug}: {detail} (DOSE_CONFLICT)")
                if not blockers and subs:
                    for s in subs:
                        if isinstance(s, dict) and s.get("from") and s.get("to"):
                            blockers.append(f"- {s.get('from')} replaced with {s.get('to')} (verify with prescribing doctor)")

                if blockers:
                    return ControllerResponse(
                        success=True,
                        answer=f"Blockers for discharge (based on this chat for {ctx.get('patient_id')}):\n\n" + "\n".join(blockers),
                        data={"contextual_answer": True, "patient_id": ctx.get("patient_id")},
                    )
                return ControllerResponse(
                    success=True,
                    answer=f"No blockers recorded in this chat for {ctx.get('patient_id')}.",
                    data={"contextual_answer": True, "patient_id": ctx.get("patient_id")},
                )
            return ControllerResponse(
                success=True,
                answer="Which patient is this for? Please provide a patient ID like PAT-001.",
                data={"needs_clarification": True, "clarification_type": "blockers_patient"},
            )

        # 1b) Discharge (without explicit invoice) should still be tool-backed so it appears in telemetry.
        _is_discharge_request = ("discharge" in t or "complete discharge" in t)
        if pid and _is_discharge_request:
            result = await self.workflow.discharge(client, pid, include_invoice=False)
            safe_data = strip_phi(result)
            state.last_patient_id = pid
            state.medications = list(safe_data.get("medications") or [])
            if isinstance(safe_data.get("billing_safe_summary"), dict):
                state.billing_safe_summary = dict(safe_data.get("billing_safe_summary") or {})
            try:
                state.last_discharge_context = {
                    "patient_id": pid,
                    "alerts": list(safe_data.get("alerts") or []),
                    "conflicts": list(safe_data.get("conflicts") or []),
                    "substitutions": list(safe_data.get("substitutions") or []),
                    "invoice_generated": bool(safe_data.get("invoice")),
                }
            except Exception:
                pass
            return ControllerResponse(success=True, answer=format_success_discharge(safe_data), data=safe_data)

        # 2) Fetch meds
        if patient_ids and len(patient_ids) >= 2 and any(k in t for k in meds_keywords):
            # Multi-patient request: fetch medication lists efficiently in one round-trip per patient.
            pids: list[str] = []
            for p in patient_ids:
                up = str(p or "").strip().upper()
                if up and up not in pids:
                    pids.append(up)

            meds_by_patient: dict[str, list[dict[str, Any]]] = {}
            # NOTE: The underlying SSE client is not concurrency-safe across tasks.
            # Fetch sequentially to avoid anyio cancel-scope exit errors.
            for p in pids:
                try:
                    meds_by_patient[p] = list(await self.workflow.get_medications(client, p) or [])
                except Exception:
                    meds_by_patient[p] = []

            # Cache last patient + meds for follow-ups.
            state.last_patient_id = pids[-1] if pids else state.last_patient_id
            state.medications = meds_by_patient.get(state.last_patient_id or "", []) or []
            try:
                for p in pids:
                    if p and p not in (state.patient_history or []):
                        state.patient_history.append(p)
                if len(state.patient_history) > 20:
                    state.patient_history = state.patient_history[-20:]
            except Exception:
                pass

            return ControllerResponse(
                success=True,
                answer=format_medication_lists(meds_by_patient),
                data={"patients": pids, "medications_by_patient": meds_by_patient},
            )

        if pid and any(k in t for k in meds_keywords):
            meds = await self.workflow.get_medications(client, pid)
            state.last_patient_id = pid
            state.medications = meds or []
            return ControllerResponse(success=True, answer=format_medication_list(pid, meds or []), data={"patient_id": pid, "medications": meds or []})

        # 3) Follow-up stock check for cached meds
        _unavailable_phrases = [
            "not in stock",
            "out of stock",
            "not available",
            "unavailable",
            "which are not in stock",
            "which medicines are not in stock",
            "which medications are not in stock",
        ]
        if any(k in t for k in _unavailable_phrases):
            meds = state.medications or []
            if pid and meds:
                result = await self.workflow.check_stock_for_list(client, pid, meds)
                return ControllerResponse(success=True, answer=format_unavailable_only(pid, result), data={"patient_id": pid, "stock_check": result})

        if any(k in t for k in ["check availability", "check if these", "check these", "check them", "these medicines", "these medications", "availability summary", "full availability"]):
            meds = state.medications or []
            if pid and meds:
                result = await self.workflow.check_stock_for_list(client, pid, meds)
                return ControllerResponse(success=True, answer=format_stock_check_list(pid, result), data={"patient_id": pid, "stock_check": result})
            # No cached context to act on; ask a clarifying question instead of timing out.
            return ControllerResponse(
                success=True,
                answer=(
                    "What availability should I check?\n\n"
                    "- A specific drug (e.g., \"Check stock for Dapagliflozin\")\n"
                    "- A patient's discharge medicines (e.g., \"Check availability for PAT-001\")\n"
                    "- All in-stock medicines (e.g., \"List all medicines in stock\")"
                ),
                data={"needs_clarification": True, "clarification_type": "availability"},
            )

        # 3c) Patient-specific medication availability check
        # Handles: "which medicines of PAT-003 are available" / "which of those medicines are in pharmacy"
        # Must fire BEFORE the global stock-list section below.
        _refers_to_patient_meds = (
            bool(patient_ids)  # explicit "PAT-003" in text (incl. after ordinal resolution)
            or bool(extract_patient_ordinal(user_text))  # "third patient", "4th patient", etc.
            or any(ref in t for ref in [
                "his medicine", "her medicine", "their medicine",
                "his medication", "her medication", "their medication",
                "these medicine", "these medication",
            ])
        )
        _availability_terms = [
            "available", "availability", "in pharmacy", "which medicine",
            "which medication", "which drug", "medicines available", "medications available",
            "are available", "is available",
        ]
        if _refers_to_patient_meds and any(k in t for k in _availability_terms):
            if not pid:
                return ControllerResponse(
                    success=False,
                    answer="Please specify a patient ID (e.g., PAT-001) to check medication availability.",
                )
            meds = state.medications or []
            if not meds:
                meds = await self.workflow.get_medications(client, pid)
                state.last_patient_id = pid
                state.medications = meds or []
            if meds:
                result = await self.workflow.check_stock_for_list(client, pid, meds)
                return ControllerResponse(
                    success=True,
                    answer=format_stock_check_list(pid, result),
                    data={"patient_id": pid, "stock_check": result},
                )
            return ControllerResponse(
                success=True,
                answer=(
                    f"❌ Not Found: Discharge Medications\n\n"
                    f"No discharge medications were found for {pid}.\n\n"
                    f"Possible reasons:\n"
                    f"* Patient may not have a discharge prescription yet\n"
                    f"* Patient ID may be incorrect\n\n"
                    f"Suggested actions:\n"
                    f"* Verify the patient ID format (e.g., PAT-001)\n"
                    f"* Ask the prescribing physician to enter discharge medications"
                ),
                data={"patient_id": pid, "medications": []},
            )

        # Replacement for an unavailable drug (multi-turn safe). Avoid matching "unavailable drug" → "available drug" list branch.
        _replacement_keywords2 = ["replacement", "replace", "substitute", "alternative", "alternatives"]
        _unavailable_ctx2 = any(k in t for k in ["unavailable", "out of stock", "not available"])
        if any(k in t for k in _replacement_keywords2) and (_unavailable_ctx2 or getattr(state, "last_unavailable_drug_name", None)):
            drug = (
                extract_drug_name(user_text)
                or getattr(state, "last_unavailable_drug_name", None)
                or getattr(state, "last_drug_name", None)
            )
            if drug:
                alt = await client.pharmacy_call("get_alternative", {"drug_name": drug}, patient_id=pid)
                alternatives = (alt or {}).get("alternatives", []) if isinstance(alt, dict) else []
                if not alternatives:
                    return ControllerResponse(
                        success=True,
                        answer=(
                            f"{drug} is not available.\n\nâš  No alternative found\n"
                            "Please consult your doctor to re-prescribe medication."
                        ),
                        data={"drug_name": drug, "alternative": alt, "patient_id": pid},
                    )
                suggested = alternatives[0].get("generic_name") if isinstance(alternatives[0], dict) else str(alternatives[0])
                return ControllerResponse(
                    success=True,
                    answer=(
                        f"{drug} is not available.\n\nSuggested Alternative:\n\n* {suggested}\n\n"
                        "Please consult your doctor before switching medications."
                    ),
                    data={"drug_name": drug, "alternative": alt, "patient_id": pid},
                )
            return ControllerResponse(
                success=True,
                answer="Which medicine should I find an alternative for? (Example: \"Get alternative for Dapagliflozin\")",
                data={"needs_clarification": True, "clarification_type": "alternative"},
            )

        # 4a) Urgent + unavailable, but missing identifiers: provide guidance + ask for specifics.
        if any(k in t for k in ["urgent", "urgently", "asap", "immediately"]) and any(
            k in t for k in ["not available", "out of stock", "unavailable", "not in stock"]
        ):
            drug_hint = extract_drug_name(user_text) or getattr(state, "last_unavailable_drug_name", None) or getattr(state, "last_drug_name", None)
            if not drug_hint and not pid:
                return ControllerResponse(
                    success=True,
                    answer=(
                        "I can help—this sounds urgent.\n\n"
                        "To act quickly, tell me:\n"
                        "- Which medicine (name)\n"
                        "- Which patient (PAT-XXX), if this is for a discharge prescription\n\n"
                        "If you already know the medicine, ask:\n"
                        "- \"Get alternative for <medicine>\" or \"Check nearby availability for <medicine>\""
                    ),
                    data={"needs_clarification": True, "clarification_type": "urgent_unavailable"},
                )

        # 4b) "Which medicines require doctor consultation?" should use this chat's context (not list all stock).
        if any(k in t for k in ["doctor consultation", "consult your doctor", "consultation required", "requires doctor", "need doctor"]):
            last_checks = getattr(state, "last_stock_check", None) or {}
            consult: list[str] = []
            if isinstance(last_checks, dict):
                for drug_name, stock in last_checks.items():
                    if not isinstance(stock, dict):
                        continue
                    if stock.get("dose_conflict"):
                        consult.append(f"- {drug_name}: dose mismatch detected (clinical review required)")
                        continue
                    if stock.get("available") is False:
                        consult.append(f"- {drug_name}: not available (may need re-prescription / alternative)")
                        continue

            if not consult and getattr(state, "last_unavailable_drug_name", None):
                consult.append(
                    f"- {state.last_unavailable_drug_name}: not available (may need re-prescription / alternative)"
                )

            if consult:
                return ControllerResponse(
                    success=True,
                    answer=(
                        "Based on what we checked in this chat, doctor consultation is recommended for:\n\n"
                        + "\n".join(consult)
                        + "\n\nIf you want, ask: \"Get alternative for <medicine>\" or \"Check nearby availability for <medicine>\"."
                    ),
                    data={"contextual_answer": True},
                )

            return ControllerResponse(
                success=True,
                answer=(
                    "Do you mean medicines from this chat (the ones we already checked), or a specific patient’s discharge medicines?\n\n"
                    "- If it’s a specific drug, ask: \"Check stock for <drug>\" then \"Do I need doctor consultation?\"\n"
                    "- If it’s a patient, ask: \"Check availability for PAT-001\""
                ),
                data={"needs_clarification": True, "clarification_type": "doctor_consultation_scope"},
            )

        # 4) List all in-stock drugs (no patient ID needed)
        # IMPORTANT: avoid false matches like "unavailable drug" containing "available drug"
        # which would otherwise trigger the stock-list branch incorrectly.
        if "unavailable" not in t and "out of stock" not in t and "not available" not in t:
            _replacement_keywords = ["replacement", "replace", "substitute", "alternative", "alternatives"]
            if any(k in t for k in _replacement_keywords):
                drug = extract_drug_name(user_text) or getattr(state, "last_unavailable_drug_name", None) or getattr(state, "last_drug_name", None)
                if drug:
                    alt = await client.pharmacy_call("get_alternative", {"drug_name": drug}, patient_id=pid)
                    alternatives = (alt or {}).get("alternatives", []) if isinstance(alt, dict) else []
                    if not alternatives:
                        return ControllerResponse(
                            success=True,
                            answer=(
                                f"{drug} is not available.\n\n⚠ No alternative found\n"
                                "Please consult your doctor to re-prescribe medication."
                            ),
                            data={"drug_name": drug, "alternative": alt, "patient_id": pid},
                        )
                    suggested = alternatives[0].get("generic_name") if isinstance(alternatives[0], dict) else str(alternatives[0])
                    return ControllerResponse(
                        success=True,
                        answer=(
                            f"{drug} is not available.\n\nSuggested Alternative:\n\n* {suggested}\n\n"
                            "Please consult your doctor before switching medications."
                        ),
                        data={"drug_name": drug, "alternative": alt, "patient_id": pid},
                    )

        _in_stock_keywords = ["list all", "show all", "all medication", "all medicine", "all drug",
                              "what medication", "what medicine", "what drug", "which medication",
                              "which medicine", "which drug", "medications in stock", "medicines in stock",
                              "drugs in stock", "in stock medication", "in stock medicine", "in stock drug",
                              "available medication", "available medicine", "available drug",
                              "currently available", "what is available", "what are available",
                              "stock list", "formulary"]
        # Global stock list is not patient-specific; skip if user is asking about a specific patient's meds.
        if (
            not _refers_to_patient_meds
            and any(k in t for k in _in_stock_keywords)
            and not any(k in t for k in ["unavailable", "out of stock", "not available", "doctor", "consult"])
        ):
            drugs = await client.pharmacy_call("list_in_stock_drugs", {}, patient_id=None)
            # Defensive normalization: tolerate SDK/tool implementations that return a JSON string
            # or a list of strings instead of list[dict].
            if isinstance(drugs, dict) and "drugs" in drugs:
                drugs = drugs.get("drugs")
            if isinstance(drugs, str):
                try:
                    import json

                    drugs = json.loads(drugs)
                except Exception:
                    drugs = [drugs]
            if drugs is None:
                drugs = []
            if not drugs:
                return ControllerResponse(success=True, answer="No medications are currently in stock.", data={"drugs": []})
            lines = ["Available medications in pharmacy stock:\n"]
            for d in drugs:
                if isinstance(d, str):
                    lines.append(f"* {d}")
                    continue
                if not isinstance(d, dict):
                    lines.append(f"* {str(d)}")
                    continue
                name = d.get("generic_name", "Unknown")
                brands = d.get("brand_names") or []
                brand_str = f" ({', '.join(brands)})" if brands else ""
                strengths = d.get("strengths") or []
                strength_str = f" — {', '.join(strengths)}" if strengths else ""
                units = d.get("stock_units", 0)
                controlled = " [Controlled]" if d.get("controlled_substance") else ""
                specialty = " [Specialty]" if d.get("specialty_drug") else ""
                lines.append(f"* {name}{brand_str}{strength_str} — {units} units{controlled}{specialty}")
            return ControllerResponse(
                success=True,
                answer="\n".join(lines),
                data={"drugs": drugs, "count": len(drugs) if isinstance(drugs, list) else 0},
            )

        # 5) Specific drug query
        drug = extract_drug_name(user_text)

        # Follow-up: "how many units/stocks are available" should resolve to the last checked drug when omitted.
        _qty_phrases = ["how many units", "how many unit", "how many stocks", "how many stock", "units available", "stock available"]
        if any(k in t for k in _qty_phrases) and not drug:
            drug = getattr(state, "last_drug_name", None) or None
        if drug and any(k in t for k in ["available", "in stock", "check", "stock", "units"]):
            state.last_drug_name = drug
            stock = await client.pharmacy_call("check_stock", {"drug_name": drug, "quantity": 1}, patient_id=pid)
            try:
                if isinstance(stock, dict) and stock.get("available") is False:
                    state.last_unavailable_drug_name = drug
            except Exception:
                pass
            if any(k in t for k in _qty_phrases):
                units = stock.get("stock_units")
                if stock.get("available") and units is not None:
                    return ControllerResponse(
                        success=True,
                        answer=f"{drug} stock available: {int(units)} units.",
                        data={"stock": stock, "patient_id": pid, "drug_name": drug},
                    )
            if stock.get("dose_conflict"):
                return ControllerResponse(success=True, answer="⚠ Dose mismatch detected. Clinical review required.", data={"stock": stock, "patient_id": pid})
            if stock.get("available"):
                generic = stock.get("generic_name")
                mapping = f"{drug} ({generic}):\n\n✔ Available in stock" if generic and str(generic).lower() != drug.lower() else f"{drug}:\n\n✔ Available in stock"
                return ControllerResponse(success=True, answer=mapping, data={"stock": stock, "patient_id": pid})
            alt = await client.pharmacy_call("get_alternative", {"drug_name": drug}, patient_id=pid)
            alternatives = (alt or {}).get("alternatives", []) if isinstance(alt, dict) else []
            if not alternatives:
                return ControllerResponse(
                    success=True,
                    answer=f"{drug} is not available.\n\n⚠ No alternative found\nPlease consult your doctor to re-prescribe medication.",
                    data={"stock": stock, "alternative": alt, "patient_id": pid},
                )
            suggested = alternatives[0].get("generic_name") if isinstance(alternatives[0], dict) else None
            return ControllerResponse(
                success=True,
                answer=f"{drug} is not available.\n\nSuggested Alternative:\n\n* {suggested}\n\nPlease consult your doctor before switching medications.",
                data={"stock": stock, "alternative": alt, "patient_id": pid},
            )

        return ControllerResponse(success=False, answer="Request timed out. Please try again with a patient ID (PAT-XXX) or a specific drug name.")

    @traceable_safe(
        name="LLMChatController.handle_message",
        run_type="chain",
        process_inputs=process_inputs_controller,
        process_outputs=process_outputs_controller,
    )
    async def handle_message(self, user_text: str, *, conversation_id: Optional[str] = None, on_step: Optional[Any] = None, on_trace: Optional[Any] = None) -> ControllerResponse:
        if not user_text or not user_text.strip():
            return ControllerResponse(success=False, answer="Please provide a valid message.")

        conv_key = (conversation_id or "").strip() or "default"
        state = self.conversations.get(conv_key)
        original_user_text = user_text

        # Always sanitize (deterministic) for downstream parsing robustness.
        user_text = sanitize_user_text(user_text)
        # Optionally rewrite via LLM (if enabled) to fix spelling/unbalanced symbols.
        hint_parts = []
        if state.last_patient_id:
            hint_parts.append(f"last_patient_id={state.last_patient_id}")
        if getattr(state, "last_drug_name", None):
            hint_parts.append(f"last_drug_name={state.last_drug_name}")
        user_text = await preprocess_user_text(user_text, conversation_hint=(", ".join(hint_parts) or None))

        # Security checks should never be delegated to the LLM.
        try:
            # Enforce on both original and cleaned text (defense in depth).
            deny_if_phi_requested(original_user_text)
            if user_text != original_user_text:
                deny_if_phi_requested(user_text)
        except PHIError:
            pid_hint = None
            try:
                # Best-effort: attach context patient_id without revealing PHI.
                t = (user_text or "").lower()
                if ("second patient" in t or "the second patient" in t or "2nd patient" in t) and len(state.patient_history or []) >= 2:
                    pid_hint = state.patient_history[1]
                elif ("first patient" in t or "the first patient" in t) and (state.patient_history or []):
                    pid_hint = state.patient_history[0]
                else:
                    pid_hint = state.last_patient_id
            except Exception:
                pid_hint = state.last_patient_id
            suffix = f"\n\nContext: {pid_hint}" if pid_hint else ""
            return ControllerResponse(success=False, answer="Request denied.\n\nSensitive patient information (PHI) cannot be included." + suffix)

        if is_prompt_injection(user_text):
            return ControllerResponse(
                success=False,
                answer="Request denied.\n\nSystem security policies prevent access to restricted data.",
            )

        lower_q = (user_text or "").lower()
        if not llm_configured():
            return ControllerResponse(
                success=False,
                answer="LLM is not configured. Set AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, and AZURE_OPENAI_DEPLOYMENT_NAME in `.env`.",
            )

        role = self.default_role
        actor = ActorContext(role=role)
        urls = MCPServerURLs()
        metrics = RequestMetrics(patient_id=state.last_patient_id)

        # Resolve references like "first patient" / "second patient" to a concrete patient_id,
        # so the LLM/tools operate on the intended patient without requiring repetition.
        patient_ids = extract_patient_ids(user_text)
        ordinal = extract_patient_ordinal(user_text)
        if not patient_ids and ordinal:
            resolved = patient_id_from_ordinal(ordinal)
            state.last_patient_id = resolved
            metrics.patient_id = resolved
            user_text = f"{user_text} {resolved}"
        elif not patient_ids and resolves_to_previous_patient(user_text):
            t = (user_text or "").lower()
            resolved: Optional[str] = None
            if ("second patient" in t or "the second patient" in t or "2nd patient" in t) and len(state.patient_history or []) >= 2:
                resolved = state.patient_history[1]
            elif ("first patient" in t or "the first patient" in t) and (state.patient_history or []):
                resolved = state.patient_history[0]
            else:
                resolved = state.last_patient_id
            if resolved:
                state.last_patient_id = resolved
                metrics.patient_id = resolved
                user_text = f"{user_text} {resolved}"

        def _update_state_from_metrics() -> None:
            # Per-conversation MCP call counters (temporary, non-PHI state).
            try:
                state.mcp_call_count_last = int(metrics.mcp_call_count or 0)
                state.mcp_call_count_total = int(state.mcp_call_count_total or 0) + state.mcp_call_count_last
                state.mcp_call_count_by_server_last = dict(metrics.mcp_call_count_by_server or {})
                for srv, n in (state.mcp_call_count_by_server_last or {}).items():
                    state.mcp_call_count_by_server_total[srv] = int(state.mcp_call_count_by_server_total.get(srv, 0) or 0) + int(n or 0)
            except Exception:
                pass

            # Maintain a small patient history for references like "first/second patient".
            try:
                pid2 = state.last_patient_id
                if pid2:
                    if pid2 not in (state.patient_history or []):
                        state.patient_history.append(pid2)
                    if len(state.patient_history) > 20:
                        state.patient_history = state.patient_history[-20:]
            except Exception:
                pass

        # History helper — records a successful exchange so the next turn has full context.
        def _record_history(answer: str) -> None:
            state.chat_history.append({"role": "user", "content": original_user_text})
            state.chat_history.append({"role": "assistant", "content": answer})
            if len(state.chat_history) > 40:
                state.chat_history = state.chat_history[-40:]

        try:
            client = MCPClient(urls, actor, metrics)
            try:
                result = await self.agent.run(
                    client=client,
                    state=state,
                    role=role,
                    user_text=user_text,
                    chat_history=state.chat_history[:],  # pass full history for multi-turn context
                    on_step=on_step,
                    on_trace=on_trace,
                )
                if result.answer.strip().lower().startswith("request timed out while planning tool calls"):
                    fb = await self._fallback(user_text=user_text, state=state, client=client)
                    if fb.success:
                        _record_history(fb.answer)
                    return fb

                data = result.data
                # Always include patient_id when known to support UI invoice links, etc.
                if state.last_patient_id:
                    data = {**(data or {}), "patient_id": state.last_patient_id}
                # Only include invoice when the current response actually generated one.
                # Do NOT inject state.last_invoice here — that would attach PDF links to every
                # follow-up message after the first invoice, which is confusing.
                final_resp = ControllerResponse(success=True, answer=result.answer, data=data)
                _record_history(final_resp.answer)
                return final_resp
            finally:
                await client.aclose()
        except RBACError:
            return ControllerResponse(
                success=False,
                answer="Access denied.\n\nYou are not authorized to view sensitive patient data.",
            )
        except (ToolExecutionError, MCPConnectionError) as exc:
            reason = exc.details.get("reason") if isinstance(exc, ToolExecutionError) else str(exc)
            return ControllerResponse(success=False, answer=reason)
        except Exception as exc:
            # Avoid exposing stack traces to end users; keep it actionable.
            msg = str(exc)
            if "returned no choices" in msg.lower():
                return ControllerResponse(
                    success=False,
                    answer=(
                        "Chat request failed while contacting Azure OpenAI.\n\n"
                        "Please verify AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_DEPLOYMENT_NAME, and AZURE_OPENAI_API_VERSION."
                    ),
                )
            return ControllerResponse(success=False, answer=f"Chat request failed: {msg}")
        finally:
            _update_state_from_metrics()
