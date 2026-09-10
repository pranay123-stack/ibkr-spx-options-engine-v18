"""
Long Straddle Strategy
Volatility strategy that profits from large price movements in either direction
"""

from typing import List

from .base import BaseStrategy, LegDefinition, StrategyType
from ...enums import OptionType, OrderSide, ExpiryRule


class LongStraddle(BaseStrategy):
    """
    Long Straddle

    Structure:
    - Buy ATM Put
    - Buy ATM Call (same strike as put)

    Outlook: High volatility expected (direction uncertain)
    Risk: Limited (premium paid for both options)
    Reward: Unlimited on upside, substantial on downside
    Breakeven: Two points - strike +/- total premium paid
    """

    def __init__(
        self,
        atm_delta: float = 0.50,
        expiry_rule: ExpiryRule = ExpiryRule.SAME_DAY
    ):
        """
        Initialize Long Straddle.

        Args:
            atm_delta: Delta for ATM options (typically 0.50)
            expiry_rule: Expiration rule for legs
        """
        super().__init__(
            atm_delta=atm_delta,
            expiry_rule=expiry_rule
        )
        self.atm_delta = atm_delta
        self.expiry_rule = expiry_rule

    @property
    def strategy_id(self) -> str:
        return "STRAT_LONG_STRADDLE"

    @property
    def strategy_type(self) -> StrategyType:
        return StrategyType.STRADDLE

    @property
    def description(self) -> str:
        return "Long Straddle (Volatility Long)"

    @property
    def is_credit(self) -> bool:
        return False

    def get_legs(self) -> List[LegDefinition]:
        """
        Returns legs for long straddle:
        1. Buy ATM put
        2. Buy ATM call
        """
        return [
            LegDefinition(
                leg_index=1,
                option_type=OptionType.PUT,
                side=OrderSide.BUY,
                target_delta=self.atm_delta,
                expiry_rule=self.expiry_rule
            ),
            LegDefinition(
                leg_index=2,
                option_type=OptionType.CALL,
                side=OrderSide.BUY,
                target_delta=self.atm_delta,
                expiry_rule=self.expiry_rule
            )
        ]

    def get_max_profit_description(self) -> str:
        return "Unlimited on upside; strike price minus premium on downside"

    def get_max_loss_description(self) -> str:
        return "Total premium paid if underlying closes at strike at expiration"
