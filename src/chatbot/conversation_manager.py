"""In-memory conversation state for multi-turn flows.

This stores non-PHI state only:
- last_patient_id
- last medication list (from EHR discharge medications)

For production, swap this with a persistent store (Redis) keyed by a secure
session/user identifier.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ConversationState:
    last_patient_id: Optional[str] = None
    last_drug_name: Optional[str] = None
    last_unavailable_drug_name: Optional[str] = None
    last_discharge_context: dict[str, Any] = field(default_factory=dict)
    patient_history: list[str] = field(default_factory=list)
    medications: list[dict[str, Any]] = field(default_factory=list)
    billing_safe_summary: dict[str, Any] = field(default_factory=dict)
    last_stock_check: dict[str, Any] = field(default_factory=dict)
    last_invoice: dict[str, Any] = field(default_factory=dict)
    # Conversation history for multi-turn LLM context (non-PHI only, capped at 40 entries)
    chat_history: list[dict[str, Any]] = field(default_factory=list)
    mcp_call_count_total: int = 0
    mcp_call_count_last: int = 0
    mcp_call_count_by_server_total: dict[str, int] = field(default_factory=dict)
    mcp_call_count_by_server_last: dict[str, int] = field(default_factory=dict)


class ConversationManager:
    def __init__(self):
        self._store: dict[str, ConversationState] = {}

    def get(self, conversation_id: str) -> ConversationState:
        if conversation_id not in self._store:
            self._store[conversation_id] = ConversationState()
        return self._store[conversation_id]

    def set_patient(self, conversation_id: str, patient_id: str) -> None:
        self.get(conversation_id).last_patient_id = patient_id

    def set_medications(self, conversation_id: str, patient_id: str, meds: list[dict[str, Any]]) -> None:
        st = self.get(conversation_id)
        st.last_patient_id = patient_id
        st.medications = meds or []

    def clear(self, conversation_id: str) -> None:
        self._store.pop(conversation_id, None)
