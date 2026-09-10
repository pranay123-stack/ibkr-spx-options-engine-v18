"""
Put Ratio Spread Strategy
Directional strategy with asymmetric risk/reward
"""

from typing import List

from .base import BaseStrategy, LegDefinition, StrategyType
from ...enums import OptionType, OrderSide, ExpiryRule


class RatioSpreadPuts(BaseStrategy):
    """
    Put Ratio Spread (Front Spread with Puts)

    Structure:
    - Buy 1 ATM/ITM Put (higher strike)
    - Sell N OTM Puts (lower strike, typically 2)

    Outlook: Mildly bearish to neutral (expecting move to short strike)
    Risk: Substantial below lower breakeven (to zero)
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
        Initialize Put Ratio Spread.

        Args:
            long_delta: Delta for long put (ATM/ITM)
            short_delta: Delta for short puts (OTM)
            ratio: Number of short puts per long put
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
        return "STRAT_RATIO_PUT"

    @property
    def strategy_type(self) -> StrategyType:
        return StrategyType.RATIO_SPREAD

    @property
    def description(self) -> str:
        return f"Put Ratio Spread (1:{self.ratio})"

    @property
    def is_credit(self) -> bool:
        # Typically a credit if premium from short puts > long put
        return True  # Usually structured as credit

    def get_legs(self) -> List[LegDefinition]:
        """
        Returns legs for put ratio spread:
        1. Buy 1 ATM/ITM put
        2. Sell N OTM puts
        """
        return [
            LegDefinition(
                leg_index=1,
                option_type=OptionType.PUT,
                side=OrderSide.BUY,
                target_delta=self.long_delta,
                expiry_rule=self.expiry_rule,
                quantity_factor=1
            ),
            LegDefinition(
                leg_index=2,
                option_type=OptionType.PUT,
                side=OrderSide.SELL,
                target_delta=self.short_delta,
                expiry_rule=self.expiry_rule,
                quantity_factor=self.ratio
            )
        ]

    def get_max_profit_description(self) -> str:
        return "Strike width plus net credit (or minus net debit) at short strike"

    def get_max_loss_description(self) -> str:
        return "Substantial below lower breakeven (to zero); net debit (if any) above long strike"
