"""
Broker/Connection Errors (1000-1999)

Author: client Options Trading Engine
"""

from typing import Dict
from .base import clientError


class BrokerError(clientError):
    """Base class for broker-related errors"""
    pass


class ConnectionError(BrokerError):
    """Failed to connect to IBKR"""
    def __init__(self, message: str = "Failed to connect to IBKR", context: Dict = None):
        super().__init__(1001, message, context)


class ConnectionTimeoutError(BrokerError):
    """Connection attempt timed out"""
    def __init__(self, timeout: float, context: Dict = None):
        ctx = {"timeout_seconds": timeout, **(context or {})}
        super().__init__(1002, f"Connection timed out after {timeout}s", ctx)


class DisconnectedError(BrokerError):
    """Lost connection to broker"""
    def __init__(self, message: str = "Disconnected from IBKR", context: Dict = None):
        super().__init__(1003, message, context)


class AuthenticationError(BrokerError):
    """Authentication failed"""
    def __init__(self, message: str = "IBKR authentication failed", context: Dict = None):
        super().__init__(1004, message, context)
