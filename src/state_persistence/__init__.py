"""
State Persistence Module

Provides crash recovery capability for the trading engine.
Saves position state to disk when positions are opened,
loads on restart, reconciles with IBKR, and resumes management.

Usage:
    from src.state_persistence import StateManager, ReconciliationStatus

    state_manager = StateManager(
        state_dir=Path("state"),
        underlying_symbol="SPX",
        logger=logger
    )

    # Save state when position opens
    state_manager.save_state(position, broker)

    # Load state on startup
    saved_state = state_manager.load_state()
    if saved_state:
        result = state_manager.reconcile_with_ibkr(saved_state, broker)
        if result.status == ReconciliationStatus.EXACT_MATCH:
            # Recover position

    # Clear state when position closes
    state_manager.clear_state()

Author: client Options Trading Engine
"""

from .models import (
    PositionState,
    SavedPosition,
    SavedLeg,
    IBKRPositionSnapshot,
    BracketOrderInfo,
    ReconciliationResult,
    ReconciliationStatus,
)

from .state_manager import StateManager

__all__ = [
    "StateManager",
    "PositionState",
    "SavedPosition",
    "SavedLeg",
    "IBKRPositionSnapshot",
    "BracketOrderInfo",
    "ReconciliationResult",
    "ReconciliationStatus",
]
