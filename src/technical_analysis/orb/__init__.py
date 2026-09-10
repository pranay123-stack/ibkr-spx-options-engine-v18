"""
ORB Module - Opening Range Breakout

Author: client Options Trading Engine
"""

from .orb_calculator import OpeningRangeBreakout

# Re-export ORBState from data_classes for backward compatibility
from ...data_classes import ORBState

__all__ = ["OpeningRangeBreakout", "ORBState"]
