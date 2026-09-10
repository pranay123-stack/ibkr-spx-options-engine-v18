"""
Market Data Adapter Module

Provides market data interface for indicators with staleness detection.

Author: client Options Trading Engine
"""

from .market_data_adapter import MarketDataAdapter, is_us_options_market_open

__all__ = ["MarketDataAdapter", "is_us_options_market_open"]
