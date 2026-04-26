"""Human-friendly response formatter matching the required examples."""

from __future__ import annotations

from typing import Any


def format_medication_list(patient_id: str, meds: list[dict[str, Any]]) -> str:
    if not meds:
        return "No prescribed medications found for this patient."

    lines: list[str] = [f"Prescribed medications for {patient_id}:\n"]
    for i, med in enumerate(meds, start=1):
        name = med.get("brand") or med.get("drug_name") or "Medication"
        dose = med.get("dose")
        label = f"{name} {dose}".strip() if dose else str(name)
        lines.append(f"{i}. {label}")
    lines.append("\nYou can ask me to check availability for these medicines.")
    return "\n".join(lines)


def format_medication_lists(meds_by_patient: dict[str, list[dict[str, Any]]]) -> str:
    """Format medications for multiple patients in one response."""
    if not meds_by_patient:
        return "No patient IDs provided."

    blocks: list[str] = []
    for patient_id, meds in meds_by_patient.items():
        if not meds:
            blocks.append(f"Prescribed medications for {patient_id}:\n\n(no medications found)")
            continue

        lines: list[str] = [f"Prescribed medications for {patient_id}:\n"]
        for i, med in enumerate(meds, start=1):
            name = med.get("brand") or med.get("drug_name") or "Medication"
            dose = med.get("dose")
            label = f"{name} {dose}".strip() if dose else str(name)
            lines.append(f"{i}. {label}")
        blocks.append("\n".join(lines).strip())

    blocks.append("\nYou can ask me to check availability for any of these medicines.")
    return "\n\n".join(blocks).strip()


def format_all_patients_report(sections_by_patient: dict[str, str]) -> str:
    if not sections_by_patient:
        return "No patients found."

    lines: list[str] = ["Report for all patients (PHI omitted)\n"]
    for pid, text in sections_by_patient.items():
        lines.append(f"=== {pid} ===\n")
        lines.append((text or "").strip())
        lines.append("\n---\n")
    return "\n".join(lines).strip()


def _fmt_med_label(drug: str | None, dose: str | None, resolved_generic: str | None = None) -> str:
    base = " ".join(x for x in [drug or "Medication", dose] if x).strip()
    if resolved_generic and drug and resolved_generic.lower() != drug.lower():
        return f"{base} ({drug} → {resolved_generic})"
    return base


def format_stock_check_list(patient_id: str, result: dict[str, Any], *, summary_only: bool = False) -> str:
    available = result.get("available", []) or []
    unavailable = result.get("unavailable", []) or []
    alternatives = result.get("alternatives", []) or []
    dose_conflicts = result.get("dose_conflicts", []) or []

    if summary_only:
        if not unavailable and not dose_conflicts:
            return "All prescribed medicines are available. Would you like a detailed breakdown?"
        if unavailable and not dose_conflicts:
            one = unavailable[0].get("drug")
            extra = f" except {one}" if len(unavailable) == 1 and one else ""
            return f"All medicines are available{extra}. Would you like details?"
        return "Some medicines need review (availability or dose mismatch). Would you like details?"

    lines: list[str] = ["Stock Check Result:\n"]

    if available:
        lines.append("✔ Available:\n")
        for a in available:
            label = _fmt_med_label(a.get("drug"), a.get("dose"), a.get("resolved_generic"))
            units = a.get("units_available")
            units_txt = f"{units} units available" if units is not None else "available"
            lines.append(f"* {label} → {units_txt}")

    if unavailable:
        if available:
            lines.append("")
        lines.append("⚠ Not Available:\n")
        for u in unavailable:
            label = _fmt_med_label(u.get("drug"), u.get("dose"), u.get("resolved_generic"))
            lines.append(f"* {label}")

    alt_by_drug = {a.get("drug"): a for a in alternatives if isinstance(a, dict)}
    any_alt = any(a.get("suggested") for a in alternatives if isinstance(a, dict))
    any_missing_alt = any(a.get("suggested") is None for a in alternatives if isinstance(a, dict))

    if alternatives and (any_alt or any_missing_alt):
        lines.append("")
        lines.append("Suggested Alternatives:\n")
        for u in unavailable:
            drug = u.get("drug")
            a = alt_by_drug.get(drug) or {}
            suggested = a.get("suggested")
            if suggested:
                lines.append(f"* {drug} → {suggested} (available)")
            else:
                lines.append(f"* {drug} → No alternative medication found for this drug.")

        if any_alt:
            lines.append("\nPlease consult your doctor before switching to the alternative medication.")
        if any_missing_alt:
            lines.append("Please consult your doctor to re-prescribe medication.")

    if dose_conflicts:
        lines.append("\n⚠ Dose mismatch detected. Clinical review required.")

    return "\n".join(lines).strip()


