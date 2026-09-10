"""
Risk Metrics Data Class

Author: client Options Trading Engine
"""

from dataclasses import dataclass


@dataclass
class RiskMetrics:
    """Risk metrics for a position"""
    entry_spread: float
    target_spread: float
    stop_spread: float
    max_loss_per_contract: float
    max_profit_per_contract: float
    risk_reward_ratio: float
    contracts: int
    total_risk: float
    max_risk: float
    max_reward: float
    is_credit: bool
