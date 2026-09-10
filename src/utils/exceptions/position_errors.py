"""
Position/Risk Errors (5000-5999)

Author: client Options Trading Engine
"""

from typing import Dict
from .base import clientError


class PositionError(clientError):
    """Base class for position-related errors"""
    pass


class NoPositionError(PositionError):
    """No position exists when expected"""
    def __init__(self, message: str = "No open position", context: Dict = None):
        super().__init__(5001, message, context)


class PositionAlreadyExistsError(PositionError):
    """Position already exists"""
    def __init__(self, position_id: str, context: Dict = None):
        ctx = {"position_id": position_id, **(context or {})}
        super().__init__(5002, f"Position already exists: {position_id}", ctx)


class RiskLimitExceededError(PositionError):
    """Risk limit exceeded"""
    def __init__(self, limit_type: str, limit_value: float, actual_value: float, context: Dict = None):
        ctx = {"limit_type": limit_type, "limit": limit_value, "actual": actual_value, **(context or {})}
        super().__init__(5003, f"Risk limit exceeded: {limit_type} ({actual_value} > {limit_value})", ctx)


class InsufficientCapitalError(PositionError):
    """Insufficient capital for trade"""
    def __init__(self, required: float, available: float, context: Dict = None):
        ctx = {"required": required, "available": available, **(context or {})}
        super().__init__(5004, f"Insufficient capital: need ${required:.2f}, have ${available:.2f}", ctx)


class InvalidPositionSizeError(PositionError):
    """Invalid position size"""
    def __init__(self, contracts: int, reason: str, context: Dict = None):
        ctx = {"contracts": contracts, "reason": reason, **(context or {})}
        super().__init__(5005, f"Invalid position size ({contracts}): {reason}", ctx)
