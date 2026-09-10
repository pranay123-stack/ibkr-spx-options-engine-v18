"""
Trend Module - Trend Filter using Moving Averages

Author: client Options Trading Engine
"""

from .trend_filter import TrendFilter

# Re-export TrendState from data_classes for backward compatibility
from ...data_classes import TrendState

__all__ = ["TrendFilter", "TrendState"]
