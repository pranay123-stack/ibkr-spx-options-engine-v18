"""
Trend State

Author: client Options Trading Engine
"""

from typing import Optional, TYPE_CHECKING
from dataclasses import dataclass

if TYPE_CHECKING:
    from ..enums import TrendRegime


@dataclass
class TrendState:
    """State of trend analysis"""
    ma_short: Optional[float] = None
    ma_long: Optional[float] = None
    trend_regime: Optional["TrendRegime"] = None
    trend_ok: bool = True
    last_price: Optional[float] = None
