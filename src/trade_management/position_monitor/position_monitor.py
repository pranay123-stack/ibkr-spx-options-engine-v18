"""
Position Monitor

Monitors open positions and triggers exits when conditions are met.

Author: client Options Trading Engine
"""

from typing import Optional

from ...broker import IBKRBroker
from ...utils.logging import TradingLogger
from ...utils.config import EngineConfig
from ...enums import ExitReason

from ..spread_tracker import SpreadValueTracker


class PositionMonitor:
    """
    Monitors open positions and triggers exits when conditions are met.

    Runs on a configurable interval and checks:
    - Profit target hit
    - Stop loss hit
    - Time cutoff reached
    """

    def __init__(
        self,
        config: EngineConfig,
        position_manager,  # PositionManager - imported later to avoid circular
        broker: IBKRBroker,
        logger: TradingLogger
    ):
        self.config = config
        self.position_manager = position_manager
        self.broker = broker
        self.logger = logger
        self.spread_tracker = SpreadValueTracker(broker, config, logger)

        # Exit callback
        self._exit_callback = None
        self._running = False

    def set_exit_callback(self, callback):
        """Set callback function for exit trigger."""
        self._exit_callback = callback

    def start(self):
        """Start position monitoring."""
        self._running = True
        self.logger.debug("Position monitor started")

    def stop(self):
        """Stop position monitoring."""
        self._running = False
        self.logger.debug("Position monitor stopped")

    def check_position(self) -> Optional[ExitReason]:
        """Check current position and return exit reason if applicable."""
        position = self.position_manager.current_position

        if position is None:
            return None

        # Get current spread value
        current_spread = self.spread_tracker.get_current_spread(position.built_strategy)
        underlying_price = self.broker.get_price(self.config.underlying_symbol)

        # Validate market data is available
        if underlying_price is None:
            self.logger.warning("Underlying price unavailable - skipping exit check this cycle")
            return None

        # Update position and check exit conditions
        exit_reason = self.position_manager.update_position(
            current_spread, underlying_price
        )

        if exit_reason:
            # Calculate P&L context for exit trigger
            from ...enums import ExitReason
            entry_spread = abs(position.entry_spread)
            current_spread_abs = abs(current_spread)
            profit = position.unrealized_pnl
            # Check actual divisor (premium at risk), not just entry_spread
            premium_at_risk = entry_spread * position.contracts * 100
            profit_pct = (profit / premium_at_risk) * 100 if premium_at_risk > 0 else 0

            self.logger.info("=" * 60)
            self.logger.info(f"{position.get_log_tag()} [EXIT CONDITION MET] {exit_reason.value}")
            self.logger.info("=" * 60)
            self.logger.info(f"{position.get_log_tag()}   Entry spread: ${entry_spread:.2f}")
            self.logger.info(f"{position.get_log_tag()}   Current spread: ${current_spread_abs:.2f}")
            self.logger.info(f"{position.get_log_tag()}   Target spread: ${abs(position.target_spread):.2f}")
            self.logger.info(f"{position.get_log_tag()}   Stop spread: ${abs(position.stop_spread):.2f}")
            self.logger.info("")
            self.logger.info(f"{position.get_log_tag()}   Unrealized P&L: ${profit:+.2f} ({profit_pct:+.1f}%)")
            self.logger.info(f"{position.get_log_tag()}   Contracts: {position.contracts}")

            if exit_reason == ExitReason.TARGET:
                self.logger.info(f"{position.get_log_tag()}   🎯 PROFIT TARGET HIT - will attempt to close for gain")
            elif exit_reason == ExitReason.STOP:
                self.logger.warning(f"{position.get_log_tag()}   🛑 STOP LOSS HIT - will attempt to close to limit loss")
            elif exit_reason == ExitReason.TIME:
                self.logger.warning(f"{position.get_log_tag()}   ⏰ TIME CUTOFF - will attempt to close before market close")

            self.logger.info(f"{position.get_log_tag()}   Attempting to execute exit...")
            self.logger.info("=" * 60)

        return exit_reason

    def log_position_status(self):
        """Log current position status."""
        position = self.position_manager.current_position

        if position is None:
            return

        # Calculate P&L percentage using unrealized_pnl (already correctly calculated)
        # This avoids sign confusion with credit/debit spreads
        entry_spread = position.entry_spread
        current_spread = position.current_spread
        # P&L % = unrealized_pnl / (premium at risk)
        # Premium at risk = abs(entry_spread) * contracts * 100
        premium_at_risk = abs(entry_spread) * position.contracts * 100
        pnl_pct = (position.unrealized_pnl / premium_at_risk) * 100 if premium_at_risk > 0 else 0

        self.logger.debug(
            f"{position.get_log_tag()} [P&L] Spread: ${current_spread:.2f} | "
            f"P&L: ${position.unrealized_pnl:+.2f} ({pnl_pct:+.1f}%) | "
            f"Target: ${position.target_spread:.2f} | Stop: ${position.stop_spread:.2f}"
        )
