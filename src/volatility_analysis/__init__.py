"""
Volatility Module - IV, IVP, VRP, VRT Logic

This module provides all volatility-related calculations:
- Implied Volatility (IV) calculation from option prices
- IV Percentile (IVP) calculation
- Volatility Regime determination
- Volatility Analyzer for real-time IV tracking

Module Structure:
- data_classes.py: IVData, IVState, IVStatistics
- iv_calculator.py: IVCalculator (core IV calculations)
- vol_analyzer.py: VolatilityAnalyzer (high-level interface)

Usage:
    from src.volatility import VolatilityAnalyzer, IVCalculator

    vol_analyzer = VolatilityAnalyzer(config, broker, logger)
    vol_analyzer.update(underlying_price)
    regime = vol_analyzer.get_vol_regime()
"""

# Data classes
from ..data_classes import (
    IVData,
    IVState,
    IVStatistics,
)

# IV Calculator - Core calculations
from ..iv_analysis import IVCalculator

# Volatility Analyzer - High-level interface
from .vol_analyzer import VolatilityAnalyzer

# Alias for convenience
VolatilityState = IVState

__all__ = [
    # Data Classes
    "IVData",
    "IVState",
    "IVStatistics",
    # IV Calculator
    "IVCalculator",
    # Volatility Analyzer
    "VolatilityAnalyzer",
    # Backward compatibility
    "VolatilityState",
]
