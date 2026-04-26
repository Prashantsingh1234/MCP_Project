"""Lightweight tests for chatbot parsing/intents (no MCP servers required)."""

from __future__ import annotations

import unittest

from src.chatbot.intent_classifier import classify_intent
from src.chatbot.validator import extract_patient_ids, extract_drug_name, validate_message


class TestChatbotParsing(unittest.TestCase):
    def test_meds_fetch_intent(self) -> None:
        self.assertEqual(classify_intent("What are the prescribed medicines for PAT-001?"), "meds_fetch")

    def test_meds_stock_followup_intent(self) -> None:
        self.assertEqual(classify_intent("Check if these medicines are available"), "meds_stock_check")
        self.assertEqual(classify_intent("Give me full availability summary"), "meds_stock_check")
        self.assertEqual(classify_intent("Tell me which medicines are not in stock"), "meds_stock_check")
        self.assertEqual(classify_intent("Which medications are out of stock?"), "meds_stock_check")

    def test_single_drug_stock_intent(self) -> None:
        self.assertEqual(classify_intent("Check if Humira is available"), "stock_check")
        self.assertEqual(extract_drug_name("Check if Humira is available"), "Humira")
        self.assertEqual(extract_drug_name("Is Furosemide available?"), "Furosemide")
        self.assertEqual(extract_drug_name("Proceed with Semaglutide 0.5mg"), "Semaglutide")

    def test_patient_id_extraction(self) -> None:
        self.assertEqual(extract_patient_ids("Discharge PAT-001 and generate invoice"), ["PAT-001"])
        self.assertEqual(extract_patient_ids("PAT-001, PAT-002"), ["PAT-001", "PAT-002"])
        # Ordinal references are resolved later, but intent should be discharge_workflow-friendly
        self.assertEqual(classify_intent("Discharge third patient"), "discharge_workflow")

    def test_validate_message_meds_fetch_requires_patient(self) -> None:
        with self.assertRaises(ValueError):
            validate_message("What medicines are prescribed?", "meds_fetch")


if __name__ == "__main__":
    unittest.main()
