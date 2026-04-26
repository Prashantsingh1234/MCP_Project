"""Invoice rendering (HTML + PDF).

PDF generation uses ReportLab to avoid external binaries (wkhtmltopdf) and OS
native dependencies (WeasyPrint). An HTML+CSS template is also included for
preview and future renderers.

Security:
- Uses billing-safe data only (NO PHI keys).
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from src.chatbot.phi_guard import contains_phi_keys, strip_phi


@dataclass(frozen=True)
class InvoiceLineItem:
    code: Optional[str]
    name: str
    description: str
    quantity: int
    unit_price_inr: float
    total_price_inr: float


def _fmt_inr(value: float) -> str:
    try:
        # ReportLab's default built-in fonts don't reliably render the ₹ symbol.
        # Use an ASCII prefix to avoid tofu boxes in PDFs.
        return f"INR {value:,.0f}"
    except Exception:
        return f"INR {value}"


def render_invoice_html(invoice_data: dict[str, Any]) -> str:
    """Render invoice HTML (A4-print CSS inlined)."""
    template_dir = Path(__file__).parent / "templates"
    css = (template_dir / "invoice.css").read_text(encoding="utf-8")
    html = (template_dir / "invoice.html").read_text(encoding="utf-8")

    try:
        from jinja2 import Template  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("Missing dependency: jinja2. Install with: pip install -r requirements.txt") from exc

    safe_invoice_data = strip_phi(invoice_data)
    if contains_phi_keys(safe_invoice_data):
        raise ValueError("PHI keys detected in invoice_data; refusing to render.")

    return Template(html).render(css=css, **safe_invoice_data)


def generate_invoice_pdf(invoice_data: dict[str, Any]) -> bytes:
    """Generate a print-ready A4 PDF invoice using ReportLab."""
    safe_invoice_data = strip_phi(invoice_data)
    if contains_phi_keys(safe_invoice_data):
        raise ValueError("PHI keys detected in invoice_data; refusing to generate PDF.")

    try:
        from reportlab.lib.pagesizes import A4  # type: ignore
        from reportlab.lib import colors  # type: ignore
        from reportlab.lib.styles import getSampleStyleSheet  # type: ignore
        from reportlab.lib.units import mm  # type: ignore
        from reportlab.platypus import (  # type: ignore
            SimpleDocTemplate,
            Paragraph,
            Spacer,
            Table,
            TableStyle,
        )
        from reportlab.lib.enums import TA_RIGHT  # type: ignore
        from reportlab.platypus.flowables import Flowable  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("Missing dependency: reportlab. Install with: pip install -r requirements.txt") from exc

    buf = io.BytesIO()

    class _InvoiceDocTemplate(SimpleDocTemplate):
        def beforeFlowable(self, flowable: Flowable) -> None:  # type: ignore[override]
            bookmark = getattr(flowable, "_invoice_bookmark", None)
            if not bookmark:
                return
            title = getattr(flowable, "_invoice_bookmark_title", bookmark)
            level = int(getattr(flowable, "_invoice_bookmark_level", 0))
            try:
                self.canv.bookmarkPage(bookmark)
                self.canv.addOutlineEntry(str(title), bookmark, level=level, closed=False)
            except Exception:
                # Outline/bookmarking should never break PDF generation.
                return

    doc = _InvoiceDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=24,
        rightMargin=24,
        topMargin=24,
        bottomMargin=24,
        title="Medical Invoice",
        author=safe_invoice_data.get("hospital", {}).get("hospital_name", "Hospital"),
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
    h1.spaceAfter = 6

    h2 = styles["Heading2"]
    h2.fontName = "Helvetica-Bold"
    h2.fontSize = 12
    h2.leading = 16
    h2.spaceBefore = 6
    h2.spaceAfter = 8

    right = styles["BodyText"].clone("Right")
    right.fontName = "Helvetica"
    right.fontSize = 11
    right.leading = 16
    right.alignment = TA_RIGHT

    hospital = safe_invoice_data["hospital"]
    invoice = safe_invoice_data["invoice"]
    insurance = safe_invoice_data.get("insurance", {}) or {}
    patient = safe_invoice_data["patient"]
    diagnosis = safe_invoice_data["diagnosis"]
    items: list[dict[str, Any]] = safe_invoice_data["items"]
    totals = safe_invoice_data["totals"]
    payment_url = str(invoice.get("payment_url") or "").strip() or None

    story: list[Any] = []

    def _bookmark(flowable: Any, *, key: str, title: str, level: int = 0) -> Any:
        setattr(flowable, "_invoice_bookmark", key)
        setattr(flowable, "_invoice_bookmark_title", title)
        setattr(flowable, "_invoice_bookmark_level", level)
        return flowable

    # Header row: left hospital, right invoice meta
    hospital_email = str(hospital.get("email") or "").strip()
    hospital_phone = str(hospital.get("phone") or "").strip()
    hospital_website = str(hospital.get("website") or "").strip()
    contact_fallback = str(hospital.get("contact") or "").strip()

    contact_lines: list[Any] = []
    if hospital_email:
        contact_lines.append(Paragraph(f'<link href="mailto:{hospital_email}">{hospital_email}</link>', normal))
    if hospital_phone:
        tel = hospital_phone.replace(" ", "")
        contact_lines.append(Paragraph(f'<link href="tel:{tel}">{hospital_phone}</link>', normal))
    if hospital_website:
        href = hospital_website if hospital_website.startswith(("http://", "https://")) else f"https://{hospital_website}"
        contact_lines.append(Paragraph(f'<link href="{href}">{hospital_website}</link>', normal))
    if not contact_lines and contact_fallback:
        contact_lines.append(Paragraph(contact_fallback, normal))

    header_left = [
        Paragraph(str(hospital["hospital_name"]), h1),
        Paragraph(str(hospital["address"]), normal),
        *contact_lines,
    ]
    header_right = [
        Paragraph("<b>MEDICAL INVOICE</b>", normal),
        Paragraph(f"<b>Invoice No</b>: {invoice['invoice_id']}", normal),
        Paragraph(f"<b>Invoice Date</b>: {invoice['invoice_date']}", normal),
    ]
    if payment_url:
        header_right.append(Paragraph(f'<b>Pay Online</b>: <link href="{payment_url}">Open payment link</link>', normal))
    header_tbl = Table([[header_left, header_right]], colWidths=[120 * mm, 60 * mm])
    header_tbl.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.HexColor("#e5e7eb")),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    story.append(_bookmark(header_tbl, key="invoice", title="Invoice", level=0))
    story.append(Spacer(1, 10))

    # Optional QR code for payment link (if provided).
    if payment_url:
        try:
            from reportlab.graphics.barcode.qr import QrCodeWidget  # type: ignore
            from reportlab.graphics.shapes import Drawing  # type: ignore
        except Exception:
            QrCodeWidget = None  # type: ignore[assignment]

        if QrCodeWidget is not None:
            try:
                qrw = QrCodeWidget(payment_url)
                x1, y1, x2, y2 = qrw.getBounds()
                qr_size = 26 * mm
                d = Drawing(
                    qr_size,
                    qr_size,
                    transform=[qr_size / (x2 - x1), 0, 0, qr_size / (y2 - y1), 0, 0],
                )
                d.add(qrw)
                qr_tbl = Table(
                    [[Paragraph("<b>Scan to pay</b>", normal), d]],
                    colWidths=[35 * mm, 26 * mm],
                )
                qr_outer = Table([[None, qr_tbl]], colWidths=[119 * mm, 61 * mm])
                qr_outer.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
                story.append(qr_outer)
                story.append(Spacer(1, 6))
            except Exception:
                # QR is a best-effort enhancement; ignore failures.
                pass

    # Patient + diagnosis blocks
    pat_lines = [
        ["<b>Patient ID</b>", str(patient["patient_id"])],
        ["<b>Ward</b>", str(patient["ward"])],
        ["<b>Admission Date</b>", str(patient["admission_date"])],
        ["<b>Discharge Date</b>", str(patient["discharge_date"])],
        ["<b>Length of Stay</b>", f"{patient['los_days']} day(s)"],
    ]
    pat_tbl = Table([[Paragraph(k, normal), Paragraph(v, normal)] for k, v in pat_lines], colWidths=[42 * mm, 70 * mm])
    pat_tbl.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("ROWSPACING", (0, 0), (-1, -1), 2)]))

    dx_codes = ", ".join(diagnosis.get("icd10_codes", [])) or "—"
    dx_tbl = Table(
        [[Paragraph("<b>ICD-10 Codes</b>", normal), Paragraph(dx_codes, normal)]], colWidths=[42 * mm, 86 * mm]
    )
    dx_tbl.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))

    insurance_rows: list[list[Any]] = []
    if bool(insurance.get("has_insurance")):
        insurance_rows = [
            [Paragraph("<b>Insurer</b>", normal), Paragraph(str(insurance.get("insurer_name") or "—"), normal)],
            [Paragraph("<b>Plan</b>", normal), Paragraph(str(insurance.get("plan_type") or "—"), normal)],
            [Paragraph("<b>Policy</b>", normal), Paragraph(str(insurance.get("policy_number_masked") or "—"), normal)],
        ]
        deductible = insurance.get("deductible_inr")
        copay = insurance.get("copay_inr")
        covered = insurance.get("covered_amount_inr")
        if deductible is not None:
            insurance_rows.append(
                [Paragraph("<b>Deductible</b>", normal), Paragraph(_fmt_inr(float(deductible or 0)), right)]
            )
        if copay is not None:
            insurance_rows.append([Paragraph("<b>Copay</b>", normal), Paragraph(_fmt_inr(float(copay or 0)), right)])
        if covered is not None:
            insurance_rows.append([Paragraph("<b>Covered</b>", normal), Paragraph(_fmt_inr(float(covered or 0)), right)])

    insurance_tbl = None
    if insurance_rows:
        insurance_tbl = Table(insurance_rows, colWidths=[42 * mm, 86 * mm])
        insurance_tbl.setStyle(
            TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("ROWSPACING", (0, 0), (-1, -1), 2)])
        )

    right_block: list[Any] = [Paragraph("Diagnosis & Insurance", h2), dx_tbl]
    if insurance_tbl is not None:
        right_block.extend([Spacer(1, 6), insurance_tbl])

    block_tbl = Table(
        [
            [
                [Paragraph("Patient Information (Non‑PHI)", h2), pat_tbl],
                right_block,
            ]
        ],
        colWidths=[112 * mm, 68 * mm],
    )
    block_tbl.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOX", (0, 0), (0, 0), 0.5, colors.HexColor("#e5e7eb")),
                ("BOX", (1, 0), (1, 0), 0.5, colors.HexColor("#e5e7eb")),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    story.append(_bookmark(block_tbl, key="patient", title="Patient & Diagnosis", level=0))
    story.append(Spacer(1, 14))

    story.append(_bookmark(Paragraph("Medication / Charges", h2), key="charges", title="Medication / Charges", level=0))

    # Line items table
    table_data: list[list[Any]] = [
        [
            Paragraph("<b>Code</b>", normal),
            Paragraph("<b>Description</b>", normal),
            Paragraph("<b>Qty</b>", right),
            Paragraph("<b>Unit Price</b>", right),
            Paragraph("<b>Total</b>", right),
        ]
    ]

    for it in items:
        table_data.append(
            [
                Paragraph(str(it.get("code") or "—"), normal),
                Paragraph(str(it.get("description", "") or "—"), normal),
                Paragraph(str(it.get("quantity", 1)), right),
                Paragraph(_fmt_inr(float(it.get("unit_price_inr", 0))), right),
                Paragraph(_fmt_inr(float(it.get("total_price_inr", 0))), right),
            ]
        )

    items_tbl = Table(
        table_data,
        colWidths=[20 * mm, 100 * mm, 10 * mm, 20 * mm, 22 * mm],
        repeatRows=1,
    )
    items_tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3f4f6")),
                ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#e5e7eb")),
                ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.HexColor("#e5e7eb")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.append(items_tbl)
    story.append(Spacer(1, 12))

    # Totals box (right aligned)
    story.append(_bookmark(Paragraph("Totals", h2), key="totals", title="Totals", level=0))
    subtotal = float(totals.get("subtotal_inr", 0))
    taxes = float(totals.get("taxes_inr", 0))
    discounts = float(totals.get("discounts_inr", 0))
    final_total = float(totals.get("final_total_inr", subtotal + taxes - discounts))

    insurance_covered = float(totals.get("insurance_covered_inr", 0) or 0)
    amount_due = float(totals.get("amount_due_inr", final_total) or 0)

    totals_rows: list[list[Any]] = [
        [Paragraph("Subtotal", normal), Paragraph(_fmt_inr(subtotal), right)],
        [Paragraph("Taxes", normal), Paragraph(_fmt_inr(taxes), right)],
        [Paragraph("Discounts", normal), Paragraph(f"- {_fmt_inr(discounts)}", right)],
    ]
    if insurance_covered:
        totals_rows.append([Paragraph("Insurance covered", normal), Paragraph(f"- {_fmt_inr(insurance_covered)}", right)])
    totals_rows.append([Paragraph("<b>Amount Due</b>", normal), Paragraph(f"<b>{_fmt_inr(amount_due)}</b>", right)])
    totals_tbl = Table(totals_rows, colWidths=[40 * mm, 40 * mm])
    totals_tbl.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#e5e7eb")),
                ("INNERGRID", (0, 0), (-1, -2), 0.25, colors.HexColor("#e5e7eb")),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LINEABOVE", (0, -1), (-1, -1), 0.5, colors.HexColor("#e5e7eb")),
            ]
        )
    )
    totals_outer = Table([[None, totals_tbl]], colWidths=[100 * mm, 80 * mm])
    totals_outer.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(_bookmark(totals_outer, key="totals_box", title="Totals (Summary)", level=1))
    story.append(Spacer(1, 14))

    footer = (
        "Payment: Payable within 7 days.<br/>"
        "This invoice is generated electronically and does not require a signature."
    )
    story.append(Paragraph(footer, normal))

    doc.build(story)
    return buf.getvalue()


def build_invoice_data(
    *,
    billing_safe_summary: dict[str, Any],
    insurance: dict[str, Any],
    invoice: dict[str, Any],
    line_items: list[InvoiceLineItem],
    hospital_name: str = "CityCare Hospital",
    hospital_address: str = "1 Healthcare Avenue, Sector 21, Bengaluru 560001",
    hospital_contact: str = "billing@citycare.example • +91 80 4000 0000",
    hospital_email: Optional[str] = "billing@citycare.example",
    hospital_phone: Optional[str] = "+91 80 4000 0000",
    hospital_website: Optional[str] = None,
    payment_url: Optional[str] = None,
) -> dict[str, Any]:
    """Create the normalized invoice render payload (PHI-safe)."""
    if contains_phi_keys(billing_safe_summary) or contains_phi_keys(invoice):
        raise ValueError("PHI keys detected in inputs; refusing to build invoice.")

    patient = {
        "patient_id": billing_safe_summary.get("patient_id"),
        "ward": billing_safe_summary.get("ward"),
        "admission_date": billing_safe_summary.get("admission_date"),
        "discharge_date": billing_safe_summary.get("discharge_date"),
        "los_days": billing_safe_summary.get("los_days"),
    }

    diagnosis = {
        "icd10_codes": billing_safe_summary.get("diagnosis_icd10", []) or [],
        "summary": "",
    }

    items = [
        {
            "code": (li.code or ""),
            "name": li.name,
            "description": li.description,
            "quantity": li.quantity,
            "unit_price_inr": round(li.unit_price_inr, 2),
            "total_price_inr": round(li.total_price_inr, 2),
        }
        for li in line_items
    ]

    subtotal = float(invoice.get("subtotal_inr") or invoice.get("subtotal") or 0)
    covered_amount = float((invoice.get("insurance") or {}).get("covered_amount") or 0)
    taxes = 0.0
    discounts = 0.0
    invoice_total = subtotal + taxes - discounts
    amount_due = float(invoice.get("patient_liability_inr") or invoice.get("patient_responsibility") or max(0, invoice_total - covered_amount))

    policy_number = str(insurance.get("policy_number") or "").strip()
    masked_policy = ""
    if policy_number:
        tail = policy_number[-4:] if len(policy_number) >= 4 else policy_number
        masked_policy = f"****{tail}"

    return {
        "hospital": {
            "hospital_name": hospital_name,
            "address": hospital_address,
            "contact": hospital_contact,
            "email": hospital_email,
            "phone": hospital_phone,
            "website": hospital_website,
        },
        "invoice": {
            "invoice_id": invoice.get("invoice_id") or f"INV-{patient['patient_id']}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
            "invoice_date": (invoice.get("generated_at") or datetime.utcnow().isoformat())[:10],
            "payment_url": payment_url,
        },
        "insurance": {
            "has_insurance": bool(insurance.get("has_insurance")),
            "insurer_name": insurance.get("insurer_name") or (invoice.get("insurance") or {}).get("insurer_name"),
            "plan_type": insurance.get("plan_type"),
            "policy_number_masked": masked_policy,
            "deductible_inr": insurance.get("deductible_inr"),
            "copay_inr": insurance.get("copay_inr"),
            "max_covered_per_admission_inr": insurance.get("max_covered_per_admission_inr"),
            "covered_amount_inr": covered_amount,
        },
        "patient": patient,
        "diagnosis": diagnosis,
        "items": items,
        "totals": {
            "subtotal_inr": round(subtotal, 2),
            "taxes_inr": round(taxes, 2),
            "discounts_inr": round(discounts, 2),
            "invoice_total_inr": round(invoice_total, 2),
            "insurance_covered_inr": round(covered_amount, 2),
            "amount_due_inr": round(amount_due, 2),
            # Back-compat for existing templates
            "final_total_inr": round(amount_due, 2),
        },
    }
