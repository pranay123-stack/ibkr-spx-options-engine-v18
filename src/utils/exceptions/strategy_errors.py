"""
Strategy Errors (3000-3999)

Author: client Options Trading Engine
"""

from typing import Dict
from .base import clientError


class StrategyError(clientError):
    """Base class for strategy-related errors"""
    pass


class StrategyNotFoundError(StrategyError):
    """Strategy not found in configuration"""
    def __init__(self, strategy_id: str, context: Dict = None):
        ctx = {"strategy_id": strategy_id, **(context or {})}
        super().__init__(3001, f"Strategy '{strategy_id}' not found", ctx)


class StrategyBuildError(StrategyError):
    """Failed to build strategy"""
    def __init__(self, strategy_id: str, reason: str, context: Dict = None):
        ctx = {"strategy_id": strategy_id, "reason": reason, **(context or {})}
        super().__init__(3002, f"Failed to build strategy '{strategy_id}': {reason}", ctx)


class LegBuildError(StrategyError):
    """Failed to build strategy leg"""
    def __init__(self, leg_index: int, option_type: str, reason: str, context: Dict = None):
        ctx = {"leg_index": leg_index, "option_type": option_type, "reason": reason, **(context or {})}
        super().__init__(3003, f"Failed to build leg {leg_index} ({option_type}): {reason}", ctx)


class NoStrategyMatchError(StrategyError):
    """No strategy matches current conditions"""
    def __init__(self, direction: str, vol_regime: str, trend_regime: str, context: Dict = None):
        ctx = {"direction": direction, "vol_regime": vol_regime, "trend_regime": trend_regime, **(context or {})}
        super().__init__(3004, f"No strategy for {direction}/{vol_regime}/{trend_regime}", ctx)


class InvalidStrategyConfigError(StrategyError):
    """Strategy configuration is invalid"""
    def __init__(self, strategy_id: str, issue: str, context: Dict = None):
        ctx = {"strategy_id": strategy_id, "issue": issue, **(context or {})}
        super().__init__(3005, f"Invalid config for '{strategy_id}': {issue}", ctx)
