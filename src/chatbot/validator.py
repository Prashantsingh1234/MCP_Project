"""Input validation for the MCPDischarge chatbot."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional


PATIENT_ID_RE = re.compile(r"\bPAT-\d{3}\b", re.IGNORECASE)
PATIENT_ORDINAL_NUM_RE = re.compile(r"\b(\d{1,3})(?:st|nd|rd|th)?\s+patient\b", re.IGNORECASE)

_ORDINAL_WORDS: dict[str, int] = {
    "first": 1,
    # Common typos observed in chat history
    "fist": 1,
    "frist": 1,
    "second": 2,
    "secod": 2,
    "third": 3,
    "thrid": 3,
    "thid": 3,
    "fourth": 4,
    "forth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
    "eleventh": 11,
    "twelfth": 12,
}
PATIENT_ORDINAL_WORD_RE = re.compile(r"\b(" + "|".join(_ORDINAL_WORDS.keys()) + r")\s+patient\b", re.IGNORECASE)


class ValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ValidationResult:
    patient_ids: list[str]
    drug_name: Optional[str]
    dose: Optional[str] = None


def extract_patient_ids(text: str) -> list[str]:
    return sorted({m.group(0).upper() for m in PATIENT_ID_RE.finditer(text or "")})


def extract_patient_ordinal(text: str) -> Optional[int]:
    """Extract ordinal patient references like 'third patient', '4th patient', 'patient 3'."""
    if not text:
        return None
    t = text.strip().lower()

    m = PATIENT_ORDINAL_WORD_RE.search(t)
    if m:
        return _ORDINAL_WORDS.get(m.group(1).lower())

    m = PATIENT_ORDINAL_NUM_RE.search(t)
    if m:
        try:
            n = int(m.group(1))
            return n if 1 <= n <= 999 else None
        except Exception:
            return None

    # Also accept "patient 3" / "patient #3"
    m = re.search(r"\bpatient\s*#?\s*(\d{1,3})\b", t, flags=re.IGNORECASE)
    if m:
        try:
            n = int(m.group(1))
            return n if 1 <= n <= 999 else None
        except Exception:
            return None

    return None


def patient_id_from_ordinal(n: int) -> str:
    return f"PAT-{int(n):03d}"


def validate_patient_id_exists(patient_id: str, known_ids: Iterable[str]) -> None:
    if patient_id not in set(known_ids):
        raise ValidationError("Patient not found. Please verify ID.")


def extract_drug_name(text: str) -> Optional[str]:
    """Best-effort drug name extraction for simple queries."""
    if not text:
        return None
    t = text.strip()
    # Common patterns: "check stock for X", "price of X", "is X available"
    lower = t.lower()

    # Follow-up quantity queries: "how many units/stocks of X are available/in stock?"
    m = re.search(
        r"\bhow\s+many\b.*?\b(?:units?|stocks?)\b.*?\bof\s+(.+?)\s+(?:are\s+)?(?:available|in\s+stock)\b",
        lower,
        flags=re.IGNORECASE,
    )
    if m:
        candidate = m.group(1)
        candidate = re.sub(r"\s+(are\s+)?(available|in stock)\b.*$", "", candidate, flags=re.IGNORECASE).strip(" .,:;?!")
        return candidate or None
    if lower.startswith("proceed with "):
        tail = t[len("proceed with ") :].strip()
        # Strip trailing dose if present (e.g., "Semaglutide 0.5mg")
        tail = re.sub(r"\s+\d+(?:\.\d+)?\s*mg\b", "", tail, flags=re.IGNORECASE).strip(" .,:;")
        return tail or None

    m = re.match(r"^\s*check\s+if\s+(.+?)\s+is\s+(available|in stock)\b", lower)
    if m:
        original = t.strip()
        candidate = re.sub(r"^\s*check\s+if\s+", "", original, flags=re.IGNORECASE)
        candidate = re.sub(r"\s+is\s+(available|in stock)\b.*$", "", candidate, flags=re.IGNORECASE).strip(" .,:;")
        return candidate or None

    m = re.match(r"^\s*is\s+(.+?)\s+(available|in stock)\b", lower)
    if m:
        # Use original casing slice based on match span in lower; easiest is to re-split from original.
        original = t.strip()
        # naive: take between "is " and last token " available"
        candidate = re.sub(r"^\s*is\s+", "", original, flags=re.IGNORECASE)
        candidate = re.sub(r"\s+(available|in stock)\b.*$", "", candidate, flags=re.IGNORECASE).strip(" .,:;")
        return candidate or None

    for sep in [" for ", " of "]:
        if sep in lower:
            candidate = t.split(sep, 1)[1].strip()
            # Strip common trailing qualifiers that are not part of drug name.
            candidate = re.sub(r"\s+(are\s+)?(available|in stock)\b.*$", "", candidate, flags=re.IGNORECASE).strip(" .,:;?!")
            # If the candidate still contains obvious scaffolding, drop it.
            candidate = re.sub(r"\b(are\s+)?(available|in stock)\b.*$", "", candidate, flags=re.IGNORECASE).strip(" .,:;?!")
            return candidate or None
    # Fallback: return None, let classifier treat as ambiguous
    return None


def extract_dose(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b(\d+(?:\.\d+)?)\s*mg\b", text.lower())
    if not m:
        return None
    return f"{m.group(1)}mg"


def validate_required_patient_id(intent: str, patient_ids: list[str]) -> None:
    if intent in {"discharge_workflow", "invoice_generation", "rbac_sensitive_request", "meds_fetch"} and not patient_ids:
        raise ValidationError("Please provide a valid patient ID to proceed.")


def validate_not_bulk_for_intent(intent: str, patient_ids: list[str]) -> None:
    if intent in {"invoice_generation", "discharge_workflow"} and len(patient_ids) > 1:
        raise ValidationError("Bulk requests are not supported for this operation. Provide one patient ID.")


def validate_message(text: str, intent: str) -> ValidationResult:
    patient_ids = extract_patient_ids(text)
    validate_required_patient_id(intent, patient_ids)
    drug = extract_drug_name(text) if intent in {"stock_check"} else None
    dose = extract_dose(text) if intent in {"stock_check"} else None
    return ValidationResult(patient_ids=patient_ids, drug_name=drug, dose=dose)


def resolves_to_previous_patient(text: str) -> bool:
    """Detect follow-up references like 'first patient' / 'same patient'."""
    t = (text or "").lower()
    return any(
        k in t
        for k in [
            "first patient",
            "the first patient",
            "second patient",
            "the second patient",
            "2nd patient",
            "same patient",
            "that patient",
            "this patient",
            "previous patient",
            "earlier patient",
            "above patient",
        ]
    )
