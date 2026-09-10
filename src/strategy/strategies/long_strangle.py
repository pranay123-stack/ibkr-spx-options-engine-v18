"""
Long Strangle Strategy
Volatility strategy that profits from large price movements (cheaper than straddle)
"""

from typing import List

from .base import BaseStrategy, LegDefinition, StrategyType
from ...enums import OptionType, OrderSide, ExpiryRule


class LongStrangle(BaseStrategy):
    """
    Long Strangle

    Structure:
    - Buy OTM Put (lower strike)
    - Buy OTM Call (higher strike)

    Similar to straddle but uses OTM options (cheaper, needs bigger move to profit).

    Outlook: High volatility expected (direction uncertain)
    Risk: Limited (premium paid for both options, less than straddle)
    Reward: Unlimited on upside, substantial on downside
    Breakeven: Two points further apart than straddle
    """

    def __init__(
        self,
        put_delta: float = 0.25,
        call_delta: float = 0.25,
        expiry_rule: ExpiryRule = ExpiryRule.SAME_DAY
    ):
        """
        Initialize Long Strangle.

        Args:
            put_delta: Delta for OTM put
            call_delta: Delta for OTM call
            expiry_rule: Expiration rule for legs
        """
        super().__init__(
            put_delta=put_delta,
            call_delta=call_delta,
            expiry_rule=expiry_rule
        )
        self.put_delta = put_delta
        self.call_delta = call_delta
        self.expiry_rule = expiry_rule

    @property
    def strategy_id(self) -> str:
        return "STRAT_LONG_STRANGLE"

    @property
    def strategy_type(self) -> StrategyType:
        return StrategyType.STRANGLE

    @property
    def description(self) -> str:
        return "Long Strangle (OTM Volatility Long)"

    @property
    def is_credit(self) -> bool:
        return False

    def get_legs(self) -> List[LegDefinition]:
        """
        Returns legs for long strangle:
        1. Buy OTM put
        2. Buy OTM call
        """
        return [
            LegDefinition(
                leg_index=1,
                option_type=OptionType.PUT,
                side=OrderSide.BUY,
                target_delta=self.put_delta,
                expiry_rule=self.expiry_rule
            ),
            LegDefinition(
                leg_index=2,
                option_type=OptionType.CALL,
                side=OrderSide.BUY,
                target_delta=self.call_delta,
                expiry_rule=self.expiry_rule
            )
        ]

    def get_max_profit_description(self) -> str:
        return "Unlimited on upside; put strike minus premium on downside"

    def get_max_loss_description(self) -> str:
        return "Total premium paid if underlying closes between strikes at expiration"
