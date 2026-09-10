"""
Broker Module - IBKR API Integration using ib_insync

This module provides the complete IBKR broker integration including:
- Connection & Session Management
- Market Data (Real-time & Historical)
- Options Data (Chains, Quotes, Greeks)
- Order Management (Single & Combo orders)
- Spread Order Execution
- Account & Portfolio Data

Module Structure:
- broker_insync.py: IBKRInsyncWrapper (main broker interface using ib_insync)
- models.py: Data classes (ConnectionConfig, OHLCBar, OptionContract, etc.)
- spread_order_executor.py: SpreadOrderExecutor (multi-leg order execution)
- broker.py: IBKRWrapper (legacy ibapi-based broker - kept for reference)

Usage:
    from src.broker import IBKRBroker, SpreadOrderExecutor

    broker = IBKRBroker(logger, config)
    if broker.connect():
        price = broker.get_latest_price()
        chain = broker.get_option_chain("SPX", "20240115", 5800.0)

        # For spread orders
        spread_order = broker.place_spread_order(legs, quantity=1)

        broker.disconnect()
"""

# Main broker interface - using ib_insync (more reliable for combo orders)
from .broker_insync import IBKRInsyncWrapper as IBKRBroker

# Data classes
from .models import (
    ConnectionConfig,
    MarketDataType,
    OHLCBar,
    OptionContract,
    OptionChain,
    OrderInfo,
    ComboOrder,
    AccountInfo,
    PositionInfo,
)

# Spread order execution
from .spread_order_executor import (
    SpreadOrderExecutor,
    SpreadType,
    ExecutionMode,
    SpreadLeg,
    SpreadOrderInfo,
)

# Backward compatibility - both names point to ib_insync wrapper
IBKRWrapper = IBKRBroker
IBKRInsyncWrapper = IBKRBroker

__all__ = [
    # Main broker (ib_insync based)
    "IBKRBroker",
    "IBKRWrapper",
    "IBKRInsyncWrapper",
    "ConnectionConfig",
    "MarketDataType",
    "OHLCBar",
    "OptionContract",
    "OptionChain",
    "OrderInfo",
    "ComboOrder",
    "AccountInfo",
    "PositionInfo",
    # Spread execution
    "SpreadOrderExecutor",
    "SpreadType",
    "ExecutionMode",
    "SpreadLeg",
    "SpreadOrderInfo",
]
