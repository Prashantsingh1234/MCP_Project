"""MCPDischarge - Hospital Discharge Workflow Automation

Production-grade MCP-based multi-agent system for hospital discharge workflows.
"""

__version__ = "1.0.0"

from src.utils.rbac import RBACEngine, RBACError
from src.utils.telemetry import Telemetry
from src.utils.exceptions import (
    MCPDischargeError,
    StockUnavailableError,
    DoseConflictError,
    PHIBoundaryViolationError,
)

__all__ = [
    "RBACEngine",
    "RBACError",
    "Telemetry",
    "MCPDischargeError",
    "StockUnavailableError",
    "DoseConflictError",
    "PHIBoundaryViolationError",
]