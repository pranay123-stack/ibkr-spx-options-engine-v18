"""
Signal Module - Signal Generator

Author: client Options Trading Engine
"""

from .signal_generator import SignalGenerator

# Re-export Signal from data_classes for backward compatibility
from ..data_classes import Signal

__all__ = ["SignalGenerator", "Signal"]
