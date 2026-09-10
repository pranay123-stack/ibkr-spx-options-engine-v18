"""
IV State

Author: client Options Trading Engine
"""

from datetime import datetime
from typing import Optional, TYPE_CHECKING
from dataclasses import dataclass

if TYPE_CHECKING:
    from ..enums import VolRegime


@dataclass
class IVState:
    """Current IV state"""
    atm_iv: Optional[float] = None
    call_iv: Optional[float] = None
    put_iv: Optional[float] = None
    ivp: Optional[float] = None
    vol_regime: Optional["VolRegime"] = None
    last_update: Optional[datetime] = None
    underlying_price: Optional[float] = None
