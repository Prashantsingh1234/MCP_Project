"""Prescription rendering (HTML + PDF).

Generates a PHI-safe discharge prescription document.
Only uses billing-safe EHR fields + medication list (no name/DOB/MRN).

Endpoints:
    GET /api/prescription/pdf?patient_id=PAT-XXX
    GET /api/prescription/html?patient_id=PAT-XXX
"""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from src.chatbot.mcp_client import MCPClient
from src.chatbot.metrics import RequestMetrics
from src.chatbot.phi_guard import strip_phi
from src.chatbot.rbac_guard import ActorContext


# ── Data collection ────────────────────────────────────────────────────────────

async def collect_prescription_data(
    pid: str,
    actor: ActorContext,
    metrics_obj: RequestMetrics,
    urls: Any,
) -> dict[str, Any]:
    """Fetch EHR data and medication list, check stock, and build prescription payload."""
    from src.chatbot.mcp_client import MCPClient  # local to avoid circular at module level

    substituted: list[dict] = []
    out_of_stock: list[str] = []
    medications: list[dict] = []

    async with MCPClient(urls, actor, metrics_obj) as client:
        billing_safe = await client.ehr_call(
            "get_billing_safe_summary", {"patient_id": pid}, patient_id=pid
        )
        meds_raw = await client.ehr_call(
            "get_discharge_medications", {"patient_id": pid}, patient_id=pid
        )
        meds_raw = strip_phi(meds_raw or [])

        for med in meds_raw:
            drug_query = med.get("brand") or med.get("drug_name") or "Unknown"
            original_label = med.get("drug_name") or med.get("brand") or "Medication"

            try:
                stock = await client.pharmacy_call(
                    "check_stock",
                    {"drug_name": drug_query, "quantity": 1, "dose": med.get("dose")},
                    patient_id=pid,
                )
            except Exception:
                stock = {"available": True, "found": True}

            if not stock.get("found", True):
                out_of_stock.append(original_label)
                medications.append({**med, "_status": "NOT_FOUND"})
                continue

            if not stock.get("available", True):
                try:
                    alt = await client.pharmacy_call(
                        "get_alternative", {"drug_name": drug_query}, patient_id=pid
                    )
                    alternatives = (alt or {}).get("alternatives", [])
                except Exception:
                    alternatives = []

                if alternatives:
                    chosen = alternatives[0]
                    alt_name = chosen.get("generic_name") or drug_query
                    substituted.append({"from": original_label, "to": alt_name})
                    medications.append({
                        **med,
                        "drug_name": alt_name,
                        "brand": chosen.get("brand_names", [alt_name])[0] if chosen.get("brand_names") else alt_name,
                        "_status": "SUBSTITUTED",
                        "_original": original_label,
                        "_safety_note": "Please consult your doctor before taking this medication.",
                    })
                else:
                    out_of_stock.append(original_label)
                    medications.append({
                        **med,
                        "_status": "UNAVAILABLE",
                        "_safety_note": "Please consult your doctor to re-prescribe this medication.",
                    })
            else:
                medications.append({**med, "_status": "AVAILABLE"})

    patient = {
        "patient_id": billing_safe.get("patient_id", pid),
        "ward": billing_safe.get("ward", "—"),
        "admission_date": billing_safe.get("admission_date", "—"),
        "discharge_date": billing_safe.get("discharge_date", "—"),
        "los_days": billing_safe.get("los_days", "—"),
        "diagnosis_icd10": billing_safe.get("diagnosis_icd10", []),
    }

    has_issues = bool(substituted or out_of_stock)

    return {
        "hospital": {
            "hospital_name": "CityCare Hospital",
            "address": "1 Healthcare Avenue, Sector 21, Bengaluru 560001",
            "contact": "pharmacy@citycare.example • +91 80 4000 0000",
        },
        "prescription": {
            "rx_id": f"RX-{pid}-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "issued_date": datetime.now().strftime("%Y-%m-%d"),
            "issued_time": datetime.now().strftime("%H:%M"),
        },
        "patient": patient,
        "medications": medications,
        "notes": {
            "has_issues": has_issues,
            "substituted": substituted,
            "out_of_stock": out_of_stock,
            "mandatory_note": (
                "Note:\nSome prescribed medicines were unavailable.\n"
                "Alternative medications have been suggested.\n\n"
                "Please consult your doctor before taking alternative medicines."
            ) if has_issues else None,
        },
    }


