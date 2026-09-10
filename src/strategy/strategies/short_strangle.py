"""
Short Strangle Strategy
Volatility strategy that profits from low volatility with wider profit range than straddle
"""

from typing import List

from .base import BaseStrategy, LegDefinition, StrategyType
from ...enums import OptionType, OrderSide, ExpiryRule


class ShortStrangle(BaseStrategy):
    """
    Short Strangle

    Structure:
    - Sell OTM Put (lower strike)
    - Sell OTM Call (higher strike)

    Similar to short straddle but with wider profit zone (less premium received).

    Outlook: Low volatility expected (expecting range-bound)
    Risk: Unlimited on upside, substantial on downside
    Reward: Limited (premium received, less than straddle)
    Breakeven: Two points - put strike - premium and call strike + premium
    """

    def __init__(
        self,
        put_delta: float = 0.25,
        call_delta: float = 0.25,
        expiry_rule: ExpiryRule = ExpiryRule.SAME_DAY
    ):
        """
        Initialize Short Strangle.

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
        return "STRAT_SHORT_STRANGLE"

    @property
    def strategy_type(self) -> StrategyType:
        return StrategyType.STRANGLE

    @property
    def description(self) -> str:
        return "Short Strangle (OTM Volatility Short)"

    @property
    def is_credit(self) -> bool:
        return True

    def get_legs(self) -> List[LegDefinition]:
        """
        Returns legs for short strangle:
        1. Sell OTM put
        2. Sell OTM call
        """
        return [
            LegDefinition(
                leg_index=1,
                option_type=OptionType.PUT,
                side=OrderSide.SELL,
                target_delta=self.put_delta,
                expiry_rule=self.expiry_rule
            ),
            LegDefinition(
                leg_index=2,
                option_type=OptionType.CALL,
                side=OrderSide.SELL,
                target_delta=self.call_delta,
                expiry_rule=self.expiry_rule
            )
        ]

    def get_max_profit_description(self) -> str:
        return "Total premium received if underlying closes between strikes at expiration"

    def get_max_loss_description(self) -> str:
        return "Unlimited on upside; put strike minus premium on downside"
