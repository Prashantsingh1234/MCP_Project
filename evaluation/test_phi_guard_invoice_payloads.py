"""Regression tests for PHI guard behavior on invoice-like payloads.

These tests ensure we don't falsely treat non-PHI item/drug "name" fields as PHI,
while still stripping patient/person names in patient contexts.
"""

from __future__ import annotations

import unittest

from src.chatbot.phi_guard import contains_phi_keys, strip_phi


class TestPHIGuardInvoicePayloads(unittest.TestCase):
    def test_invoice_items_name_is_not_phi(self) -> None:
        payload = {
            "hospital": {"hospital_name": "X", "address": "Y", "contact": "Z"},
            "invoice": {"invoice_id": "INV-1", "invoice_date": "2026-04-26"},
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

        self.assertFalse(contains_phi_keys(payload))
        safe = strip_phi(payload)
        self.assertFalse(contains_phi_keys(safe))
        self.assertEqual(safe["items"][0]["name"], "Paracetamol")

    def test_patient_name_is_stripped_in_patient_context(self) -> None:
        payload = {
            "patient": {
                "patient_id": "PAT-001",
                "mrn": "MRN-123",
                "dob": "1990-01-01",
                "name": "John Doe",
                "ward": "Cardiology",
            }
        }

        self.assertTrue(contains_phi_keys(payload))
        safe = strip_phi(payload)
        self.assertFalse(contains_phi_keys(safe))
        self.assertNotIn("name", safe["patient"])
        self.assertNotIn("mrn", safe["patient"])
        self.assertNotIn("dob", safe["patient"])


if __name__ == "__main__":
    unittest.main()

