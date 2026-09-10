"""
ORB State

Author: client Options Trading Engine
"""

from datetime import datetime
from typing import Optional, List, TYPE_CHECKING
from dataclasses import dataclass

if TYPE_CHECKING:
    from ..broker import OHLCBar
    from ..enums import DirectionBias


@dataclass
class ORBState:
    """State of the Opening Range Breakout analysis"""
    or_high: Optional[float] = None
    or_low: Optional[float] = None
    orb_complete: bool = False
    breakout_detected: bool = False
    direction_bias: Optional["DirectionBias"] = None
    breach_time: Optional[datetime] = None
    breach_price: Optional[float] = None
    orb_bars: List["OHLCBar"] = None

    def __post_init__(self):
        if self.orb_bars is None:
            self.orb_bars = []
