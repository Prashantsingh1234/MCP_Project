"""Security / RBAC MCP Server for MCPDischarge.

Exposes RBAC access-control tools so the LLM and agents can query and audit
the permission layer without touching internal Python objects directly.
"""

import logging
from typing import Any, Optional
from datetime import datetime

from src.utils.rbac import get_rbac_engine, TOOL_PERMISSIONS
from src.utils.telemetry import get_telemetry

logger = logging.getLogger(__name__)


class SecurityServer:
    """Security server: RBAC validation, permission queries, and access-log retrieval."""

    def __init__(self):
        self.rbac = get_rbac_engine()
        self.telemetry = get_telemetry()
        logger.info("Security Server initialized")

    def check_access(self, role: str, server: str, tool: str) -> dict[str, Any]:
        """Check whether a role is permitted to call a tool on a server.

        Args:
            role: Role to check (e.g. discharge_coordinator).
            server: Target server (ehr, pharmacy, billing, security, telemetry).
            tool: Tool name to check.

        Returns:
            Dict with allowed (bool) and resolved permission string.
        """
        permission = TOOL_PERMISSIONS.get(tool, tool)
        role_perms = self.rbac.policies.get(role, {})
        server_perms = role_perms.get(server, [])
        allowed = permission in server_perms

        if not allowed:
            self.telemetry.record_alert(
                "WARNING", "Security",
                f"Access check failed: {role} → {server}.{tool}",
                {"role": role, "server": server, "tool": tool}
            )

        return {
            "role": role,
            "server": server,
            "tool": tool,
            "required_permission": permission,
            "allowed": allowed,
            "checked_at": datetime.utcnow().isoformat(),
        }

    def get_role_permissions(self, role: str) -> dict[str, Any]:
        """Return all permissions granted to a role across every server.

        Args:
            role: Role to query.

        Returns:
            Dict of server → list[permission] plus all callable tools.
        """
        role_perms = self.rbac.policies.get(role, {})

        callable_tools: dict[str, list[str]] = {}
        for srv, perms in role_perms.items():
            callable_tools[srv] = [
                t for t, p in TOOL_PERMISSIONS.items() if p in perms
            ]

        return {
            "role": role,
            "permissions_by_server": role_perms,
            "callable_tools_by_server": callable_tools,
            "known_roles": list(self.rbac.policies.keys()),
        }

    def log_rbac_violation(self, role: str, tool: str, server: str,
                           patient_id: Optional[str] = None) -> dict[str, Any]:
        """Manually log an RBAC violation (for external auditing or testing).

        Args:
            role: Role that attempted access.
            tool: Tool that was blocked.
            server: Server of the blocked tool.
            patient_id: Optional patient context.

        Returns:
            Confirmation with timestamp.
        """
        self.telemetry.record_rbac_violation(role, server, tool, patient_id)
        self.rbac._log_violation(role, server, tool, patient_id)
        return {
            "logged": True,
            "role": role,
            "server": server,
            "tool": tool,
            "patient_id": patient_id,
            "timestamp": datetime.utcnow().isoformat(),
        }

    def get_access_logs(self, limit: int = 50) -> dict[str, Any]:
        """Retrieve recent RBAC violation audit logs.

        Args:
            limit: Maximum number of entries to return.

        Returns:
            Dict with violation log and summary counts.
        """
        violations = self.telemetry.get_rbac_violations()[-limit:]
        engine_violations = self.rbac.get_violations()[-limit:]

        return {
            "total_violations": len(self.telemetry.get_rbac_violations()),
            "recent_violations": violations,
            "engine_violation_log": engine_violations,
            "limit": limit,
        }


_security_server: Optional[SecurityServer] = None


def get_security_server() -> SecurityServer:
    global _security_server
    if _security_server is None:
        _security_server = SecurityServer()
    return _security_server
