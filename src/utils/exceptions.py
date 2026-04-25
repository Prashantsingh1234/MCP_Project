"""Custom exceptions for MCPDischarge system."""

from typing import Optional, Any


class MCPDischargeError(Exception):
    """Base exception for MCPDischarge system."""
    
    def __init__(self, message: str, details: Optional[dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class RBACError(MCPDischargeError):
    """Raised when RBAC check fails."""
    
    def __init__(self, message: str, role: Optional[str] = None, 
                 tool: Optional[str] = None, server: Optional[str] = None):
        details = {}
        if role:
            details["role"] = role
        if tool:
            details["tool"] = tool
        if server:
            details["server"] = server
        super().__init__(message, details)


class StockUnavailableError(MCPDischargeError):
    """Raised when medication is out of stock."""
    
    def __init__(self, drug_name: str, patient_id: str, 
                 alternatives: Optional[list[str]] = None):
        message = f"Drug '{drug_name}' unavailable for patient '{patient_id}'"
        details = {"drug_name": drug_name, "patient_id": patient_id}
        if alternatives:
            details["alternatives"] = alternatives
        super().__init__(message, details)
        self.drug_name = drug_name
        self.patient_id = patient_id
        self.alternatives = alternatives or []


class DoseConflictError(MCPDischargeError):
    """Raised when there's a dose conflict between medications."""
    
    def __init__(self, drug1: str, drug2: str, reason: str):
        message = f"Dose conflict: {drug1} and {drug2} - {reason}"
        details = {"drug1": drug1, "drug2": drug2, "reason": reason}
        super().__init__(message, details)
        self.drug1 = drug1
        self.drug2 = drug2
        self.reason = reason


class PHIBoundaryViolationError(MCPDischargeError):
    """Raised when PHI boundary is violated."""
    
    def __init__(self, field: str, server: str, payload_sample: Optional[dict] = None):
        message = f"PHI field '{field}' detected in {server} payload"
        details = {"phi_field": field, "server": server}
        if payload_sample:
            details["payload_sample"] = payload_sample
        super().__init__(message, details)
        self.field = field
        self.server = server


class MCPConnectionError(MCPDischargeError):
    """Raised when MCP server connection fails."""
    
    def __init__(self, server: str, reason: str):
        message = f"Failed to connect to {server}: {reason}"
        details = {"server": server, "reason": reason}
        super().__init__(message, details)
        self.server = server


class ToolExecutionError(MCPDischargeError):
    """Raised when MCP tool execution fails."""
    
    def __init__(self, tool: str, server: str, reason: str):
        message = f"Tool '{tool}' failed on {server}: {reason}"
        details = {"tool": tool, "server": server, "reason": reason}
        super().__init__(message, details)
        self.tool = tool
        self.server = server