def format_unavailable_only(patient_id: str, result: dict[str, Any]) -> str:
    unavailable = result.get("unavailable", []) or []
    alternatives = result.get("alternatives", []) or []
    dose_conflicts = result.get("dose_conflicts", []) or []

    if not unavailable and not dose_conflicts:
        return f"All prescribed medicines for {patient_id} are available in stock."

    lines: list[str] = [f"Medicines not in stock for {patient_id}:\n"]
    if unavailable:
        for u in unavailable:
            label = _fmt_med_label(u.get("drug"), u.get("dose"), u.get("resolved_generic"))
            lines.append(f"* {label}")

    alt_by_drug = {a.get("drug"): a for a in alternatives if isinstance(a, dict)}
    any_alt = any(a.get("suggested") for a in alternatives if isinstance(a, dict))
    any_missing_alt = any(a.get("suggested") is None for a in alternatives if isinstance(a, dict))

    if unavailable and alternatives and (any_alt or any_missing_alt):
        lines.append("\nSuggested Alternatives:\n")
        for u in unavailable:
            drug = u.get("drug")
            a = alt_by_drug.get(drug) or {}
            suggested = a.get("suggested")
            if suggested:
                lines.append(f"* {drug} → {suggested} (available)")
            else:
                lines.append(f"* {drug} → No alternative medication found for this drug.")

        if any_alt:
            lines.append("\nPlease consult your doctor before switching to the alternative medication.")
        if any_missing_alt:
            lines.append("Please consult your doctor to re-prescribe medication.")

    if dose_conflicts:
        lines.append("\n⚠ Dose mismatch detected for one or more items. Clinical review required.")

    return "\n".join(lines).strip()


def format_success_discharge(result: dict[str, Any]) -> str:
    pid = result["patient_id"]
    alerts = result.get("alerts", [])
    subs = result.get("substitutions", [])
    invoice = result.get("invoice", {}) or {}

    lines: list[str] = [f"Discharge process completed for {pid}.\n"]
    if not alerts:
        lines.append("✔ All medications verified")
    else:
        # show key alerts
        for a in alerts:
            if a.get("type") == "OUT_OF_STOCK":
                lines.append(f"⚠ {a.get('drug')} was out of stock")
            if a.get("type") == "OUT_OF_STOCK_NO_ALTERNATIVE":
                lines.append(f"⚠ {a.get('drug')} is currently unavailable.\n\n⚠ No suitable alternative found\nEscalation required")
                return "\n".join(lines)
            if a.get("type") == "DRUG_NOT_FOUND":
                lines.append(f"⚠ {a.get('drug')} not found in formulary")
            if a.get("type") == "PRICE_UNAVAILABLE":
                lines.append(f"⚠ {a.get('message')}")

    for s in subs:
        lines.append(f"✔ Replaced with {s.get('to')}")

    if result.get("conflicts"):
        c = result["conflicts"][0]
        lines.append("\nDose conflict detected.\n")
        lines.append(f"Clinical review required\n\nDetails: {c.get('detail')}")
        return "\n".join(lines)

    if invoice:
        lines.append("✔ Invoice generated successfully")
        total = invoice.get("subtotal_inr") or invoice.get("subtotal")
        if total is not None:
            lines.append(f"Total amount: ₹{int(total):,}")

    return "\n".join(lines)


