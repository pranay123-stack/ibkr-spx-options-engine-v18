"""
Built Strategy Data Class

Author: client Options Trading Engine
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, TYPE_CHECKING

if TYPE_CHECKING:
    from .built_leg import BuiltLeg

from ..enums import OrderSide, PriceField
from ..utils.timezone import US_EASTERN


@dataclass
class BuiltStrategy:
    """A complete built strategy with all legs resolved"""
    strategy_id: str
    legs: List["BuiltLeg"] = field(default_factory=list)
    underlying_price: float = 0.0
    entry_spread: float = 0.0
    is_credit: bool = False
    build_time: datetime = None
    error: Optional[str] = None

    def __post_init__(self):
        if self.build_time is None:
            self.build_time = datetime.now(US_EASTERN)

    def calculate_spread_value(self, price_field: PriceField = PriceField.MID) -> float:
        """Calculate spread value: Sum(buy) - Sum(sell)"""
        buy_total = 0.0
        sell_total = 0.0

        for leg in self.legs:
            # Use entry_price if available (includes estimated prices), fall back to contract price
            price = leg.entry_price if leg.entry_price > 0 else leg.contract.get_price(price_field)
            price = price * leg.quantity
            if leg.side == OrderSide.BUY:
                buy_total += price
            else:
                sell_total += price

        return buy_total - sell_total

    def get_max_profit(self) -> Optional[float]:
        """Estimate maximum profit (simplified)"""
        if self.is_credit:
            return abs(self.entry_spread)
        return None

    def get_max_loss(self) -> Optional[float]:
        """Estimate maximum loss (simplified)"""
        if self.is_credit:
            strikes = sorted([leg.strike for leg in self.legs])
            if len(strikes) >= 2:
                width = max(strikes) - min(strikes)
                return width - abs(self.entry_spread)
        else:
            return abs(self.entry_spread)
        return None
