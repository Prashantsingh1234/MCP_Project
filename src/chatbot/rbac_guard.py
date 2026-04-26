"""RBAC enforcement for the chatbot (do not trust user-provided role)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.utils.rbac import get_rbac_engine
from src.utils.exceptions import RBACError


@dataclass(frozen=True)
class ActorContext:
    """Authenticated actor context.

    In production, this would be derived from auth (session/JWT).
    """

    role: str


class RBACGuard:
    def __init__(self):
        self.rbac = get_rbac_engine()

    def ensure_allowed(self, actor: ActorContext, server: str, tool: str, patient_id: Optional[str] = None) -> None:
        self.rbac.check_permission(actor.role, server, tool, patient_id)

    def deny_sensitive(self) -> None:
        raise RBACError(
            "Access denied.\n\nYou are not authorized to view full clinical discharge summaries.",
            role="end_user",
            tool="get_patient_discharge_summary",
            server="ehr",
        )

