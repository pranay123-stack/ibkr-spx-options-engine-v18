"""
Position Data Class

Author: client Options Trading Engine
"""

from datetime import datetime
from typing import Optional, List, Dict, Any, TYPE_CHECKING
from dataclasses import dataclass, field

if TYPE_CHECKING:
    from ..trade_management.entry_management import BuiltStrategy
    from ..enums import ExitReason


@dataclass
class Position:
    """Represents an open trading position"""
    position_id: str
    strategy_id: str
    built_strategy: "BuiltStrategy"
    contracts: int
    entry_time: datetime
    entry_spread: float
    target_spread: float
    stop_spread: float
    is_credit: bool

    # Direction and regime info
    direction_bias: str = ""
    vol_regime: str = ""
    trend_regime: str = ""

    # Current state
    current_spread: float = 0.0
    unrealized_pnl: float = 0.0
    underlying_price_entry: float = 0.0
    underlying_price_current: float = 0.0

    # Exit info
    exit_time: Optional[datetime] = None
    exit_spread: Optional[float] = None
    exit_reason: Optional["ExitReason"] = None
    realized_pnl: float = 0.0

    # Leg tracking
    leg_details: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self):
        self.current_spread = self.entry_spread
        if not self.leg_details:
            self.leg_details = self._build_leg_details()

    def _build_leg_details(self) -> List[Dict[str, Any]]:
        """Build leg details for logging"""
        details = []
        for leg in self.built_strategy.legs:
            details.append({
                "type": leg.option_type.value,
                "strike": leg.strike,
                "side": leg.side.value,
                "delta_target": leg.target_delta,
                "delta_actual": leg.actual_delta,
                "quantity": leg.quantity,
                "entry_price": leg.entry_price
            })
        return details

    def get_log_tag(self) -> str:
        """
        Generate log tag for this position for easy debugging.

        Returns:
            String like "[PUT_CREDIT_SPREAD | CREDIT]" or "[CALL_DEBIT_SPREAD | DEBIT]"

        Example:
            >>> position.get_log_tag()
            "[PUT_CREDIT_SPREAD | CREDIT]"
        """
        spread_type = "CREDIT" if self.is_credit else "DEBIT"
        return f"[{self.strategy_id} | {spread_type}]"
