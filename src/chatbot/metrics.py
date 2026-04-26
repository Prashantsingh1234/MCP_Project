"""Per-request metrics for the chatbot."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class RequestMetrics:
    patient_id: Optional[str] = None
    mcp_call_count: int = 0
    mcp_call_count_by_server: dict[str, int] = field(default_factory=dict)
    alerts: list[dict[str, Any]] = field(default_factory=list)
    rbac_violations: int = 0

    def add_alert(self, alert: dict[str, Any]) -> None:
        self.alerts.append(alert)
