"""Smoke tests for interactive invoice PDF features.

These tests are best-effort and skip if ReportLab isn't installed.
"""

from __future__ import annotations

import unittest


class TestInvoicePDFInteractive(unittest.TestCase):
    def test_pdf_contains_payment_link_uri(self) -> None:
        try:
            import reportlab  # noqa: F401
        except Exception:
            self.skipTest("reportlab not installed")

        from src.gateway.invoice_pdf import generate_invoice_pdf

        payment_url = "https://payments.example/pay/INV-TEST"
        payload = {
            "hospital": {
                "hospital_name": "CityCare Hospital",
                "address": "1 Healthcare Avenue",
                "email": "billing@citycare.example",
                "phone": "+91 80 4000 0000",
                "website": "citycare.example",
            },
            "invoice": {
                "invoice_id": "INV-TEST",
                "invoice_date": "2026-04-26",
                "payment_url": payment_url,
            },
            "patient": {
                "patient_id": "PAT-001",
                "ward": "Cardiology",
                "admission_date": "2026-04-20",
                "discharge_date": "2026-04-26",
                "los_days": 6,
            },
            "diagnosis": {"icd10_codes": ["I10"], "summary": ""},
            "items": [
                {
                    "name": "Paracetamol",
                    "description": "Tab",
                    "quantity": 1,
                    "unit_price_inr": 10,
                    "total_price_inr": 10,
                }
            ],
            "totals": {
                "subtotal_inr": 10,
                "taxes_inr": 0,
                "discounts_inr": 0,
                "final_total_inr": 10,
            },
        }

        pdf = generate_invoice_pdf(payload)
        self.assertGreater(len(pdf), 5000)
        self.assertIn(b"/URI", pdf)
        self.assertIn(payment_url.encode("utf-8"), pdf)


if __name__ == "__main__":
    unittest.main()

