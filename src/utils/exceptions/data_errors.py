"""
Data Errors (2000-2999)

Author: client Options Trading Engine
"""

from typing import Dict
from .base import clientError


class DataError(clientError):
    """Base class for data-related errors"""
    pass


class MarketDataError(DataError):
    """Failed to get market data"""
    def __init__(self, symbol: str, message: str = None, context: Dict = None):
        ctx = {"symbol": symbol, **(context or {})}
        msg = message or f"Failed to get market data for {symbol}"
        super().__init__(2001, msg, ctx)


class NoMarketDataError(DataError):
    """No market data available"""
    def __init__(self, symbol: str, context: Dict = None):
        ctx = {"symbol": symbol, **(context or {})}
        super().__init__(2002, f"No market data available for {symbol}", ctx)


class OptionChainError(DataError):
    """Failed to get option chain"""
    def __init__(self, symbol: str, expiry: str = None, context: Dict = None):
        ctx = {"symbol": symbol, "expiry": expiry, **(context or {})}
        super().__init__(2003, f"Failed to get option chain for {symbol}", ctx)


class NoOptionFoundError(DataError):
    """Could not find option matching criteria"""
    def __init__(self, symbol: str, option_type: str, delta: float, context: Dict = None):
        ctx = {"symbol": symbol, "option_type": option_type, "target_delta": delta, **(context or {})}
        super().__init__(2004, f"No {option_type} option found with delta ~{delta}", ctx)


class StaleDataError(DataError):
    """Data is stale/outdated"""
    def __init__(self, data_type: str, age_seconds: float, context: Dict = None):
        ctx = {"data_type": data_type, "age_seconds": age_seconds, **(context or {})}
        super().__init__(2005, f"{data_type} data is stale ({age_seconds}s old)", ctx)


class IVDataError(DataError):
    """Failed to get IV data"""
    def __init__(self, message: str = "Failed to get IV data", context: Dict = None):
        super().__init__(2010, message, context)
