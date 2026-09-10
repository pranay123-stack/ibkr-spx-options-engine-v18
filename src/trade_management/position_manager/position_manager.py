"""
Position Manager - Position Lifecycle Management

Handles:
- Position creation and management
- Exit condition checking
- Position closure

Uses PnLCalculator for P&L calculations.

Author: client Options Trading Engine
"""

from datetime import datetime
from typing import Optional, Dict, Any, List
from copy import deepcopy

from ...utils.logging import TradingLogger
from ...utils.config import EngineConfig
from ...enums import ExitReason
from ...utils.exceptions import NoPositionError
from ...data_classes import Position
from ...utils.timezone import US_EASTERN, TimezoneManager

from ..entry_management import BuiltStrategy
from ..pnl_manager import PnLCalculator


class PositionManager:
    """
    Manages trading positions throughout their lifecycle.

    Responsibilities:
    - Create new positions from built strategies
    - Track position state
    - Handle position closure

    Uses PnLCalculator for all P&L calculations.
    """

    def __init__(
        self,
        config: EngineConfig,
        logger: TradingLogger
    ):
        self.config = config
        self.logger = logger

        # P&L Calculator
        self.pnl_calculator = PnLCalculator(config)

        # Current open position (only one at a time)
        self.current_position: Optional[Position] = None

        # Position history
        self.closed_positions: List[Position] = []

        # Position counter for ID generation
        self._position_counter = 0

    def has_open_position(self) -> bool:
        """Check if there's an open position"""
        return self.current_position is not None

    def create_position(
        self,
        built_strategy: BuiltStrategy,
        contracts: int,
        direction_bias: str = "",
        vol_regime: str = "",
        trend_regime: str = "",
        underlying_price: float = 0.0,
        fill_spread: float = None
    ) -> Position:
        """Create a new position from a built strategy.

        Args:
            fill_spread: Actual fill price from broker. If None, uses built_strategy.entry_spread.
        """
        if self.current_position is not None:
            self.logger.warning("Creating new position while one is already open")

        self._position_counter += 1
        position_id = f"POS_{datetime.now(US_EASTERN).strftime('%Y%m%d')}_{self._position_counter:04d}"

        # Use actual fill spread if provided, otherwise use estimate
        entry_spread = fill_spread if fill_spread is not None else built_strategy.entry_spread
        is_credit = built_strategy.is_credit

        # Use PnLCalculator for target/stop calculations
        target_spread = self.pnl_calculator.calculate_target_spread(entry_spread, is_credit)
        stop_spread = self.pnl_calculator.calculate_stop_spread(entry_spread, is_credit)

        position = Position(
            position_id=position_id,
            strategy_id=built_strategy.strategy_id,
            built_strategy=deepcopy(built_strategy),
            contracts=contracts,
            entry_time=datetime.now(US_EASTERN),
            entry_spread=entry_spread,
            target_spread=target_spread,
            stop_spread=stop_spread,
            is_credit=is_credit,
            direction_bias=direction_bias,
            vol_regime=vol_regime,
            trend_regime=trend_regime,
            underlying_price_entry=underlying_price,
            underlying_price_current=underlying_price
        )

        self.current_position = position

        self.logger.info(f"{position.get_log_tag()} Position created: {position_id}")
        self.logger.info(f"{position.get_log_tag()}   Entry spread: ${entry_spread:.2f}")
        self.logger.info(f"{position.get_log_tag()}   Contracts: {contracts}")
        self.logger.info(f"{position.get_log_tag()}   Target: ${target_spread:.2f}")
        self.logger.info(f"{position.get_log_tag()}   Stop: ${stop_spread:.2f}")

        return position

    def update_position(
        self,
        current_spread: float,
        underlying_price: float
    ) -> Optional[ExitReason]:
        """Update current position with latest prices."""
        if self.current_position is None:
            return None

        pos = self.current_position
        pos.current_spread = current_spread
        pos.underlying_price_current = underlying_price

        # Use PnLCalculator for unrealized P&L
        pos.unrealized_pnl = self.pnl_calculator.calculate_unrealized_pnl(pos, current_spread)

        return self._check_exit_conditions(pos)

    def _check_exit_conditions(self, position: Position) -> Optional[ExitReason]:
        """Check if any exit condition is met."""
        current = position.current_spread
        target = position.target_spread
        stop = position.stop_spread

        if position.is_credit:
            if self.config.exit_on_target and current >= target:
                return ExitReason.TARGET
            if self.config.exit_on_stop and current <= stop:
                return ExitReason.STOP
        else:
            if self.config.exit_on_target and current >= target:
                return ExitReason.TARGET
            if self.config.exit_on_stop and current <= stop:
                return ExitReason.STOP

        if self.config.exit_on_time:
            current_time = datetime.now(US_EASTERN).time()
            # Convert IST config times to ET for comparison
            session_start = TimezoneManager.convert_ist_to_eastern(self.config.session_start_time)
            session_end = TimezoneManager.convert_ist_to_eastern(self.config.session_end_time)
            cutoff = TimezoneManager.convert_ist_to_eastern(self.config.cutoff_time)

            if session_start > session_end:
                # Overnight session (in ET terms)
                # Only trigger if we're in the post-midnight portion of the session
                if current_time <= session_end and current_time >= cutoff:
                    return ExitReason.TIME
            else:
                # Regular session - simple comparison
                if current_time >= cutoff:
                    return ExitReason.TIME

        return None

    def close_position(
        self,
        exit_spread: float,
        exit_reason: ExitReason,
        underlying_price: float
    ) -> Position:
        """Close the current position."""
        if self.current_position is None:
            raise NoPositionError(
                message="No position to close",
                context={"action": "close_position"}
            )

        pos = self.current_position

        # Use PnLCalculator for slippage adjustment
        exit_spread = self.pnl_calculator.apply_exit_slippage(
            exit_spread,
            len(pos.built_strategy.legs),
            pos.is_credit
        )

        pos.exit_time = datetime.now(US_EASTERN)
        pos.exit_spread = exit_spread
        pos.exit_reason = exit_reason
        pos.underlying_price_current = underlying_price

        # Use PnLCalculator for realized P&L
        pos.realized_pnl = self.pnl_calculator.calculate_realized_pnl(pos, exit_spread)

        self.closed_positions.append(pos)
        self.current_position = None

        self.logger.info(f"{pos.get_log_tag()} Position closed: {pos.position_id}")
        self.logger.info(f"{pos.get_log_tag()}   Exit reason: {exit_reason.value}")
        self.logger.info(f"{pos.get_log_tag()}   Exit spread: ${exit_spread:.2f}")
        self.logger.info(f"{pos.get_log_tag()}   Realized P&L: ${pos.realized_pnl:.2f}")

        return pos

    def get_position_for_logging(self) -> Optional[Dict[str, Any]]:
        """Get current position data formatted for trade logging."""
        if self.current_position is None:
            return None

        pos = self.current_position
        return {
            "strategy_id": pos.strategy_id,
            "direction_bias": pos.direction_bias,
            "vol_regime": pos.vol_regime,
            "trend_regime": pos.trend_regime,
            "legs": pos.leg_details,
            "entry_spread": pos.entry_spread,
            "exit_spread": pos.exit_spread,
            "target_spread": pos.target_spread,
            "stop_spread": pos.stop_spread,
            "exit_reason": pos.exit_reason.value if pos.exit_reason else None,
            "contracts": pos.contracts,
            "pnl_per_contract": self.pnl_calculator.get_pnl_per_contract(pos),
            "total_pnl": pos.realized_pnl,
            "entry_time": pos.entry_time.strftime("%Y-%m-%d %H:%M:%S"),
            "exit_time": pos.exit_time.strftime("%Y-%m-%d %H:%M:%S") if pos.exit_time else None,
            "underlying_price_entry": pos.underlying_price_entry,
            "underlying_price_exit": pos.underlying_price_current
        }

    def get_daily_summary(self) -> Dict[str, Any]:
        """Get summary of today's trading."""
        return self.pnl_calculator.get_daily_summary(self.closed_positions)

    def reset(self):
        """Reset position manager for new trading day."""
        self.current_position = None
        self.closed_positions.clear()
        self._position_counter = 0
        self.logger.debug("Position manager reset")
