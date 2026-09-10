"""
System/Critical Errors (9000-9999)

Author: client Options Trading Engine
"""

from typing import Dict
from .base import clientError


class SystemError(clientError):
    """Base class for system-level errors"""
    pass


class CriticalError(SystemError):
    """Critical error requiring immediate attention"""
    def __init__(self, message: str, context: Dict = None):
        super().__init__(9001, f"CRITICAL: {message}", context)


class EmergencyShutdownError(SystemError):
    """Emergency shutdown triggered"""
    def __init__(self, reason: str, context: Dict = None):
        ctx = {"reason": reason, **(context or {})}
        super().__init__(9002, f"Emergency shutdown: {reason}", ctx)


class StateError(SystemError):
    """Invalid engine state"""
    def __init__(self, current_state: str, expected_state: str, context: Dict = None):
        ctx = {"current_state": current_state, "expected_state": expected_state, **(context or {})}
        super().__init__(9003, f"Invalid state: {current_state} (expected {expected_state})", ctx)
