"""
Debit Put Spread (Bear Put Spread) Strategy
Bearish strategy with limited risk and reward
"""

from typing import List

from .base import BaseStrategy, LegDefinition, StrategyType
from ...enums import OptionType, OrderSide, ExpiryRule


class DebitPutSpread(BaseStrategy):
    """
    Debit Put Spread (Bear Put Spread)

    Structure:
    - Buy ITM/ATM Put (higher strike, higher delta)
    - Sell OTM Put (lower strike, lower delta)

    Outlook: Bearish
    Risk: Limited (premium paid)
    Reward: Limited (strike width - premium paid)
    Breakeven: Long strike - premium paid
    """

    def __init__(
        self,
        long_delta: float = 0.50,
        short_delta: float = 0.25,
        expiry_rule: ExpiryRule = ExpiryRule.SAME_DAY
    ):
        """
        Initialize Debit Put Spread.

        Args:
            long_delta: Delta for long put (closer to ATM, higher delta)
            short_delta: Delta for short put (further OTM, lower delta)
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
        return "STRAT_DEBIT_PUT"

    @property
    def strategy_type(self) -> StrategyType:
        return StrategyType.DEBIT_SPREAD

    @property
    def description(self) -> str:
        return "Bear Put Spread (Debit Put Spread)"

    @property
    def is_credit(self) -> bool:
        return False

    def get_legs(self) -> List[LegDefinition]:
        """
        Returns legs for debit put spread:
        1. Buy higher delta put (closer to ATM)
        2. Sell lower delta put (further OTM)
        """
        return [
            LegDefinition(
                leg_index=1,
                option_type=OptionType.PUT,
                side=OrderSide.BUY,
                target_delta=self.long_delta,
                expiry_rule=self.expiry_rule
            ),
            LegDefinition(
                leg_index=2,
                option_type=OptionType.PUT,
                side=OrderSide.SELL,
                target_delta=self.short_delta,
                expiry_rule=self.expiry_rule
            )
        ]

    def get_max_profit_description(self) -> str:
        return "Strike width minus premium paid if underlying falls below short strike"

    def get_max_loss_description(self) -> str:
        return "Premium paid if underlying stays above long strike at expiration"
