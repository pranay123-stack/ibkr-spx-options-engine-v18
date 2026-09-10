"""
Call Ratio Spread Strategy
Directional strategy with asymmetric risk/reward
"""

from typing import List

from .base import BaseStrategy, LegDefinition, StrategyType
from ...enums import OptionType, OrderSide, ExpiryRule


class RatioSpreadCalls(BaseStrategy):
    """
    Call Ratio Spread (Front Spread with Calls)

    Structure:
    - Buy 1 ATM/ITM Call (lower strike)
    - Sell N OTM Calls (higher strike, typically 2)

    Outlook: Mildly bullish to neutral (expecting move to short strike)
    Risk: Unlimited above upper breakeven
    Reward: Limited (difference in strikes + net credit or minus net debit)
    Breakeven: Complex - depends on ratio and premium
    """

    def __init__(
        self,
        long_delta: float = 0.50,
        short_delta: float = 0.25,
        ratio: int = 2,
        expiry_rule: ExpiryRule = ExpiryRule.SAME_DAY
    ):
        """
        Initialize Call Ratio Spread.

        Args:
            long_delta: Delta for long call (ATM/ITM)
            short_delta: Delta for short calls (OTM)
            ratio: Number of short calls per long call
            expiry_rule: Expiration rule for legs
        """
        super().__init__(
            long_delta=long_delta,
            short_delta=short_delta,
            ratio=ratio,
            expiry_rule=expiry_rule
        )
        self.long_delta = long_delta
        self.short_delta = short_delta
        self.ratio = ratio
        self.expiry_rule = expiry_rule

    @property
    def strategy_id(self) -> str:
        return "STRAT_RATIO_CALL"

    @property
    def strategy_type(self) -> StrategyType:
        return StrategyType.RATIO_SPREAD

    @property
    def description(self) -> str:
        return f"Call Ratio Spread (1:{self.ratio})"

    @property
    def is_credit(self) -> bool:
        # Typically a credit if premium from short calls > long call
        return True  # Usually structured as credit

    def get_legs(self) -> List[LegDefinition]:
        """
        Returns legs for call ratio spread:
        1. Buy 1 ATM/ITM call
        2. Sell N OTM calls
        """
        return [
            LegDefinition(
                leg_index=1,
                option_type=OptionType.CALL,
                side=OrderSide.BUY,
                target_delta=self.long_delta,
                expiry_rule=self.expiry_rule,
                quantity_factor=1
            ),
            LegDefinition(
                leg_index=2,
                option_type=OptionType.CALL,
                side=OrderSide.SELL,
                target_delta=self.short_delta,
                expiry_rule=self.expiry_rule,
                quantity_factor=self.ratio
            )
        ]

    def get_max_profit_description(self) -> str:
        return "Strike width plus net credit (or minus net debit) at short strike"

    def get_max_loss_description(self) -> str:
        return "Unlimited above upper breakeven; net debit (if any) below long strike"
