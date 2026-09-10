"""
Iron Fly (Iron Butterfly) Strategy
Neutral strategy with ATM short strikes
"""

from typing import List

from .base import BaseStrategy, LegDefinition, StrategyType
from ...enums import OptionType, OrderSide, ExpiryRule


class IronFly(BaseStrategy):
    """
    Iron Fly (Iron Butterfly)

    Structure:
    - Buy OTM Put (lowest strike, protection)
    - Sell ATM Put (same strike as short call)
    - Sell ATM Call (same strike as short put)
    - Buy OTM Call (highest strike, protection)

    Similar to Iron Condor but with ATM short strikes (more credit, tighter range).

    Outlook: Very neutral (expecting minimal movement)
    Risk: Limited (wing width - premium received)
    Reward: Limited (premium received, higher than iron condor)
    Breakeven: Two breakeven points close to ATM
    """

    def __init__(
        self,
        put_long_delta: float = 0.10,
        atm_delta: float = 0.50,
        call_long_delta: float = 0.10,
        expiry_rule: ExpiryRule = ExpiryRule.SAME_DAY
    ):
        """
        Initialize Iron Fly.

        Args:
            put_long_delta: Delta for long put (OTM protection)
            atm_delta: Delta for ATM short options (typically 0.50)
            call_long_delta: Delta for long call (OTM protection)
            expiry_rule: Expiration rule for all legs
        """
        super().__init__(
            put_long_delta=put_long_delta,
            atm_delta=atm_delta,
            call_long_delta=call_long_delta,
            expiry_rule=expiry_rule
        )
        self.put_long_delta = put_long_delta
        self.atm_delta = atm_delta
        self.call_long_delta = call_long_delta
        self.expiry_rule = expiry_rule

    @property
    def strategy_id(self) -> str:
        return "STRAT_IRON_FLY"

    @property
    def strategy_type(self) -> StrategyType:
        return StrategyType.IRON_FLY

    @property
    def description(self) -> str:
        return "Iron Butterfly (ATM Credit Strategy)"

    @property
    def is_credit(self) -> bool:
        return True

    def get_legs(self) -> List[LegDefinition]:
        """
        Returns legs for iron butterfly:
        1. Buy OTM put (protection)
        2. Sell ATM put
        3. Sell ATM call
        4. Buy OTM call (protection)
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
                target_delta=self.atm_delta,
                expiry_rule=self.expiry_rule
            ),
            LegDefinition(
                leg_index=3,
                option_type=OptionType.CALL,
                side=OrderSide.SELL,
                target_delta=self.atm_delta,
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
        return "Premium received if underlying closes exactly at ATM strike at expiration"

    def get_max_loss_description(self) -> str:
        return "Wing width minus premium received if underlying moves beyond wings"
