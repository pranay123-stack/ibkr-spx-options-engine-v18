"""
Order/Execution Errors (4000-4999)

Author: client Options Trading Engine
"""

from typing import Dict
from .base import clientError


class ExecutionError(clientError):
    """Base class for execution-related errors"""
    pass


class OrderSubmitError(ExecutionError):
    """Failed to submit order"""
    def __init__(self, order_id: int = None, reason: str = None, context: Dict = None):
        ctx = {"order_id": order_id, "reason": reason, **(context or {})}
        super().__init__(4001, f"Failed to submit order: {reason}", ctx)


class OrderRejectError(ExecutionError):
    """Order was rejected by broker"""
    def __init__(self, order_id: int, reject_reason: str, context: Dict = None):
        ctx = {"order_id": order_id, "reject_reason": reject_reason, **(context or {})}
        super().__init__(4002, f"Order {order_id} rejected: {reject_reason}", ctx)


class OrderTimeoutError(ExecutionError):
    """Order fill timed out"""
    def __init__(self, order_id: int, timeout: float, context: Dict = None):
        ctx = {"order_id": order_id, "timeout_seconds": timeout, **(context or {})}
        super().__init__(4003, f"Order {order_id} timed out after {timeout}s", ctx)


class PartialFillError(ExecutionError):
    """Order only partially filled"""
    def __init__(self, order_id: int, filled: int, total: int, context: Dict = None):
        ctx = {"order_id": order_id, "filled": filled, "total": total, **(context or {})}
        super().__init__(4004, f"Order {order_id} partial fill: {filled}/{total}", ctx)


class SpreadExecutionError(ExecutionError):
    """Failed to execute spread"""
    def __init__(self, combo_id: str, legs_filled: int, total_legs: int, context: Dict = None):
        ctx = {"combo_id": combo_id, "legs_filled": legs_filled, "total_legs": total_legs, **(context or {})}
        super().__init__(4005, f"Spread {combo_id} failed: {legs_filled}/{total_legs} legs filled", ctx)


class OrderCancelError(ExecutionError):
    """Failed to cancel order"""
    def __init__(self, order_id: int, reason: str = None, context: Dict = None):
        ctx = {"order_id": order_id, "reason": reason, **(context or {})}
        super().__init__(4006, f"Failed to cancel order {order_id}", ctx)
