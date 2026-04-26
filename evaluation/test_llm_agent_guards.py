"""Offline tests for LLM agent guardrails (no Azure/OpenAI calls)."""

from __future__ import annotations

import unittest

from src.chatbot.llm_agent import ALLOWED_BILLING_SAFE_KEYS


class TestLLMAgentGuards(unittest.TestCase):
    def test_allowed_billing_safe_keys(self) -> None:
        self.assertIn("patient_id", ALLOWED_BILLING_SAFE_KEYS)
        self.assertIn("diagnosis_icd10", ALLOWED_BILLING_SAFE_KEYS)
        self.assertNotIn("name", ALLOWED_BILLING_SAFE_KEYS)
        self.assertNotIn("mrn", ALLOWED_BILLING_SAFE_KEYS)


if __name__ == "__main__":
    unittest.main()

