"""
IV Data Point

Author: client Options Trading Engine
"""

from datetime import datetime
from dataclasses import dataclass


@dataclass
class IVData:
    """IV data point"""
    timestamp: datetime
    iv: float
    underlying_price: float
    strike: float = 0.0
    option_type: str = "ATM"  # ATM, CALL, PUT
