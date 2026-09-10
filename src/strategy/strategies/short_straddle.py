"""
Short Straddle Strategy
Volatility strategy that profits from low volatility and time decay
"""

from typing import List

from .base import BaseStrategy, LegDefinition, StrategyType
from ...enums import OptionType, OrderSide, ExpiryRule


class ShortStraddle(BaseStrategy):
    """
    Short Straddle

    Structure:
    - Sell ATM Put
    - Sell ATM Call (same strike as put)

    Outlook: Low volatility expected (expecting range-bound)
    Risk: Unlimited on upside, substantial on downside
    Reward: Limited (premium received)
    Breakeven: Two points - strike +/- total premium received
    """

    def __init__(
        self,
        atm_delta: float = 0.50,
        expiry_rule: ExpiryRule = ExpiryRule.SAME_DAY
    ):
        """
        Initialize Short Straddle.

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
        return "STRAT_SHORT_STRADDLE"

    @property
    def strategy_type(self) -> StrategyType:
        return StrategyType.STRADDLE

    @property
    def description(self) -> str:
        return "Short Straddle (Volatility Short)"

    @property
    def is_credit(self) -> bool:
        return True

    def get_legs(self) -> List[LegDefinition]:
        """
        Returns legs for short straddle:
        1. Sell ATM put
        2. Sell ATM call
        """
        return [
            LegDefinition(
                leg_index=1,
                option_type=OptionType.PUT,
                side=OrderSide.SELL,
                target_delta=self.atm_delta,
                expiry_rule=self.expiry_rule
            ),
            LegDefinition(
                leg_index=2,
                option_type=OptionType.CALL,
                side=OrderSide.SELL,
                target_delta=self.atm_delta,
                expiry_rule=self.expiry_rule
            )
        ]

    def get_max_profit_description(self) -> str:
        return "Total premium received if underlying closes at strike at expiration"

    def get_max_loss_description(self) -> str:
        return "Unlimited on upside; strike price minus premium on downside"