def format_discharge_summary_safe(
    patient_id: str,
    *,
    admission_info: dict[str, Any] | None = None,
    diagnosis_codes: dict[str, Any] | None = None,
    medications: list[dict[str, Any]] | None = None,
    stock_check: dict[str, Any] | None = None,
    substitutions: list[dict[str, Any]] | None = None,
    alerts: list[dict[str, Any]] | None = None,
    conflicts: list[dict[str, Any]] | None = None,
    billing_safe_summary: dict[str, Any] | None = None,
    invoice: dict[str, Any] | None = None,
) -> str:
    """Detailed discharge summary that excludes PHI (no name/DOB/MRN/discharge_note)."""
    lines: list[str] = [f"Discharge Summary (PHI omitted) — {patient_id}\n"]

    if admission_info and isinstance(admission_info, dict):
        ward = admission_info.get("ward")
        adm = admission_info.get("admission_date")
        dis = admission_info.get("discharge_date")
        los = admission_info.get("los_days")
        lines.append("Admission\n")
        if ward is not None:
            lines.append(f"- Ward: {ward}")
        if adm is not None:
            lines.append(f"- Admission date: {adm}")
        if dis is not None:
            lines.append(f"- Discharge date: {dis}")
        if los is not None:
            lines.append(f"- Length of stay: {los} days")
        lines.append("")

    icd = None
    if diagnosis_codes and isinstance(diagnosis_codes, dict):
        icd = diagnosis_codes.get("diagnosis_icd10")
    if icd is None and billing_safe_summary and isinstance(billing_safe_summary, dict):
        icd = billing_safe_summary.get("diagnosis_icd10")
    if icd:
        lines.append("Diagnosis (ICD-10)\n")
        if isinstance(icd, list):
            for code in icd:
                lines.append(f"- {code}")
        else:
            lines.append(f"- {icd}")
        lines.append("")

    meds = medications or []
    if meds:
        lines.append("Discharge medications\n")
        for m in meds:
            name = m.get("brand") or m.get("drug_name") or "Medication"
            dose = m.get("dose")
            freq = m.get("frequency")
            route = m.get("route")
            parts = [str(name)]
            if dose:
                parts.append(str(dose))
            if freq:
                parts.append(str(freq))
            if route:
                parts.append(str(route))
            lines.append(f"- {' — '.join(parts)}")
        lines.append("")

    subs = substitutions or []
    if subs:
        lines.append("Substitutions\n")
        for s in subs:
            if not isinstance(s, dict):
                continue
            f = s.get("from")
            to = s.get("to")
            reason = s.get("reason") or "SUBSTITUTION"
            if f and to:
                lines.append(f"- {f} → {to} ({reason})")
        lines.append("")

    if conflicts:
        lines.append("Clinical review required\n")
        for c in conflicts:
            if not isinstance(c, dict):
                continue
            drug = c.get("drug") or c.get("detail") or "Medication"
            detail = c.get("detail") or "Dose mismatch detected"
            lines.append(f"- {drug}: {detail}")
        lines.append("")

    if alerts:
        lines.append("Alerts\n")
        for a in alerts:
            if not isinstance(a, dict):
                continue
            msg = a.get("message") or a.get("type") or "ALERT"
            drug = a.get("drug")
            if drug:
                lines.append(f"- {drug}: {msg}")
            else:
                lines.append(f"- {msg}")
        lines.append("")

    if stock_check and isinstance(stock_check, dict):
        unavailable = stock_check.get("unavailable") or []
        dose_conflicts = stock_check.get("dose_conflicts") or []
        if unavailable or dose_conflicts:
            lines.append("Pharmacy availability (from this session)\n")
            if unavailable:
                lines.append("- Not available:")
                for u in unavailable:
                    if isinstance(u, dict):
                        label = _fmt_med_label(u.get("drug"), u.get("dose"), u.get("resolved_generic"))
                        lines.append(f"  - {label}")
                    else:
                        lines.append(f"  - {u}")
            if dose_conflicts:
                lines.append("- Dose mismatch: clinical review required")
            lines.append("")

    if billing_safe_summary and isinstance(billing_safe_summary, dict):
        ward = billing_safe_summary.get("ward")
        los = billing_safe_summary.get("los_days")
        lines.append("Billing-safe summary\n")
        if ward is not None:
            lines.append(f"- Ward: {ward}")
        if los is not None:
            lines.append(f"- LOS days: {los}")
        lines.append("")

    if invoice and isinstance(invoice, dict) and invoice:
        lines.append("Invoice\n")
        subtotal = invoice.get("subtotal_inr") or invoice.get("subtotal")
        inv_id = invoice.get("invoice_id") or invoice.get("id")
        if inv_id:
            lines.append(f"- Invoice ID: {inv_id}")
        if subtotal is not None:
            try:
                lines.append(f"- Total amount: INR {int(subtotal):,}")
            except Exception:
                lines.append(f"- Total amount: INR {subtotal}")
        lines.append("")

    return "\n".join(lines).strip()


def format_access_denied() -> str:
    return "Access denied.\n\nYou are not authorized to view full clinical discharge summaries."


def format_phi_denied() -> str:
    return "Request denied.\n\nSensitive patient information (PHI) cannot be included in billing."


def format_observability(patient_id: str | None, summary: dict[str, Any]) -> str:
    lines = ["Execution Summary:\n"]
    lines.append(f"Total calls: {summary.get('total_calls', 0)}")
    lines.append(f"Alerts: {summary.get('alerts', 0)}")
    lines.append(f"RBAC violations: {summary.get('rbac_violations', 0)}")
    return "\n".join(lines)
