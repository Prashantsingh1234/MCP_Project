"""PHI protection utilities for the chatbot."""

from __future__ import annotations

import re
from typing import Any, Iterable


# NOTE: "name" is contextual. Many safe payloads legitimately contain keys like
# {"name": "Paracetamol"} (drug/line-item names). We only treat "name" as PHI
# when it appears in a patient/person context (e.g., alongside patient
# identifiers like MRN/DOB or under patient/demographics structures).
ALWAYS_PHI_FIELDS = {"dob", "mrn", "discharge_note", "attending_physician"}
CONTEXTUAL_PHI_FIELDS = {"name"}

# Backwards-compatible export used across the codebase.
PHI_FIELDS = ALWAYS_PHI_FIELDS | CONTEXTUAL_PHI_FIELDS


class PHIError(PermissionError):
    pass


def _iter_path_tokens(path: Iterable[str]) -> set[str]:
    tokens: set[str] = set()
    for p in path:
        pl = (p or "").lower()
        if pl:
            tokens.add(pl)
    return tokens


def _is_person_context(*, path: tuple[str, ...], container: dict[str, Any]) -> bool:
    keys_lower = {str(k).lower() for k in container.keys()}
    # Strong signals that the dict is about a patient/person.
    if keys_lower & {"mrn", "dob", "patient_id", "patientid", "person_id", "personid"}:
        return True

    # Path-based hints (e.g. payload["patient"]["name"]).
    tokens = _iter_path_tokens(path)
    if any("patient" in t for t in tokens):
        return True
    if "demographics" in tokens:
        return True

    return False


def contains_phi_keys(data: Any) -> bool:
    return _contains_phi_keys(data, path=())


def _contains_phi_keys(data: Any, *, path: tuple[str, ...]) -> bool:
    if isinstance(data, dict):
        keys_lower = {str(k).lower() for k in data.keys()}

        # Fields that are always considered PHI, regardless of context.
        if any(f in keys_lower for f in ALWAYS_PHI_FIELDS):
            return True

        # "name" is contextual; only PHI in patient/person contexts.
        if "name" in keys_lower and _is_person_context(path=path, container=data):
            return True

        for k, v in data.items():
            return_path = path + (str(k),)
            if _contains_phi_keys(v, path=return_path):
                return True
        return False
    if isinstance(data, list):
        for idx, v in enumerate(data):
            if _contains_phi_keys(v, path=path + (str(idx),)):
                return True
        return False
    return False


def strip_phi(data: Any) -> Any:
    """Recursively remove PHI keys from dict payloads."""
    return _strip_phi(data, path=())


def _strip_phi(data: Any, *, path: tuple[str, ...]) -> Any:
    if isinstance(data, dict):
        out = {}
        for k, v in data.items():
            kl = str(k).lower()
            if kl in ALWAYS_PHI_FIELDS:
                continue
            if kl == "name" and _is_person_context(path=path, container=data):
                continue
            out[k] = _strip_phi(v, path=path + (str(k),))
        return out
    if isinstance(data, list):
        return [_strip_phi(v, path=path + (str(i),)) for i, v in enumerate(data)]
    return data


def deny_if_phi_requested(user_text: str) -> None:
    t = (user_text or "").lower()
    # These phrases are direct requests for PHI fields.
    # "name and" and "doctor" are intentionally excluded — too broad; they block legitimate drug/workflow queries.
    phi_phrases = [
        "full patient profile",
        "patient profile",
        "patient demographics",
        "demographics",
        "patient name",
        "patient's name",
        "doctor details",
        "doctor's name",
        "attending physician",
        "attending doctor",
        "dob",
        "date of birth",
        "mrn",
        "medical record",
        "discharge note",
        "full discharge summary",
        "full summary",
    ]
    if any(k in t for k in phi_phrases):
        raise PHIError("Request denied.\n\nSensitive patient information (PHI) cannot be included.")

    # Also deny explicit "name of PAT-XXX" or similar patient-specific name requests.
    # This avoids a bypass where the user omits the literal phrase "patient name".
    try:
        upper = (user_text or "").upper()
        if re.search(r"\bNAME\b\s+(OF|FOR)\s+\bPAT-\d{3}\b", upper):
            raise PHIError("Request denied.\n\nSensitive patient information (PHI) cannot be included.")
        if re.search(r"\bPAT-\d{3}\b.*\bNAME\b", upper) and "MEDICINE" not in upper and "MEDICATION" not in upper and "DRUG" not in upper:
            # Patient identifier + "name" in the same query is treated as PHI unless clearly about drugs.
            raise PHIError("Request denied.\n\nSensitive patient information (PHI) cannot be included.")
    except PHIError:
        raise
    except Exception:
        pass
