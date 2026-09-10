"""
Iron Condor Strategy
Neutral strategy that profits from low volatility and range-bound movement
"""

from typing import List

from .base import BaseStrategy, LegDefinition, StrategyType
from ...enums import OptionType, OrderSide, ExpiryRule


class IronCondor(BaseStrategy):
    """
    Iron Condor

    Structure:
    - Buy OTM Put (lowest strike, protection)
    - Sell OTM Put (higher strike than long put)
    - Sell OTM Call (lower strike than long call)
    - Buy OTM Call (highest strike, protection)

    Essentially a Bull Put Spread + Bear Call Spread combined.

    Outlook: Neutral (expecting range-bound movement)
    Risk: Limited (wider wing width - premium received)
    Reward: Limited (premium received)
    Breakeven: Two breakeven points (put and call sides)
    """

    def __init__(
        self,
        put_long_delta: float = 0.10,
        put_short_delta: float = 0.25,
        call_short_delta: float = 0.25,
        call_long_delta: float = 0.10,
        expiry_rule: ExpiryRule = ExpiryRule.SAME_DAY
    ):
        """
        Initialize Iron Condor.

        Args:
            put_long_delta: Delta for long put (furthest OTM)
            put_short_delta: Delta for short put
            call_short_delta: Delta for short call
            call_long_delta: Delta for long call (furthest OTM)
            expiry_rule: Expiration rule for all legs
        """
        super().__init__(
            put_long_delta=put_long_delta,
            put_short_delta=put_short_delta,
            call_short_delta=call_short_delta,
            call_long_delta=call_long_delta,
            expiry_rule=expiry_rule
        )
        self.put_long_delta = put_long_delta
        self.put_short_delta = put_short_delta
        self.call_short_delta = call_short_delta
        self.call_long_delta = call_long_delta
        self.expiry_rule = expiry_rule

    @property
    def strategy_id(self) -> str:
        return "STRAT_IRON_CONDOR"

    @property
    def strategy_type(self) -> StrategyType:
        return StrategyType.IRON_CONDOR

    @property
    def description(self) -> str:
        return "Iron Condor (Neutral Credit Strategy)"

    @property
    def is_credit(self) -> bool:
        return True

    def get_legs(self) -> List[LegDefinition]:
        """
        Returns legs for iron condor:
        1. Buy far OTM put (protection)
        2. Sell OTM put (short leg)
        3. Sell OTM call (short leg)
        4. Buy far OTM call (protection)
        """
        return [
            LegDefinition(
                leg_index=1,
                option_type=OptionType.PUT,
                side=OrderSide.BUY,
                target_delta=self.put_long_delta,
                expiry_rule=self.expiry_rule
            ),
            LegDefinition(
                leg_index=2,
                option_type=OptionType.PUT,
                side=OrderSide.SELL,
                target_delta=self.put_short_delta,
                expiry_rule=self.expiry_rule
            ),
            LegDefinition(
                leg_index=3,
                option_type=OptionType.CALL,
                side=OrderSide.SELL,
                target_delta=self.call_short_delta,
                expiry_rule=self.expiry_rule
            ),
            LegDefinition(
                leg_index=4,
                option_type=OptionType.CALL,
                side=OrderSide.BUY,
                target_delta=self.call_long_delta,
                expiry_rule=self.expiry_rule
            )
        ]

    def get_max_profit_description(self) -> str:
        return "Premium received if underlying stays between short strikes at expiration"

    def get_max_loss_description(self) -> str:
        return "Wider wing width minus premium received on the breached side"
