"""
Exit Manager - Trade Exit Management

Handles:
- Exit condition evaluation
- Exit order execution
- Position closing

Author: client Options Trading Engine
"""

from typing import Optional, Dict, Any

from ..entry_management import SpreadExecutor
from ..position_manager import PositionManager
from ...broker import IBKRBroker
from ...utils.logging import TradingLogger
from ...utils.config import EngineConfig
from ...enums import ExitReason
from ...data_classes import Position


class ExitManager:
    """
    Manages position exits.

    Handles:
    - Exit order execution
    - P&L calculation
    - Trade logging
    """

    def __init__(
        self,
        config: EngineConfig,
        broker: IBKRBroker,
        position_manager: PositionManager,
        spread_executor: SpreadExecutor,
        logger: TradingLogger
    ):
        self.config = config
        self.broker = broker
        self.position_manager = position_manager
        self.spread_executor = spread_executor
        self.logger = logger

    def exit_position(
        self,
        exit_reason: ExitReason,
        use_market: bool = False
    ) -> Optional[Position]:
        """
        Exit the current position.

        Args:
            exit_reason: Reason for exit
            use_market: Use market orders (default: False)

        Returns:
            Closed Position or None if failed
        """
        position = self.position_manager.current_position

        if position is None:
            self.logger.warning("No position to exit")
            return None

        self.logger.info(f"Exiting position: {position.position_id}")
        self.logger.info(f"Exit reason: {exit_reason.value}")

        # For time exits, still use LIMIT orders - MARKET combo orders don't fill reliably
        if exit_reason == ExitReason.TIME:
            use_market = False  # Changed from True - LIMIT fills better
            self.logger.info("Using LIMIT orders for time exit (with price chasing)")

        # Execute exit
        result = self.spread_executor.execute_exit(
            position=position,
            exit_reason=exit_reason,
            use_market=use_market
        )

        if result.success:
            # Close position in position manager
            underlying_price = self.broker.get_price(self.config.underlying_symbol)

            closed_position = self.position_manager.close_position(
                exit_spread=result.fill_price,
                exit_reason=exit_reason,
                underlying_price=underlying_price
            )

            self._log_exit_summary(closed_position)
            return closed_position

        else:
            self.logger.error(f"Exit failed: {result.error_message}")
            # Still try to close position with estimated spread
            if position.current_spread != 0:
                underlying_price = self.broker.get_price(self.config.underlying_symbol)
                closed_position = self.position_manager.close_position(
                    exit_spread=position.current_spread,
                    exit_reason=exit_reason,
                    underlying_price=underlying_price
                )
                self._log_exit_summary(closed_position)
                return closed_position

            return None

    def _log_exit_summary(self, position: Position):
        """Log simple exit summary."""
        exit_reason = position.exit_reason.value if position.exit_reason else "MANUAL"

        # Calculate duration
        duration_minutes = None
        entry_time_str = None
        exit_time_str = None
        if position.entry_time and position.exit_time:
            duration = position.exit_time - position.entry_time
            duration_minutes = int(duration.total_seconds() / 60)
            entry_time_str = position.entry_time.strftime("%H:%M")
            exit_time_str = position.exit_time.strftime("%H:%M")

        self.logger.log_trade_closed_simple(
            exit_reason=exit_reason,
            entry_spread=position.entry_spread,
            exit_spread=position.exit_spread or 0,
            realized_pnl=position.realized_pnl,
            contracts=position.contracts,
            target_spread=position.target_spread,
            stop_spread=position.stop_spread,
            entry_time=entry_time_str,
            exit_time=exit_time_str,
            duration_minutes=duration_minutes
        )

    def emergency_exit(self) -> Optional[Position]:
        """Emergency exit using LIMIT orders (MARKET combo orders don't fill reliably)."""
        self.logger.warning("EMERGENCY EXIT TRIGGERED")
        return self.exit_position(ExitReason.MANUAL, use_market=False)

    def check_and_exit_if_needed(self, exit_reason: Optional[ExitReason]) -> bool:
        """
        Check if exit is needed and execute if so.

        Args:
            exit_reason: Exit reason from position monitor

        Returns:
            True if position was exited
        """
        if exit_reason is None:
            return False

        result = self.exit_position(exit_reason)
        return result is not None

    def get_exit_summary(self) -> Dict[str, Any]:
        """Get summary of all exits today."""
        return self.position_manager.get_daily_summary()
