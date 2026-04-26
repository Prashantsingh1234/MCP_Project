"""Rule-based intent classifier for the MCPDischarge chatbot."""

from __future__ import annotations

import re

from src.chatbot.validator import extract_patient_ids

INJECTION_PATTERNS = [
    r"\bignore\b.*\brbac\b",
    r"\bbypass\b.*\brbac\b",
    r"\bshow\b.*\ball\b.*\bpatient\b",
    r"\b(system prompt|developer message|jailbreak)\b",
]


def is_prompt_injection(text: str) -> bool:
    t = (text or "").strip().lower()
    return any(re.search(p, t) for p in INJECTION_PATTERNS)


def classify_intent(text: str) -> str:
    if not text or not text.strip():
        return "invalid_input"

    t = text.strip().lower()

    if is_prompt_injection(t):
        return "rbac_sensitive_request"

    # Bulk: more than one patient id in a single request
    if len(extract_patient_ids(text)) > 1:
        return "bulk_request"

    # Observability
    if any(k in t for k in ["how many mcp calls", "mcp calls", "rbac violations", "telemetry", "observability"]):
        return "observability_query"

    # RBAC sensitive / PHI heavy
    if any(k in t for k in ["full discharge summary", "full summary", "discharge note", "attending physician", "mrn", "dob", "patient name"]):
        return "rbac_sensitive_request"

    # Medication list fetch (multi-turn flow entry)
    if any(k in t for k in ["prescribed medicines", "prescribed medications", "what medicines", "what medications", "show medicines", "show medications"]):
        return "meds_fetch"
    if "medicin" in t and any(k in t for k in ["prescribed", "discharge medications", "discharge medicines"]):
        return "meds_fetch"

    # Medication availability follow-ups ("these medicines", "all medicines", summaries)
    if any(k in t for k in ["these medicines", "these medications", "those medicines", "those medications", "all medicines", "all medications"]):
        if any(k in t for k in ["available", "availability", "check", "stock"]):
            return "meds_stock_check"
    if any(k in t for k in ["check if these", "check these", "check them", "check availability", "full availability summary", "availability summary"]):
        return "meds_stock_check"
    if any(k in t for k in ["is everything ok", "is everything okay", "is everything available", "is everything fine"]):
        return "meds_stock_check"

    # Workflow
    if any(k in t for k in ["complete discharge", "process discharge"]) or ("discharge" in t and "patient" in t):
        if "invoice" in t or "bill" in t:
            return "invoice_generation"
        return "discharge_workflow"

    if any(k in t for k in ["generate invoice", "invoice for", "billing for", "bill for"]):
        return "invoice_generation"

    # Pharmacy
    if any(k in t for k in ["check stock", "available", "availability", "in stock", "out of stock", "not in stock"]):
        # If the user is talking about medicines/medications as a set, prefer the list-stock intent.
        if any(k in t for k in ["medicines", "medications", "prescribed", "discharge medications", "discharge medicines"]):
            return "meds_stock_check"
        return "stock_check"

    if "proceed with" in t and "mg" in t:
        return "stock_check"

    # Otherwise
    if len(t.split()) <= 2:
        return "ambiguous_query"

    return "ambiguous_query"
