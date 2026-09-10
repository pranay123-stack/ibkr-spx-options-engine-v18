"""
Debit Call Spread (Bull Call Spread) Strategy
Bullish strategy with limited risk and reward
"""

from typing import List

from .base import BaseStrategy, LegDefinition, StrategyType
from ...enums import OptionType, OrderSide, ExpiryRule


class DebitCallSpread(BaseStrategy):
    """
    Debit Call Spread (Bull Call Spread)

    Structure:
    - Buy ITM/ATM Call (lower strike, higher delta)
    - Sell OTM Call (higher strike, lower delta)

    Outlook: Bullish
    Risk: Limited (premium paid)
    Reward: Limited (strike width - premium paid)
    Breakeven: Long strike + premium paid
    """

    def __init__(
        self,
        long_delta: float = 0.50,
        short_delta: float = 0.25,
        expiry_rule: ExpiryRule = ExpiryRule.SAME_DAY
    ):
        """
        Initialize Debit Call Spread.

        Args:
            long_delta: Delta for long call (closer to ATM, higher delta)
            short_delta: Delta for short call (further OTM, lower delta)
            expiry_rule: Expiration rule for legs
        """
        super().__init__(
            long_delta=long_delta,
            short_delta=short_delta,
            expiry_rule=expiry_rule
        )
        self.long_delta = long_delta
        self.short_delta = short_delta
        self.expiry_rule = expiry_rule

    @property
    def strategy_id(self) -> str:
        return "STRAT_DEBIT_CALL"

    @property
    def strategy_type(self) -> StrategyType:
        return StrategyType.DEBIT_SPREAD

    @property
    def description(self) -> str:
        return "Bull Call Spread (Debit Call Spread)"

    @property
    def is_credit(self) -> bool:
        return False

    def get_legs(self) -> List[LegDefinition]:
        """
        Returns legs for debit call spread:
        1. Buy higher delta call (closer to ATM)
        2. Sell lower delta call (further OTM)
        """
        return [
            LegDefinition(
                leg_index=1,
                option_type=OptionType.CALL,
                side=OrderSide.BUY,
                target_delta=self.long_delta,
                expiry_rule=self.expiry_rule
            ),
            LegDefinition(
                leg_index=2,
                option_type=OptionType.CALL,
                side=OrderSide.SELL,
                target_delta=self.short_delta,
                expiry_rule=self.expiry_rule
            )
        ]

    def get_max_profit_description(self) -> str:
        return "Strike width minus premium paid if underlying rises above short strike"

    def get_max_loss_description(self) -> str:
        return "Premium paid if underlying stays below long strike at expiration"
