"""
Technical Analysis Module - Price-Based Technical Analysis

This module provides technical analysis including:
- Opening Range Breakout (ORB) calculation and detection
- Trend Filter using Moving Averages

Usage:
    from src.technical_analysis import OpeningRangeBreakout, TrendFilter

    orb = OpeningRangeBreakout(config, broker, logger)
    trend = TrendFilter(config, broker, logger)
"""

# Data classes (re-export from centralized data_classes)
from ..data_classes import ORBState, TrendState

# ORB
from .orb import OpeningRangeBreakout

# Trend
from .trend import TrendFilter

# OHLCBar re-export from broker
from ..broker import OHLCBar

__all__ = [
    # Data classes
    "ORBState",
    "TrendState",
    # ORB
    "OpeningRangeBreakout",
    # Trend
    "TrendFilter",
    # Data
    "OHLCBar",
]
