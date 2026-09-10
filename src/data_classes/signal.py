"""
Signal Data Class

Author: client Options Trading Engine
"""

from datetime import datetime
from typing import Optional, TYPE_CHECKING
from dataclasses import dataclass

if TYPE_CHECKING:
    from ..enums import DirectionBias, VolRegime, TrendRegime


@dataclass
class Signal:
    """Trading signal with all regime information"""
    timestamp: datetime
    direction_bias: Optional["DirectionBias"]
    vol_regime: Optional["VolRegime"]
    trend_regime: Optional["TrendRegime"]
    signal_ok: bool
    reason: str
    underlying_price: float

    # Additional context
    or_high: Optional[float] = None
    or_low: Optional[float] = None
    ma_short: Optional[float] = None
    ma_long: Optional[float] = None
    iv_atm: Optional[float] = None
    ivp: Optional[float] = None
