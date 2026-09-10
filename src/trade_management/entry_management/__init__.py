"""
Entry Module - Strategy Building and Order Execution

Author: client Options Trading Engine
"""

from .strategy_builder import StrategyBuilder
from .spread_executor import SpreadExecutor

# Re-export dataclasses from data_classes for backward compatibility
from ...data_classes import BuiltLeg, BuiltStrategy, ExecutionResult

__all__ = [
    "StrategyBuilder",
    "SpreadExecutor",
    "BuiltLeg",
    "BuiltStrategy",
    "ExecutionResult",
]
