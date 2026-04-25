"""Telemetry module for MCPDischarge.

Tracks MCP call counts, alerts, RBAC violations, and system metrics.
"""

import logging
import time
from typing import Optional, Any
from datetime import datetime
from dataclasses import dataclass, field
from collections import defaultdict
import threading

logger = logging.getLogger(__name__)


@dataclass
class MCPCall:
    """Represents a single MCP tool call."""
    timestamp: str
    server: str
    tool: str
    role: str
    patient_id: Optional[str]
    duration_ms: float
    success: bool
    error: Optional[str] = None


@dataclass
class Alert:
    """Represents a system alert."""
    timestamp: str
    level: str  # INFO, WARNING, ERROR, CRITICAL
    source: str
    message: str
    details: Optional[dict] = None


class Telemetry:
    """Telemetry collector for MCPDischarge system."""
    
    _instance: Optional['Telemetry'] = None
    _lock = threading.Lock()
    
    def __new__(cls):
        """Singleton pattern for telemetry."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        """Initialize telemetry collectors."""
        if self._initialized:
            return
            
        self._initialized = True
        self._call_counts: dict[str, int] = defaultdict(int)
        self._server_call_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._role_call_counts: dict[str, int] = defaultdict(int)
        self._alerts: list[Alert] = []
        self._rbac_violations: list[dict] = []
        self._calls: list[MCPCall] = []
        self._start_time = datetime.utcnow()
        self._lock = threading.Lock()
        
        logger.info("Telemetry initialized")
    
    def record_call(self, server: str, tool: str, role: str, 
                   patient_id: Optional[str], duration_ms: float,
                   success: bool, error: Optional[str] = None):
        """Record an MCP tool call.
        
        Args:
            server: Target server (ehr, pharmacy, billing).
            tool: Tool name called.
            role: Role making the call.
            patient_id: Patient ID if applicable.
            duration_ms: Call duration in milliseconds.
            success: Whether the call succeeded.
            error: Error message if failed.
        """
        with self._lock:
            call = MCPCall(
                timestamp=datetime.utcnow().isoformat(),
                server=server,
                tool=tool,
                role=role,
                patient_id=patient_id,
                duration_ms=duration_ms,
                success=success,
                error=error
            )
            self._calls.append(call)
            self._call_counts[tool] += 1
            self._server_call_counts[server][tool] += 1
            self._role_call_counts[role] += 1
            
            logger.debug(f"MCP Call recorded: {server}.{tool} by {role} - {'OK' if success else 'FAILED'}")
    
    def record_alert(self, level: str, source: str, message: str, 
                    details: Optional[dict] = None):
        """Record an alert.
        
        Args:
            level: Alert level (INFO, WARNING, ERROR, CRITICAL).
            source: Source of the alert.
            message: Alert message.
            details: Additional details.
        """
        with self._lock:
            alert = Alert(
                timestamp=datetime.utcnow().isoformat(),
                level=level,
                source=source,
                message=message,
                details=details
            )
            self._alerts.append(alert)
            
            log_method = {
                "INFO": logger.info,
                "WARNING": logger.warning,
                "ERROR": logger.error,
                "CRITICAL": logger.critical
            }.get(level, logger.info)
            
            log_method(f"ALERT [{level}] {source}: {message}")
    
    def record_rbac_violation(self, role: str, server: str, tool: str,
                             patient_id: Optional[str] = None):
        """Record an RBAC violation.
        
        Args:
            role: Role that was denied.
            server: Target server.
            tool: Tool that was denied.
            patient_id: Patient ID if applicable.
        """
        with self._lock:
            violation = {
                "timestamp": datetime.utcnow().isoformat(),
                "role": role,
                "server": server,
                "tool": tool,
                "patient_id": patient_id
            }
            self._rbac_violations.append(violation)
            self.record_alert(
                "WARNING",
                "RBAC",
                f"Access denied: {role} attempted {server}.{tool}",
                violation
            )
    
    def get_call_counts(self) -> dict[str, int]:
        """Get call counts by tool."""
        with self._lock:
            return dict(self._call_counts)
    
    def get_server_call_counts(self, server: Optional[str] = None) -> dict:
        """Get call counts by server or for a specific server."""
        with self._lock:
            if server:
                return dict(self._server_call_counts.get(server, {}))
            return {k: dict(v) for k, v in self._server_call_counts.items()}
    
    def get_role_call_counts(self) -> dict[str, int]:
        """Get call counts by role."""
        with self._lock:
            return dict(self._role_call_counts)
    
    def get_alerts(self, level: Optional[str] = None) -> list[Alert]:
        """Get alerts, optionally filtered by level."""
        with self._lock:
            if level:
                return [a for a in self._alerts if a.level == level]
            return self._alerts.copy()
    
    def get_rbac_violations(self) -> list[dict]:
        """Get all RBAC violations."""
        with self._lock:
            return self._rbac_violations.copy()
    
    def get_calls(self, limit: Optional[int] = None) -> list[MCPCall]:
        """Get recent MCP calls."""
        with self._lock:
            if limit:
                return self._calls[-limit:]
            return self._calls.copy()
    
    def get_summary(self) -> dict[str, Any]:
        """Get telemetry summary."""
        with self._lock:
            total_calls = len(self._calls)
            successful_calls = sum(1 for c in self._calls if c.success)
            failed_calls = total_calls - successful_calls
            avg_duration = (
                sum(c.duration_ms for c in self._calls) / total_calls 
                if total_calls > 0 else 0
            )
            
            return {
                "uptime_seconds": (datetime.utcnow() - self._start_time).total_seconds(),
                "total_calls": total_calls,
                "successful_calls": successful_calls,
                "failed_calls": failed_calls,
                "success_rate_pct": (successful_calls / total_calls * 100) if total_calls > 0 else 0,
                "avg_duration_ms": avg_duration,
                "calls_by_tool": dict(self._call_counts),
                "calls_by_server": {k: dict(v) for k, v in self._server_call_counts.items()},
                "calls_by_role": dict(self._role_call_counts),
                "total_alerts": len(self._alerts),
                "alerts_by_level": self._count_by_level(self._alerts),
                "total_rbac_violations": len(self._rbac_violations)
            }
    
    @staticmethod
    def _count_by_level(items: list) -> dict[str, int]:
        """Count items by level."""
        counts = defaultdict(int)
        for item in items:
            counts[item.level] += 1
        return dict(counts)
    
    def reset(self):
        """Reset all telemetry data."""
        with self._lock:
            self._call_counts.clear()
            self._server_call_counts.clear()
            self._role_call_counts.clear()
            self._alerts.clear()
            self._rbac_violations.clear()
            self._calls.clear()
            self._start_time = datetime.utcnow()
            logger.info("Telemetry reset")


# Global telemetry instance
_telemetry: Optional[Telemetry] = None


def get_telemetry() -> Telemetry:
    """Get the global telemetry instance."""
    global _telemetry
    if _telemetry is None:
        _telemetry = Telemetry()
    return _telemetry


class MCPCallTimer:
    """Context manager for timing MCP calls."""
    
    def __init__(self, server: str, tool: str, role: str, 
                 patient_id: Optional[str] = None):
        self.server = server
        self.tool = tool
        self.role = role
        self.patient_id = patient_id
        self.start_time = 0.0
        self.success = False
        self.error: Optional[str] = None
    
    def __enter__(self):
        self.start_time = time.perf_counter()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        duration_ms = (time.perf_counter() - self.start_time) * 1000
        
        if exc_type is not None:
            self.success = False
            self.error = str(exc_val)
        else:
            self.success = True
        
        get_telemetry().record_call(
            server=self.server,
            tool=self.tool,
            role=self.role,
            patient_id=self.patient_id,
            duration_ms=duration_ms,
            success=self.success,
            error=self.error
        )
        
        return False  # Don't suppress exceptions