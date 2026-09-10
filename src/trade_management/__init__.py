"""
Trade Module - Entry, Risk, Monitoring & Exit

This module provides all trade management functionality:
- Entry: Strategy building and order execution
- Risk: Position sizing and risk calculations
- Position Manager: Position lifecycle management
- P&L Manager: P&L calculations
- Spread Tracker: Real-time spread tracking
- Position Monitor: Position monitoring and exit detection
- Exit: Exit condition detection and execution

Usage:
    from src.trade_management import StrategyBuilder, PositionManager, PositionMonitor
"""

from .entry_management import (
    BuiltLeg,
    BuiltStrategy,
    StrategyBuilder,
    SpreadExecutor,
    ExecutionResult,
)

from .risk_management import (
    RiskCalculator,
    RiskMetrics,
)

from .position_manager import (
    PositionManager,
    Position,
)

from .pnl_manager import (
    PnLCalculator,
)

from .spread_tracker import (
    SpreadValueTracker,
)

from .position_monitor import (
    PositionMonitor,
)

from .exit_management import (
    ExitManager,
)

__all__ = [
    # Entry
    "BuiltLeg",
    "BuiltStrategy",
    "StrategyBuilder",
    "SpreadExecutor",
    "ExecutionResult",
    # Risk
    "RiskCalculator",
    "RiskMetrics",
    # Position Manager
    "PositionManager",
    "Position",
    # P&L Manager
    "PnLCalculator",
    # Spread Tracker
    "SpreadValueTracker",
    # Position Monitor
    "PositionMonitor",
    # Exit
    "ExitManager",
]