# ── HTML renderer ──────────────────────────────────────────────────────────────

def render_prescription_html(rx_data: dict[str, Any]) -> str:
    """Render prescription HTML (A4-print CSS inlined)."""
    template_dir = Path(__file__).parent / "templates"
    css = (template_dir / "prescription.css").read_text(encoding="utf-8")
    html = (template_dir / "prescription.html").read_text(encoding="utf-8")

    try:
        from jinja2 import Template  # type: ignore
    except Exception as exc:
        raise RuntimeError("Missing dependency: jinja2.") from exc

    safe = strip_phi(rx_data)
    return Template(html).render(css=css, **safe)


# ── PDF generator ──────────────────────────────────────────────────────────────

def generate_prescription_pdf(rx_data: dict[str, Any]) -> bytes:
    """Generate a print-ready A4 prescription PDF using ReportLab."""
    safe = strip_phi(rx_data)

    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.lib.enums import TA_RIGHT, TA_CENTER
    except Exception as exc:
        raise RuntimeError("Missing dependency: reportlab.") from exc

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=24,
        rightMargin=24,
        topMargin=24,
        bottomMargin=24,
        title="Discharge Prescription",
    )

    styles = getSampleStyleSheet()
    normal = styles["BodyText"]
    normal.fontName = "Helvetica"
    normal.fontSize = 11
    normal.leading = 16

    h1 = styles["Heading1"]
    h1.fontName = "Helvetica-Bold"
    h1.fontSize = 16
    h1.leading = 20

    h2 = styles["Heading2"]
    h2.fontName = "Helvetica-Bold"
    h2.fontSize = 12
    h2.leading = 16
    h2.spaceBefore = 6
    h2.spaceAfter = 6

    right = styles["BodyText"].clone("Right")
    right.fontName = "Helvetica"
    right.fontSize = 11
    right.leading = 16
    right.alignment = TA_RIGHT

    center = styles["BodyText"].clone("Center")
    center.fontName = "Helvetica"
    center.fontSize = 11
    center.leading = 16
    center.alignment = TA_CENTER

    hospital = safe["hospital"]
    rx = safe["prescription"]
    patient = safe["patient"]
    medications: list[dict] = safe.get("medications") or []
    notes: dict = safe.get("notes") or {}

    story: list[Any] = []

    # ── Header ────────────────────────────────────────────────────────────────
    header_left = [
        Paragraph(str(hospital["hospital_name"]), h1),
        Paragraph(str(hospital["address"]), normal),
        Paragraph(str(hospital["contact"]), normal),
    ]
    header_right = [
        Paragraph("<b>DISCHARGE PRESCRIPTION</b>", normal),
        Paragraph(f"<b>Rx No</b>: {rx['rx_id']}", normal),
        Paragraph(f"<b>Date</b>: {rx['issued_date']}", normal),
        Paragraph(f"<b>Time</b>: {rx['issued_time']}", normal),
    ]
    hdr_tbl = Table([[header_left, header_right]], colWidths=[120 * mm, 60 * mm])
    hdr_tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.HexColor("#e5e7eb")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(hdr_tbl)
    story.append(Spacer(1, 10))

    # ── Patient info ──────────────────────────────────────────────────────────
    story.append(Paragraph("Patient Information (Non-PHI)", h2))
    icd = ", ".join(patient.get("diagnosis_icd10") or []) or "—"
    pat_rows = [
        ["<b>Patient ID</b>", str(patient.get("patient_id", "—"))],
        ["<b>Ward</b>", str(patient.get("ward", "—"))],
        ["<b>Discharge Date</b>", str(patient.get("discharge_date", "—"))],
        ["<b>Length of Stay</b>", f"{patient.get('los_days', '—')} day(s)"],
        ["<b>ICD-10 Codes</b>", icd],
    ]
    pat_tbl = Table(
        [[Paragraph(k, normal), Paragraph(v, normal)] for k, v in pat_rows],
        colWidths=[42 * mm, 130 * mm],
    )
    pat_tbl.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(pat_tbl)
    story.append(Spacer(1, 14))

    # ── Medications table ─────────────────────────────────────────────────────
    story.append(Paragraph("Prescribed Medications", h2))
    med_header = [
        Paragraph("<b>#</b>", normal),
        Paragraph("<b>Drug</b>", normal),
        Paragraph("<b>Dose</b>", right),
        Paragraph("<b>Frequency</b>", center),
        Paragraph("<b>Route</b>", center),
        Paragraph("<b>Days</b>", right),
        Paragraph("<b>Status</b>", center),
    ]
    med_table_data: list[list[Any]] = [med_header]

    status_colors = {
        "AVAILABLE": colors.HexColor("#16a34a"),
        "SUBSTITUTED": colors.HexColor("#d97706"),
        "UNAVAILABLE": colors.HexColor("#dc2626"),
        "NOT_FOUND": colors.HexColor("#dc2626"),
    }
    status_labels = {
        "AVAILABLE": "✔ Available",
        "SUBSTITUTED": "⚠ Substitute",
        "UNAVAILABLE": "❌ Unavailable",
        "NOT_FOUND": "❌ Not Found",
    }

    row_styles: list[tuple] = []
    for idx, med in enumerate(medications, start=1):
        status = str(med.get("_status", "AVAILABLE"))
        drug_name = med.get("drug_name") or "Medication"
        brand = med.get("brand") or ""
        label = f"{drug_name}"
        if brand and brand.lower() != drug_name.lower():
            label += f"\n({brand})"
        original = med.get("_original")
        if original:
            label += f"\n[alt. for {original}]"

        s_label = status_labels.get(status, status)
        s_color = status_colors.get(status, colors.black)

        s_para = styles["BodyText"].clone(f"Status{idx}")
        s_para.fontName = "Helvetica-Bold"
        s_para.fontSize = 9
        s_para.textColor = s_color
        s_para.alignment = TA_CENTER
        s_para.leading = 13

        med_table_data.append([
            Paragraph(str(idx), normal),
            Paragraph(label.replace("\n", "<br/>"), normal),
            Paragraph(str(med.get("dose") or "—"), right),
            Paragraph(str(med.get("frequency") or "—"), center),
            Paragraph(str(med.get("route") or "Oral"), center),
            Paragraph(str(med.get("days_supply") or "—"), right),
            Paragraph(s_label, s_para),
        ])
        if status == "SUBSTITUTED":
            row_styles.append(("BACKGROUND", (0, idx), (-1, idx), colors.HexColor("#fffbeb")))
        elif status in ("UNAVAILABLE", "NOT_FOUND"):
            row_styles.append(("BACKGROUND", (0, idx), (-1, idx), colors.HexColor("#fef2f2")))

    med_tbl = Table(
        med_table_data,
        colWidths=[8 * mm, 58 * mm, 20 * mm, 26 * mm, 18 * mm, 12 * mm, 30 * mm],
        repeatRows=1,
    )
    base_style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3f4f6")),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#e5e7eb")),
        ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.HexColor("#e5e7eb")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]
    med_tbl.setStyle(TableStyle(base_style + row_styles))
    story.append(med_tbl)
    story.append(Spacer(1, 14))

    # ── Safety note (mandatory when alternatives suggested) ───────────────────
    if notes.get("has_issues"):
        note_normal = styles["BodyText"].clone("NoteNormal")
        note_normal.fontName = "Helvetica"
        note_normal.fontSize = 10
        note_normal.leading = 14
        note_normal.backColor = colors.HexColor("#fff7ed")
        note_normal.borderColor = colors.HexColor("#fb923c")
        note_normal.borderPadding = 8
        note_normal.borderWidth = 0.5

        note_lines = ["<b>Note:</b>"]
        note_lines.append("Some prescribed medicines were unavailable.")
        subs: list[dict] = notes.get("substituted") or []
        if subs:
            note_lines.append("Substitutions made:")
            for s in subs:
                note_lines.append(f"  • {s.get('from', '')} → {s.get('to', '')}")
        oos: list[str] = notes.get("out_of_stock") or []
        if oos:
            note_lines.append("Unavailable (no alternative):")
            for d in oos:
                note_lines.append(f"  • {d}")
        note_lines.append("")
        note_lines.append(
            "<b>⚠ Please consult your doctor before taking alternative medicines.</b>"
        )
        story.append(Paragraph("<br/>".join(note_lines), note_normal))
        story.append(Spacer(1, 10))

    # ── Footer ────────────────────────────────────────────────────────────────
    footer_txt = (
        "This prescription is generated electronically at discharge.<br/>"
        "Dispensed by: CityCare Hospital Pharmacy • Valid for 30 days from issue date."
    )
    story.append(Paragraph(footer_txt, normal))

    doc.build(story)
    return buf.getvalue()
