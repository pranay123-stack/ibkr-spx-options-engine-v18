"""
Credit Call Spread (Bear Call Spread) Strategy
Bearish strategy that profits from time decay and downward movement
"""

from typing import List

from .base import BaseStrategy, LegDefinition, StrategyType
from ...enums import OptionType, OrderSide, ExpiryRule


class CreditCallSpread(BaseStrategy):
    """
    Credit Call Spread (Bear Call Spread)

    Structure:
    - Sell OTM Call (lower strike, closer to money)
    - Buy further OTM Call (higher strike, protection)

    Outlook: Bearish to neutral
    Risk: Limited (strike width - premium received)
    Reward: Limited (premium received)
    Breakeven: Short strike + premium received
    """

    def __init__(
        self,
        short_delta: float = 0.25,
        long_delta: float = 0.10,
        expiry_rule: ExpiryRule = ExpiryRule.SAME_DAY
    ):
        """
        Initialize Credit Call Spread.

        Args:
            short_delta: Delta for short call (closer to ATM)
            long_delta: Delta for long call (further OTM)
            expiry_rule: Expiration rule for legs
        """
        super().__init__(
            short_delta=short_delta,
            long_delta=long_delta,
            expiry_rule=expiry_rule
        )
        self.short_delta = short_delta
        self.long_delta = long_delta
        self.expiry_rule = expiry_rule

    @property
    def strategy_id(self) -> str:
        return "STRAT_CREDIT_CALL"

    @property
    def strategy_type(self) -> StrategyType:
        return StrategyType.CREDIT_SPREAD

    @property
    def description(self) -> str:
        return "Bear Call Spread (Credit Call Spread)"

    @property
    def is_credit(self) -> bool:
        return True

    def get_legs(self) -> List[LegDefinition]:
        """
        Returns legs for credit call spread:
        1. Sell higher delta call (closer to money)
        2. Buy lower delta call (further OTM, protection)
        """
        return [
            LegDefinition(
                leg_index=1,
                option_type=OptionType.CALL,
                side=OrderSide.SELL,
                target_delta=self.short_delta,
                expiry_rule=self.expiry_rule
            ),
            LegDefinition(
                leg_index=2,
                option_type=OptionType.CALL,
                side=OrderSide.BUY,
                target_delta=self.long_delta,
                expiry_rule=self.expiry_rule
            )
        ]

    def get_max_profit_description(self) -> str:
        return "Premium received if underlying stays below short strike at expiration"

    def get_max_loss_description(self) -> str:
        return "Strike width minus premium received if underlying rises above long strike"
