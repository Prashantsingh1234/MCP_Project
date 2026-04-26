"""Tests for ordinal patient reference parsing."""

from __future__ import annotations

import unittest

from src.chatbot.validator import extract_patient_ordinal, patient_id_from_ordinal


class TestPatientOrdinalResolution(unittest.TestCase):
    def test_word_ordinals(self) -> None:
        self.assertEqual(extract_patient_ordinal("discharge third patient"), 3)
        self.assertEqual(patient_id_from_ordinal(3), "PAT-003")

    def test_numeric_ordinals(self) -> None:
        self.assertEqual(extract_patient_ordinal("discharge 4th patient"), 4)
        self.assertEqual(extract_patient_ordinal("discharge patient 12"), 12)
        self.assertEqual(extract_patient_ordinal("discharge patient #7"), 7)


if __name__ == "__main__":
    unittest.main()

