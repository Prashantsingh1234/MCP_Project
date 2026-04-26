"""Regression tests for LLM tool-name normalization."""

from __future__ import annotations

import unittest


def _normalize_tool_name(name: str) -> str:
    tool_name_norm = str(name or "").strip()
    for _ in range(2):
        if (tool_name_norm.startswith("'") and tool_name_norm.endswith("'")) or (
            tool_name_norm.startswith('"') and tool_name_norm.endswith('"')
        ):
            tool_name_norm = tool_name_norm[1:-1].strip()
    return tool_name_norm


class TestLLMAgentToolNameNormalization(unittest.TestCase):
    def test_strips_quotes(self) -> None:
        self.assertEqual(_normalize_tool_name("'list_in_stock_drugs'"), "list_in_stock_drugs")
        self.assertEqual(_normalize_tool_name('"check_stock"'), "check_stock")

    def test_strips_nested_quotes(self) -> None:
        self.assertEqual(_normalize_tool_name(" ''get_price'' "), "get_price")


if __name__ == "__main__":
    unittest.main()

