"""
Built Leg Data Class

Author: client Options Trading Engine
"""

from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..broker import OptionContract

from ..enums import OptionType, OrderSide


@dataclass
class BuiltLeg:
    """A concrete leg with resolved strike and contract"""
    leg_index: int
    option_type: OptionType
    side: OrderSide
    target_delta: float
    actual_delta: Optional[float]
    strike: float
    expiry: str
    quantity: int
    contract: "OptionContract"

    # Pricing
    entry_price: float = 0.0
    current_price: float = 0.0

    def get_notional_value(self, contract_size: int = 100) -> float:
        """Calculate notional value of this leg"""
        return self.entry_price * self.quantity * contract_size
