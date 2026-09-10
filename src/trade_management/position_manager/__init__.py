"""
Position Manager Module - Position Lifecycle Management

Author: client Options Trading Engine
"""

from .position_manager import PositionManager

# Re-export Position from data_classes for backward compatibility
from ...data_classes import Position

__all__ = ["PositionManager", "Position"]
