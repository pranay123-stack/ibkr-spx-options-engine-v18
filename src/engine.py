"""
Trading Engine - Main Orchestrator
Coordinates all components for the options trading strategy

Uses IBKRBroker for all IBKR operations

Version 2.1.0 - Major fixes:
- Issue 1: Signal-only mode support (decoupled signal generation from execution)
- Issue 2: Hard separation between signal generation and execution
- Issue 3: Manual position detection to prevent interference
- Issue 4: State machine validation to prevent invalid transitions
- Issue 5: Unfilled order cleanup before new signals
- Issue 6: Stale order position inflation fix
- Issue 7: ORB calculation with proper data validation
- Issue 8: OHLC timestamp handling and validation
- Issue 9: Market data staleness detection
- Issue 10: Graceful IBKR disconnect handling
- Issue 11: Automatic reconnect with exponential backoff
- Issue 12: Proper engine shutdown with immediate execution stop
- Issue 13: Circuit breaker for error accumulation
- Issue 14: Logging encoding fixes
"""

import time
import threading
from datetime import datetime, timedelta, time as dt_time
from typing import Optional, Dict, Any, Set, List

from .utils.timezone import TimezoneManager, US_EASTERN, IST

# Use centralized timezone utilities
is_time_in_session = TimezoneManager.is_time_in_session
is_session_ended = TimezoneManager.is_session_ended

# Market data adapter
from .market_data_adapter import MarketDataAdapter, is_us_options_market_open

# Updated imports for new structure
from .broker import IBKRBroker, OHLCBar
from .technical_analysis import OpeningRangeBreakout, TrendFilter
from .signal import SignalGenerator, Signal
from .volatility_analysis import VolatilityAnalyzer
from .strategy.selector import StrategySelector
from .trade_management import StrategyBuilder, BuiltStrategy, PositionManager, Position, PositionMonitor
from .trade_management.risk_management import RiskCalculator
from .utils.config import ConfigLoader, EngineConfig
from .utils.logging import TradingLogger
from .enums import EngineState, ExitReason, OrderSide
from .utils.exceptions import (
    clientError, ConnectionError, ConnectionTimeoutError,
    MarketDataError, StrategyBuildError, SpreadExecutionError,
    CriticalError, EmergencyShutdownError
)
from .state_persistence import StateManager, ReconciliationStatus


class TradingEngine:
    """
    Main trading engine that orchestrates all components.

    Uses IBKRWrapper for unified IBKR operations:
    - Connection management
    - Market data
    - Options data
    - Order execution
    - Account info

    Flow:
    1. Initialize and load configuration
    2. Connect to IBKR via IBKRWrapper
    3. Wait for session start
    4. Collect ORB data
    5. Monitor for breakout signals
    6. Execute strategy when conditions met
    7. Monitor position for exit conditions
    8. Close at end of day
    """

    def __init__(self, config_dir: str = "config"):
        # State
        self.state = EngineState.INITIALIZING
        self._running = False
        self._shutdown_event = threading.Event()

        # Configuration
        self.config_dir = config_dir
        self.config_loader: Optional[ConfigLoader] = None
        self.config: Optional[EngineConfig] = None

        # Logger
        self.logger: Optional[TradingLogger] = None

        # IBKR Broker - Single unified interface
        self.broker: Optional[IBKRBroker] = None

        # Indicators
        self.orb: Optional[OpeningRangeBreakout] = None
        self.trend: Optional[TrendFilter] = None
        self.volatility: Optional[VolatilityAnalyzer] = None
        self.signal_generator: Optional[SignalGenerator] = None

        # Strategy components
        self.strategy_selector: Optional[StrategySelector] = None
        self.strategy_builder: Optional[StrategyBuilder] = None

        # Risk management
        self.position_manager: Optional[PositionManager] = None
        self.risk_calculator: Optional[RiskCalculator] = None
        self.position_monitor: Optional[PositionMonitor] = None

        # Trading state
        self.current_signal: Optional[Signal] = None
        self.current_strategy: Optional[BuiltStrategy] = None

        # Market data adapter
        self._market_data_adapter: Optional[MarketDataAdapter] = None

        # State persistence for crash recovery
        self.state_manager: Optional[StateManager] = None

        # Cycle tracking
        self._cycle_count = 0
        self._orb_failure_logged = False

        # Order tracking - prevent duplicate orders
        self._pending_order_id: Optional[str] = None
        self._last_order_time: Optional[datetime] = None
        self._order_retry_count = 0
        self._max_order_retries = 3
        self._order_cooldown_seconds = 30  # Minimum time between order attempts

        # Bracket order tracking (when IBKR handles SL + Target)
        self._bracket_order_info: Optional[Dict[str, Any]] = None

        # Stale price detection during disconnections
        self._last_spread_value: Optional[float] = None
        self._last_spread_change_time: Optional[datetime] = None
        self._stale_price_threshold_seconds = 30  # Mark as stale after 30s of same value

        # Exit tracking - prevent duplicate exit attempts
        self._exit_in_progress = False
        self._exit_attempt_count = 0
        self._max_exit_attempts = 3  # Max attempts before giving up

        # Circuit breaker for error accumulation (Issue 13)
        self._consecutive_errors = 0
        self._last_error_time: Optional[datetime] = None
        self._circuit_breaker_tripped = False
        self._circuit_breaker_trip_time: Optional[datetime] = None

        # Connection management (Issue 10, 11)
        self._reconnect_attempts = 0
        self._last_connection_check: Optional[datetime] = None
        self._connection_healthy = True

        # Lock file for multiple engine protection
        self._lock_file_path: Optional[Path] = None
        self._lock_file_handle = None

        # State transition validation (Issue 4)
        self._valid_state_transitions: Dict[EngineState, Set[EngineState]] = {
            EngineState.INITIALIZING: {EngineState.WAITING_FOR_SESSION, EngineState.POSITION_OPEN, EngineState.ERROR, EngineState.SHUTDOWN},
            EngineState.WAITING_FOR_SESSION: {EngineState.COLLECTING_ORB, EngineState.POSITION_OPEN, EngineState.ERROR, EngineState.SHUTDOWN},
            EngineState.COLLECTING_ORB: {EngineState.WAITING_FOR_BREAKOUT, EngineState.MONITORING_SIGNALS, EngineState.IDLE_NO_TRADE, EngineState.ERROR, EngineState.SHUTDOWN},
            EngineState.WAITING_FOR_BREAKOUT: {EngineState.POSITION_OPEN, EngineState.MONITORING_SIGNALS, EngineState.IDLE_NO_TRADE, EngineState.SESSION_ENDED, EngineState.ERROR, EngineState.SHUTDOWN},
            EngineState.MONITORING_SIGNALS: {EngineState.POSITION_OPEN, EngineState.SESSION_ENDED, EngineState.ERROR, EngineState.SHUTDOWN},
            EngineState.IDLE_NO_TRADE: {EngineState.SESSION_ENDED, EngineState.ERROR, EngineState.SHUTDOWN},  # No trade today, just wait for session end
            EngineState.POSITION_OPEN: {EngineState.WAITING_FOR_BREAKOUT, EngineState.MONITORING_SIGNALS, EngineState.SESSION_ENDED, EngineState.ERROR, EngineState.SHUTDOWN},
            EngineState.SESSION_ENDED: {EngineState.SHUTDOWN, EngineState.ERROR},
            EngineState.ERROR: {EngineState.SHUTDOWN},
            EngineState.SHUTDOWN: set(),  # Terminal state
        }

        # Manual position tracking (Issue 3)
        self._manual_positions_detected = False
        self._manual_position_symbols: Set[str] = set()
        self._manual_positions_logged = False  # Track if we've logged the manual positions message

        # Track if startup reconciliation has been done (for reducing verbosity)
        self._startup_reconciliation_done = False

        # Infinite loop detection - shutdown after N consecutive blocked iterations
        self._blocked_iterations = 0
        self._max_blocked_iterations = 50  # Shutdown after 50 blocked iterations without progress
        self._last_successful_action_time: Optional[datetime] = None

        # ORB waiting status logging - only log once at start
        self._orb_waiting_status_logged = False

        # Time-to-close warnings tracking
        self._warning_10min_shown = False
        self._warning_5min_shown = False
        self._warning_2min_shown = False

        # Daily P&L tracking for risk limits
        self._daily_realized_pnl: float = 0.0
        self._daily_pnl_limit_hit = False
        self._daily_pnl_limit_reason: Optional[str] = None

        # Config change detection
        self._config_mtime: Optional[float] = None
        self._config_change_warned = False

        # Wait for new bar after trade close - prevent immediate re-entry on same bar
        self._last_trade_close_bar: Optional[datetime] = None  # Bar timestamp when last trade closed

    def _get_current_et_time(self) -> datetime:
        """
        Get current time in US Eastern timezone.
        IBKR bar timestamps are in ET, so we need to compare against ET time.

        Returns timezone-aware datetime in US Eastern.
        """
        return datetime.now(US_EASTERN)

    def _get_current_bar_timestamp(self) -> Optional[datetime]:
        """
        Get the timestamp of the current 5-minute bar.
        Rounds down current time to nearest 5-minute interval.
        """
        now = datetime.now(US_EASTERN)
        # Round down to nearest 5 minutes
        minutes = (now.minute // 5) * 5
        bar_time = now.replace(minute=minutes, second=0, microsecond=0)
        return bar_time

    def _check_time_to_close_warnings(self):
        """
        Check time remaining until cutoff/market close and show warnings.

        If exit_on_time is enabled: warns before cutoff_time (forced exit time)
        If exit_on_time is disabled: warns before session_end_time (market close)

        All times in IST from config.

        Shows warnings at:
        - 10 minutes before cutoff/close
        - 5 minutes before cutoff/close
        - 2 minutes before cutoff/close
        """
        if not self.position_manager.has_open_position():
            return

        current_time = datetime.now(US_EASTERN)

        # Determine which time to warn against (convert IST config times to ET)
        if self.config.exit_on_time and self.config.cutoff_time:
            # Warn before cutoff (forced exit time)
            target_time = TimezoneManager.convert_ist_to_eastern(self.config.cutoff_time)
            time_label = "cutoff (forced exit)"
        else:
            # Warn before market close
            target_time = TimezoneManager.convert_ist_to_eastern(self.config.session_end_time)
            time_label = "market close"

        # Calculate target datetime
        warning_target = current_time.replace(
            hour=target_time.hour,
            minute=target_time.minute,
            second=0,
            microsecond=0
        )

        # Calculate minutes until target time
        time_remaining = warning_target - current_time
        minutes_remaining = time_remaining.total_seconds() / 60

        # If already past target time, don't show warnings
        if minutes_remaining < 0:
            return

        position = self.position_manager.current_position

        # 10-minute warning
        if 9 <= minutes_remaining <= 10 and not self._warning_10min_shown:
            self.logger.warning("=" * 60)
            self.logger.warning(f"{position.get_log_tag()} ⏰ WARNING: 10 minutes until {time_label} ({warning_target.strftime('%H:%M')} IST)")
            self.logger.warning("=" * 60)
            self.logger.warning(f"{position.get_log_tag()}   Position: {position.position_id}")
            self.logger.warning(f"{position.get_log_tag()}   Unrealized P&L: ${position.unrealized_pnl:+.2f}")
            self.logger.warning(f"{position.get_log_tag()}   Consider closing position if exit targets not yet hit")
            self.logger.warning("=" * 60)
            self._warning_10min_shown = True

        # 5-minute warning (more urgent)
        elif 4 <= minutes_remaining <= 5 and not self._warning_5min_shown:
            self.logger.error("=" * 60)
            self.logger.error(f"{position.get_log_tag()} ⚠️ URGENT: 5 minutes until {time_label} ({warning_target.strftime('%H:%M')} IST)")
            self.logger.error("=" * 60)
            self.logger.error(f"{position.get_log_tag()}   Position: {position.position_id}")
            self.logger.error(f"{position.get_log_tag()}   Unrealized P&L: ${position.unrealized_pnl:+.2f}")

            if not self._bracket_order_info:
                self.logger.error(f"{position.get_log_tag()}   ⚠️ NO BRACKET ORDERS - Consider manual close in TWS")
            else:
                self.logger.error(f"{position.get_log_tag()}   Bracket orders active - may not execute in time!")

            self.logger.error(f"{position.get_log_tag()}   Liquidity may deteriorate near close")
            self.logger.error("=" * 60)
            self._warning_5min_shown = True

        # 2-minute warning (critical)
        elif 1 <= minutes_remaining <= 2 and not self._warning_2min_shown:
            self.logger.error("!" * 60)
            self.logger.error(f"{position.get_log_tag()} 🚨 CRITICAL: 2 minutes until {time_label} ({warning_target.strftime('%H:%M')} IST)")
            self.logger.error("!" * 60)
            self.logger.error(f"{position.get_log_tag()}   Position: {position.position_id}")
            self.logger.error(f"{position.get_log_tag()}   Unrealized P&L: ${position.unrealized_pnl:+.2f}")
            self.logger.error(f"{position.get_log_tag()}   Entry: ${abs(position.entry_spread):.2f}")
            self.logger.error(f"{position.get_log_tag()}   Current: ${abs(position.current_spread):.2f}")

            if not self._bracket_order_info:
                self.logger.error(f"{position.get_log_tag()}   ⚠️ IMMEDIATE ACTION: Close manually in TWS NOW!")
            else:
                self.logger.error(f"{position.get_log_tag()}   ⚠️ Bracket orders may not execute - consider manual close!")

            self.logger.error(f"{position.get_log_tag()}   Overnight exposure risk if not closed!")
            self.logger.error("!" * 60)
            self._warning_2min_shown = True

    def _validate_state_transition(self, new_state: EngineState) -> bool:
        """
        Validate if a state transition is allowed. (Issue 4)

        Args:
            new_state: The target state

        Returns:
            True if transition is valid, False otherwise
        """
        if self.state not in self._valid_state_transitions:
            return False

        valid_targets = self._valid_state_transitions[self.state]
        return new_state in valid_targets

    def _safe_state_transition(self, new_state: EngineState, reason: str = "") -> bool:
        """
        Safely transition to a new state with validation. (Issue 4)

        Args:
            new_state: The target state
            reason: Reason for the transition

        Returns:
            True if transition succeeded, False if blocked
        """
        # Block redundant transitions - same state should not trigger again
        if new_state == self.state:
            if self.logger:
                self.logger.warning(f"Redundant state transition blocked: {self.state.name} -> {new_state.name} (already in this state)")
            return False  # Block redundant transitions to prevent loops

        if not self._validate_state_transition(new_state):
            if self.logger:
                self.logger.error(f"Invalid state transition blocked: {self.state.name} -> {new_state.name} (not in allowed transitions)")
            return False

        old_state = self.state.name
        self.state = new_state

        # Reset ORB waiting status flag when entering WAITING_FOR_BREAKOUT
        if new_state == EngineState.WAITING_FOR_BREAKOUT:
            self._orb_waiting_status_logged = False

        if self.logger:
            self.logger.log_state_transition(old_state, new_state.name, reason)

        return True

    def _check_circuit_breaker(self) -> bool:
        """
        Check if circuit breaker should trip. (Issue 13)

        Returns:
            True if execution should continue, False if circuit breaker is tripped
        """
        if not hasattr(self.config, 'circuit_breaker_enabled') or not self.config.circuit_breaker_enabled:
            return True

        max_errors = getattr(self.config, 'max_consecutive_errors', 10)
        cooldown = getattr(self.config, 'error_cooldown_seconds', 300.0)

        # Check if we're in cooldown from a previous trip
        if self._circuit_breaker_tripped and self._circuit_breaker_trip_time:
            elapsed = (datetime.now(US_EASTERN) - self._circuit_breaker_trip_time).total_seconds()
            if elapsed < cooldown:
                return False
            else:
                # Cooldown expired, reset circuit breaker
                self._circuit_breaker_tripped = False
                self._consecutive_errors = 0
                if self.logger:
                    self.logger.info("Circuit breaker reset after cooldown period")

        # Check if we should trip
        if self._consecutive_errors >= max_errors:
            self._circuit_breaker_tripped = True
            self._circuit_breaker_trip_time = datetime.now(US_EASTERN)
            if self.logger:
                self.logger.error(f"Circuit breaker TRIPPED after {self._consecutive_errors} consecutive errors. "
                                 f"Entering cooldown for {cooldown} seconds.")
            return False

        return True

    def _record_error(self, error_msg: str = ""):
        """Record an error for circuit breaker tracking. (Issue 13)"""
        self._consecutive_errors += 1
        self._last_error_time = datetime.now(US_EASTERN)
        if self.logger:
            self.logger.debug(f"Error recorded ({self._consecutive_errors} consecutive): {error_msg}")

    def _record_success(self):
        """Record a successful operation, resetting error counter. (Issue 13)"""
        if self._consecutive_errors > 0:
            self._consecutive_errors = 0
            if self.logger:
                self.logger.debug("Success recorded, error counter reset")

    def _record_blocked_iteration(self, reason: str = ""):
        """
        Record a blocked iteration where no meaningful action was taken.
        Triggers shutdown after too many consecutive blocked iterations.
        """
        self._blocked_iterations += 1

        # Log every 10 blocked iterations to avoid spam
        if self._blocked_iterations % 10 == 0 and self.logger:
            self.logger.warning(f"[INFINITE LOOP DETECTION] {self._blocked_iterations} consecutive blocked iterations. Reason: {reason}")

        # Check if we should shutdown
        if self._blocked_iterations >= self._max_blocked_iterations:
            if self.logger:
                self.logger.error("=" * 60)
                self.logger.error("[INFINITE LOOP DETECTED] ENGINE SHUTTING DOWN")
                self.logger.error(f"  {self._blocked_iterations} consecutive iterations without progress")
                self.logger.error(f"  Last block reason: {reason}")
                self.logger.error("  This prevents wasted CPU/network resources")
                self.logger.error("=" * 60)
            self._safe_state_transition(EngineState.SHUTDOWN, f"Infinite loop detected - {self._blocked_iterations} blocked iterations")
            self._running = False

    def _reset_blocked_iterations(self):
        """Reset blocked iteration counter when meaningful action is taken."""
        if self._blocked_iterations > 0:
            self._blocked_iterations = 0
            self._last_successful_action_time = datetime.now(US_EASTERN)

    def _check_connection_health(self) -> bool:
        """
        Check IBKR connection health and attempt reconnect if needed. (Issue 10, 11)

        Returns:
            True if connection is healthy, False if recovery failed
        """
        check_interval = getattr(self.config, 'connection_health_check_interval', 30)

        # Only check periodically
        if self._last_connection_check:
            elapsed = (datetime.now(US_EASTERN) - self._last_connection_check).total_seconds()
            if elapsed < check_interval:
                return self._connection_healthy

        self._last_connection_check = datetime.now(US_EASTERN)

        if not self.broker or not self.broker.is_connected():
            self._connection_healthy = False
            if self.logger:
                self.logger.warning("IBKR connection lost - attempting reconnect...")

            # Try to reconnect
            if self._attempt_reconnect():
                self._connection_healthy = True
                self._reconnect_attempts = 0
                return True
            return False

        self._connection_healthy = True
        return True

    def _attempt_reconnect(self) -> bool:
        """
        Attempt to reconnect to IBKR with exponential backoff. (Issue 11)

        Returns:
            True if reconnection succeeded, False otherwise
        """
        if not hasattr(self.config, 'auto_reconnect') or not self.config.auto_reconnect:
            if self.logger:
                self.logger.info("Auto-reconnect disabled, skipping reconnection attempt")
            return False

        max_attempts = getattr(self.config, 'max_reconnect_attempts', 5)
        base_delay = getattr(self.config, 'reconnect_base_delay', 2.0)
        max_delay = getattr(self.config, 'reconnect_max_delay', 60.0)

        if self._reconnect_attempts >= max_attempts:
            if self.logger:
                self.logger.error(f"Max reconnection attempts ({max_attempts}) exceeded")
            return False

        self._reconnect_attempts += 1

        # Exponential backoff: delay = base * 2^(attempt-1), capped at max
        delay = min(base_delay * (2 ** (self._reconnect_attempts - 1)), max_delay)

        if self.logger:
            self.logger.info(f"Reconnection attempt {self._reconnect_attempts}/{max_attempts} "
                           f"(waiting {delay:.1f}s)...")

        time.sleep(delay)

        try:
            if self.broker:
                # Disconnect cleanly first
                self.broker.disconnect()
                time.sleep(1)

                # Bug #7 fix: Add timeout for reconnection attempt
                connect_timeout = getattr(self.config, 'connect_timeout', 30.0)
                if self.broker.connect(timeout=connect_timeout):
                    # Bug #6 fix: Verify broker health after reconnect
                    if not self._verify_broker_health():
                        if self.logger:
                            self.logger.error("Reconnection succeeded but broker health check failed")
                        return False

                    if self.logger:
                        self.logger.info("Reconnection successful!")

                    # After reconnection, verify position state if we have an open position
                    self._verify_position_after_reconnect()
                    return True
                else:
                    # Bug #4 fix: Ensure broker state is clean after failed connect
                    if self.logger:
                        self.logger.warning("Reconnection failed - ensuring broker is disconnected")
                    try:
                        self.broker.disconnect()
                    except Exception:
                        pass  # Ignore disconnect errors during cleanup
        except Exception as e:
            if self.logger:
                self.logger.error(f"Reconnection failed: {e}")
            # Bug #4 fix: Ensure clean state on exception
            try:
                if self.broker:
                    self.broker.disconnect()
            except Exception:
                pass

        return False

    def _verify_broker_health(self) -> bool:
        """
        Verify broker is fully functional after reconnection.
        Bug #6 fix: Check that broker can actually perform operations.
        """
        try:
            if not self.broker or not self.broker.is_connected():
                return False

            # Test 1: Can we get account info?
            account_values = self.broker.ib.accountValues()
            if not account_values:
                if self.logger:
                    self.logger.warning("Broker health check: No account values returned")
                return False

            # Test 2: Can we get positions? (even if empty, should not throw)
            try:
                _ = self.broker.ib.positions()
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"Broker health check: Cannot get positions - {e}")
                return False

            # Test 3: Can we get open orders? (even if empty, should not throw)
            try:
                _ = self.broker.ib.openOrders()
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"Broker health check: Cannot get orders - {e}")
                return False

            if self.logger:
                self.logger.debug("Broker health check passed")
            return True

        except Exception as e:
            if self.logger:
                self.logger.error(f"Broker health check failed: {e}")
            return False

    def _verify_position_after_reconnect(self):
        """Verify position state after IBKR reconnection."""
        # First, re-subscribe to market data (subscriptions were cleared on reconnect)
        self._resubscribe_market_data_after_reconnect()

        if not self.position_manager or not self.position_manager.has_open_position():
            return

        position = self.position_manager.current_position
        if not position:
            return

        self.logger.info("=" * 50)
        self.logger.info("[RECONNECT] Verifying position state...")
        self.logger.info("=" * 50)

        # Get our position's leg strikes
        our_leg_strikes = set()
        if position.built_strategy:
            for leg in position.built_strategy.legs:
                our_leg_strikes.add((leg.strike, leg.option_type.value))

        # Check IBKR positions - filter for OUR legs only
        spx_positions = [p for p in self.broker.ib.positions() if p.contract.symbol == 'SPX']
        our_legs_on_ibkr = []
        other_positions = []
        for pos in spx_positions:
            leg_key = (pos.contract.strike, pos.contract.right)
            if leg_key in our_leg_strikes:
                our_legs_on_ibkr.append(pos)
            else:
                other_positions.append(pos)

        if our_legs_on_ibkr:
            self.logger.info(f"✓ Our position verified on IBKR ({len(our_legs_on_ibkr)} legs)")
            for pos in our_legs_on_ibkr:
                self.logger.info(f"   - {pos.contract.strike} {pos.contract.right} qty={pos.position}")
            if other_positions:
                self.logger.info(f"   ℹ️ Note: {len(other_positions)} other SPX position(s) exist (not from this system)")

            # Check bracket orders if applicable
            if self._bracket_order_info:
                target_id = self._bracket_order_info.get('target_order_id')
                sl_id = self._bracket_order_info.get('sl_order_id')
                self.logger.info(f"   Bracket orders: Target={target_id}, SL={sl_id}")

                # Verify orders still exist
                open_orders = self.broker.ib.openOrders()
                order_ids = [o.orderId for o in open_orders]

                if target_id in order_ids and sl_id in order_ids:
                    self.logger.info("   ✓ Bracket orders still active on IBKR")
                    # Verify order prices haven't changed
                    for order in open_orders:
                        if order.orderId == target_id:
                            target_price = self._bracket_order_info.get('target_price')
                            if target_price and abs(order.lmtPrice - target_price) > 0.01:
                                self.logger.warning(f"   ⚠️ Target price changed: expected ${target_price:.2f}, actual ${order.lmtPrice:.2f}")
                            else:
                                self.logger.info(f"   ✓ Target order price verified: ${order.lmtPrice:.2f}")
                        elif order.orderId == sl_id:
                            sl_trigger = self._bracket_order_info.get('sl_trigger_price')
                            if sl_trigger and abs(order.auxPrice - sl_trigger) > 0.01:
                                self.logger.warning(f"   ⚠️ SL trigger changed: expected ${sl_trigger:.2f}, actual ${order.auxPrice:.2f}")
                            else:
                                self.logger.info(f"   ✓ SL trigger price verified: ${order.auxPrice:.2f}")
                else:
                    self.logger.warning("   ⚠️ Bracket orders may have filled during disconnect")
                    self.logger.warning("   Checking bracket order status...")
                    # Check if orders were filled
                    bracket_status = self.broker.check_bracket_order_status(target_id, sl_id, self._bracket_order_info.get('oca_group', ''))
                    if bracket_status['position_closed']:
                        self.logger.warning(f"   ⚠️ POSITION WAS CLOSED: {bracket_status['exit_type']} filled @ ${bracket_status['fill_price']:.2f}")
                        self.logger.warning("   Engine will process this on next monitoring cycle")
        else:
            # Our position legs not found - position may have been closed during disconnect
            self.logger.warning("=" * 50)
            self.logger.warning("⚠️ POSITION STATE MISMATCH AFTER RECONNECT")
            self.logger.warning("=" * 50)
            if other_positions:
                self.logger.warning(f"   Note: {len(other_positions)} other SPX position(s) exist (not from this system)")
            self.logger.warning("   Engine shows open position but IBKR has none")
            self.logger.warning("   Position may have been closed during disconnect")
            self.logger.warning("")
            self.logger.warning("   ACTION REQUIRED: Check TWS for trade history")
            self.logger.warning("=" * 50)

    def _resubscribe_market_data_after_reconnect(self):
        """Re-subscribe to market data after reconnection.

        Subscriptions are cleared on reconnect (Bug #5 fix), so we need to
        re-establish them for live price updates.
        """
        try:
            self.logger.info("[RECONNECT] Re-subscribing to market data...")

            # Re-subscribe to underlying (SPX) market data
            if self.config and self.config.underlying_symbol:
                from ibapi.contract import Contract
                contract = Contract()
                contract.symbol = self.config.underlying_symbol
                contract.secType = self.config.underlying_sec_type
                contract.exchange = self.config.underlying_exchange
                contract.currency = self.config.underlying_currency

                self.broker.subscribe_market_data(contract)
                self.logger.info(f"   ✓ Re-subscribed to {self.config.underlying_symbol} market data")

            # Re-fetch historical bars for ORB/indicators
            if self._refresh_bars_after_reconnect():
                self.logger.info("   ✓ Historical bars refreshed")
            else:
                self.logger.warning("   ⚠️ Failed to refresh historical bars")

        except Exception as e:
            self.logger.error(f"[RECONNECT] Error re-subscribing to market data: {e}")

    def _refresh_bars_after_reconnect(self) -> bool:
        """Refresh historical bars after reconnection."""
        try:
            if not self.config or not self.config.underlying_symbol:
                return False

            from ibapi.contract import Contract
            contract = Contract()
            contract.symbol = self.config.underlying_symbol
            contract.secType = self.config.underlying_sec_type
            contract.exchange = self.config.underlying_exchange
            contract.currency = self.config.underlying_currency

            # Fetch recent bars (last 2 hours should be enough for ORB + trend)
            bars = self.broker.request_historical_data(
                contract=contract,
                duration='2 D',
                bar_size=f'{self.config.bar_interval_minutes} mins',
                what_to_show='TRADES',
                use_rth=False,
                keep_up_to_date=True,
                timeout=30
            )

            if bars:
                self.logger.debug(f"   Refreshed {len(bars)} historical bars after reconnect")
                return True
            return False

        except Exception as e:
            self.logger.error(f"Error refreshing bars after reconnect: {e}")
            return False

    def _acquire_engine_lock(self, logger=None) -> bool:
        """
        Acquire engine lock to prevent multiple instances.

        Uses a lock file based on client_id to ensure only one engine
        instance runs per IBKR client ID.

        Works on both Windows and Linux.

        Returns:
            True if lock acquired, False if another instance is running
        """
        import os
        import sys
        from pathlib import Path

        # Create lock file path in state directory
        state_dir = Path("state")
        state_dir.mkdir(exist_ok=True)

        client_id = getattr(self.config, 'client_id', 1)
        self._lock_file_path = state_dir / f"engine_client_{client_id}.lock"

        try:
            # Open lock file (create if doesn't exist)
            self._lock_file_handle = open(self._lock_file_path, 'w')

            # Platform-specific locking
            if sys.platform == 'win32':
                # Windows: use msvcrt
                import msvcrt
                msvcrt.locking(self._lock_file_handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                # Linux/Mac: use fcntl
                import fcntl
                fcntl.flock(self._lock_file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

            # Write PID and timestamp to lock file
            self._lock_file_handle.write(f"PID: {os.getpid()}\n")
            self._lock_file_handle.write(f"Started: {datetime.now(US_EASTERN).isoformat()}\n")
            self._lock_file_handle.write(f"Client ID: {client_id}\n")
            self._lock_file_handle.flush()

            if logger:
                logger.info(f"Engine lock acquired (client_id={client_id})")

            return True

        except (IOError, OSError) as e:
            # Lock failed - another instance is running
            if self._lock_file_handle:
                self._lock_file_handle.close()
                self._lock_file_handle = None
            return False

    def _release_engine_lock(self):
        """Release engine lock on shutdown."""
        import sys

        if self._lock_file_handle:
            try:
                # Platform-specific unlocking
                if sys.platform == 'win32':
                    import msvcrt
                    try:
                        msvcrt.locking(self._lock_file_handle.fileno(), msvcrt.LK_UNLCK, 1)
                    except (OSError, IOError):
                        pass  # May fail if already unlocked
                else:
                    import fcntl
                    fcntl.flock(self._lock_file_handle.fileno(), fcntl.LOCK_UN)

                self._lock_file_handle.close()
                self._lock_file_handle = None

                # Remove lock file
                if self._lock_file_path and self._lock_file_path.exists():
                    self._lock_file_path.unlink()

                if self.logger:
                    self.logger.info("Engine lock released")
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"Error releasing engine lock: {e}")

    def _detect_manual_positions(self) -> bool:
        """
        Detect if there are manual positions that weren't opened by this engine. (Issue 3)

        This is called:
        1. At startup before position recovery
        2. Periodically during the trading loop

        A position is considered "manual" if:
        - There are option positions on our underlying in TWS
        - The position manager doesn't have them tracked
        - The positions weren't opened by this engine session

        Returns:
            True if manual positions detected, False otherwise
        """
        try:
            positions = self.broker.request_positions() if self.broker else {}

            # Filter for option positions on our underlying
            option_positions = {}
            for key, pos_info in positions.items():
                if (pos_info.sec_type == "OPT" and
                    pos_info.symbol == self.config.underlying_symbol and
                    pos_info.position != 0):
                    option_positions[key] = pos_info

            if not option_positions:
                # No positions in TWS - clear manual position flag if previously set
                if self._manual_positions_detected:
                    self._manual_positions_detected = False
                    self._manual_positions_logged = False  # Reset so we can log again if positions reappear
                    self._manual_position_symbols.clear()
                    if self.logger:
                        self.logger.info("[MANUAL POSITIONS] Cleared - no positions remain in TWS")
                return False

            # Check if position manager has these positions tracked
            if self.position_manager.has_open_position():
                # We have tracked positions - check if TWS positions match
                tracked_position = self.position_manager.current_position
                if tracked_position and tracked_position.built_strategy:
                    tracked_strikes = set()
                    for leg in tracked_position.built_strategy.legs:
                        tracked_strikes.add(f"{leg.strike}{leg.option_type.value}")

                    tws_strikes = set(f"{p.strike}{p.right}" for p in option_positions.values())

                    # Check if there are extra positions in TWS not tracked by engine
                    extra_positions = tws_strikes - tracked_strikes
                    if extra_positions:
                        self._manual_positions_detected = True
                        self._manual_position_symbols = extra_positions
                        if self.logger:
                            self.logger.warning(f"Additional manual positions detected alongside engine position")
                            self.logger.warning(f"  Engine tracking: {tracked_strikes}")
                            self.logger.warning(f"  Extra in TWS: {extra_positions}")
                        return True
                    else:
                        # All TWS positions are tracked by engine
                        return False
            else:
                # Position manager doesn't have positions but TWS does - these are manual
                self._manual_positions_detected = True
                self._manual_position_symbols = set(f"{p.strike}{p.right}" for p in option_positions.values())

                # Only log once to avoid spam
                if self.logger and not self._manual_positions_logged:
                    self.logger.info(f"[MANUAL POSITIONS] Detected {len(option_positions)} untracked legs in TWS (will ignore)")
                    for key, pos in option_positions.items():
                        self.logger.info(f"  - {pos.symbol} {pos.strike}{pos.right} x{pos.position}")
                    self._manual_positions_logged = True

                return True

        except Exception as e:
            if self.logger:
                self.logger.warning(f"Could not check for manual positions: {e}")
            return False

    def _detect_orphan_engine_spread(self, option_positions: dict) -> tuple:
        """
        Detect if orphan positions are from this engine by checking signal CSV.

        This checks today's signal CSV for executed trades that match the
        IBKR positions. If a match is found, the position is confirmed to be
        from this engine.

        Args:
            option_positions: Dict of option positions from IBKR

        Returns:
            Tuple of (is_engine_spread: bool, csv_exists: bool, reason: str)
            - is_engine_spread: True if positions match an executed signal
            - csv_exists: True if signal CSV file was found
            - reason: Description of result for logging
        """
        if not option_positions:
            return (False, False, "No positions to check")

        positions = list(option_positions.values())

        # Build leg signature from IBKR positions: "P5800S|P5750B" format
        # S = short (position < 0), B = buy/long (position > 0)
        ibkr_legs = set()
        for pos in positions:
            side = "S" if pos.position < 0 else "B"
            leg_str = f"{pos.right}{int(pos.strike)}{side}"
            ibkr_legs.add(leg_str)

        self.logger.debug(f"  IBKR position legs: {ibkr_legs}")

        # Check today's signal CSV for matching executed trade
        try:
            from pathlib import Path
            import csv
            from datetime import datetime

            # Find today's signal file
            today_str = datetime.now().strftime("%Y-%m-%d")
            signal_dir = Path("logs") / today_str / "signals"

            if not signal_dir.exists():
                self.logger.debug(f"  Signal directory not found: {signal_dir}")
                return (False, False, f"Signal directory not found: {signal_dir}")

            signal_files = list(signal_dir.glob("signals_*.csv"))
            if not signal_files:
                self.logger.debug("  No signal CSV files found")
                return (False, False, "No signal CSV files found for today")

            # Read the most recent signal file
            signal_file = sorted(signal_files)[-1]
            self.logger.debug(f"  Checking signal file: {signal_file}")

            with open(signal_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # Check if this signal was executed
                    if row.get('executed', '').upper() != 'YES':
                        continue

                    # Parse legs from CSV: "P5800S|P5750B" format
                    legs_str = row.get('legs', '')
                    if not legs_str:
                        continue

                    csv_legs = set(legs_str.split('|'))

                    # Check if legs match
                    if csv_legs == ibkr_legs:
                        self.logger.info(f"  ✓ MATCH FOUND in signal CSV!")
                        self.logger.info(f"    Signal time: {row.get('timestamp', 'unknown')}")
                        self.logger.info(f"    Strategy: {row.get('strategy_selected', 'unknown')}")
                        self.logger.info(f"    Legs: {legs_str}")
                        return (True, True, "Match found in signal CSV")

            self.logger.debug("  No matching executed signal found in CSV")
            return (False, True, "No matching executed signal in CSV - likely manual positions")

        except Exception as e:
            self.logger.warning(f"  Error checking signal CSV: {e}")
            return (False, False, f"Error reading signal CSV: {e}")

    def _recover_orphan_spread(self, option_positions: dict) -> bool:
        """
        Recover an orphan spread that has no state file.

        This creates a Position object from the IBKR positions and places
        bracket exit orders to protect the position.

        Args:
            option_positions: Dict of option positions from IBKR

        Returns:
            True if recovery succeeded, False otherwise
        """
        from .trade_management.entry_management import BuiltStrategy, BuiltLeg
        from .broker import OptionContract
        from .enums import OptionType, OrderSide
        from datetime import datetime

        try:
            positions = list(option_positions.values())

            # Determine strategy type based on leg count
            if len(positions) == 2:
                strategy_id = "STRAT_RECOVERED_VERTICAL"
            elif len(positions) == 4:
                strategy_id = "STRAT_RECOVERED_IRON_CONDOR"
            else:
                strategy_id = "STRAT_RECOVERED_UNKNOWN"

            # Get quantity (all legs should have same absolute qty)
            contracts = abs(positions[0].position)

            # Build legs from IBKR positions
            legs = []
            entry_spread = 0.0

            for i, pos_info in enumerate(positions):
                opt_type = OptionType.from_string(pos_info.right)
                # Position > 0 means we're long, < 0 means short
                side = OrderSide.BUY if pos_info.position > 0 else OrderSide.SELL

                # Create option contract
                contract = OptionContract(
                    symbol=pos_info.symbol,
                    expiry=pos_info.expiry,
                    strike=pos_info.strike,
                    right=pos_info.right,
                    exchange="SMART",
                    multiplier=100,
                )
                contract.contract = pos_info.contract  # Use actual IBKR contract

                # Get current price for this leg
                leg_price = pos_info.avg_cost / 100.0 if pos_info.avg_cost else 0.0

                leg = BuiltLeg(
                    leg_index=i + 1,
                    option_type=opt_type,
                    side=side,
                    strike=pos_info.strike,
                    expiry=pos_info.expiry,
                    quantity=1,  # Per contract
                    entry_price=leg_price,
                    current_price=leg_price,
                    target_delta=0.0,
                    actual_delta=0.0,
                    contract=contract,
                )
                legs.append(leg)

                # Calculate entry spread (BUY legs positive, SELL legs negative)
                if side == OrderSide.BUY:
                    entry_spread += leg_price
                else:
                    entry_spread -= leg_price

            # Determine if credit or debit spread
            is_credit = entry_spread < 0
            entry_spread = abs(entry_spread)

            # Calculate target and stop based on config
            if is_credit:
                target_spread = entry_spread * (1 - self.config.credit_target_factor)
                stop_spread = entry_spread * (1 + self.config.credit_stop_factor)
            else:
                target_spread = entry_spread * (1 + self.config.debit_target_factor)
                stop_spread = entry_spread * (1 - self.config.debit_stop_factor)

            # Create built strategy
            built_strategy = BuiltStrategy(
                strategy_id=strategy_id,
                legs=legs,
                entry_spread=entry_spread if not is_credit else -entry_spread,
                is_credit=is_credit,
            )

            # Create position
            position = self.position_manager.create_position(
                built_strategy=built_strategy,
                contracts=contracts,
                direction_bias="",
                vol_regime="",
                trend_regime="",
                underlying_price=self.broker.get_price(self.config.underlying_symbol) or 0,
                fill_spread=entry_spread if not is_credit else -entry_spread,
            )

            self.logger.info(f"  Created position: {position.position_id}")
            self.logger.info(f"  Strategy: {strategy_id}")
            self.logger.info(f"  Contracts: {contracts}")
            self.logger.info(f"  Entry spread: ${entry_spread:.2f} ({'CREDIT' if is_credit else 'DEBIT'})")
            self.logger.info(f"  Target: ${target_spread:.2f}")
            self.logger.info(f"  Stop: ${stop_spread:.2f}")

            # Build legs for exit order placement
            exit_legs = []
            position_conids = []
            for leg in legs:
                leg_dict = {
                    'contract': leg.contract.contract,
                    'action': leg.side.value,
                    'quantity': leg.quantity,
                }
                exit_legs.append(leg_dict)
                position_conids.append(leg.contract.contract.conId)

            # FIRST: Scan for existing bracket orders on IBKR
            # This prevents duplicate orders if crash happened after brackets were placed
            self.logger.info("  Scanning for existing bracket orders on IBKR...")
            scan_result = self.broker.scan_for_existing_exit_orders(position_conids)

            if scan_result['found'] and scan_result['target_order_id'] and scan_result['sl_order_id']:
                # Found existing bracket orders - adopt them instead of placing new ones
                self.logger.info("=" * 50)
                self.logger.info("  [FOUND] Existing bracket orders on IBKR!")
                self.logger.info("=" * 50)
                self._bracket_order_info = {
                    'target_order_id': scan_result['target_order_id'],
                    'sl_order_id': scan_result['sl_order_id'],
                    'oca_group': scan_result.get('oca_group'),
                    'target_price': scan_result.get('target_price'),
                    'sl_trigger_price': scan_result.get('sl_trigger_price'),
                }
                self.logger.info(f"  Adopted Target order ID: {scan_result['target_order_id']}")
                self.logger.info(f"  Adopted SL order ID: {scan_result['sl_order_id']}")
                bracket_orders_ready = True
            else:
                # No existing bracket orders - place new ones
                self.logger.info("  No existing bracket orders found - placing new ones...")
                exit_result = self.broker.place_exit_orders_only(
                    legs=exit_legs,
                    quantity=contracts,
                    entry_fill_price=entry_spread if not is_credit else -entry_spread,
                    credit_target_pct=self.config.credit_target_factor,
                    credit_sl_pct=self.config.credit_stop_factor,
                    debit_target_pct=self.config.debit_target_factor,
                    debit_sl_pct=self.config.debit_stop_factor,
                    sl_limit_offset=self.config.sl_limit_offset,
                )

                if exit_result['success']:
                    self._bracket_order_info = {
                        'target_order_id': exit_result['target_order_id'],
                        'sl_order_id': exit_result['sl_order_id'],
                        'oca_group': exit_result['oca_group'],
                        'target_price': exit_result['target_price'],
                        'sl_trigger_price': exit_result['sl_trigger_price'],
                    }
                    self.logger.info(f"  Target order ID: {exit_result['target_order_id']}")
                    self.logger.info(f"  SL order ID: {exit_result['sl_order_id']}")
                    bracket_orders_ready = True
                else:
                    self.logger.error(f"  Failed to place exit orders: {exit_result.get('error_message', 'Unknown')}")
                    bracket_orders_ready = False

            if bracket_orders_ready:
                # Save state for future crash recovery
                if self.state_manager:
                    self.state_manager.save_state(position, self.broker, self._bracket_order_info)
                    self.logger.info("  State file created for recovered position")

                # Start position monitoring
                self.position_monitor.start()
                self._safe_state_transition(EngineState.POSITION_OPEN, "Orphan spread recovered")

                # Reset warnings
                self._warning_10min_shown = False
                self._warning_5min_shown = False
                self._warning_2min_shown = False

                return True
            else:
                # bracket_orders_ready is False - error already logged above
                return False

        except Exception as e:
            self.logger.error(f"  Recovery error: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return False

    def _should_skip_due_to_manual_positions(self) -> bool:
        """
        Check if engine should skip execution due to manual positions. (Issue 3)

        UPDATED: Engine now IGNORES manual positions and continues its own work.
        Manual positions are logged for informational purposes but do NOT block trading.
        The engine only tracks positions it creates (with saved state).

        Returns:
            Always False - manual positions don't block engine operation
        """
        # Engine ignores manual positions - they don't block trading
        # The engine will only monitor positions it creates (tracked via saved state)
        return False

    def _is_signal_only_mode(self) -> bool:
        """Check if engine is in signal-only mode. (Issue 1, 2)"""
        return hasattr(self.config, 'signal_only_mode') and self.config.signal_only_mode

    def _cancel_unfilled_orders(self, include_global_cancel: bool = False):
        """Cancel all unfilled orders before processing new signals. (Issue 5)

        Args:
            include_global_cancel: If True, use Global Cancel to clear draft orders too.
                                   Use True at startup, False during running.
        """
        if self.broker:
            try:
                cancelled_count = self.broker.cancel_all_open_orders(include_global_cancel=include_global_cancel)
                if self.logger:
                    if cancelled_count > 0:
                        self.logger.warning(f"Cancelled {cancelled_count} unfilled orders")
                    else:
                        self.logger.debug("No unfilled orders to cancel")
                # Reset order tracking state completely
                self._reset_order_tracking()
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"Error cancelling unfilled orders: {e}")

    def _cancel_unfilled_orders_except(self, protected_order_ids: set):
        """Cancel unfilled orders EXCEPT for protected bracket orders.

        This is used during startup recovery to preserve existing bracket exit orders
        while still clearing any stale/draft orders.

        Args:
            protected_order_ids: Set of order IDs that should NOT be cancelled
        """
        if not self.broker or not self.broker.ib:
            return

        try:
            # Request all open orders
            self.broker.ib.reqAllOpenOrders()
            self.broker.ib.sleep(0.5)

            open_trades = self.broker.ib.openTrades()
            cancelled_count = 0
            preserved_count = 0

            for trade in open_trades:
                order_id = trade.order.orderId
                status = trade.orderStatus.status

                # Skip already completed orders
                if status in ('Filled', 'Cancelled', 'Inactive'):
                    continue

                if order_id in protected_order_ids:
                    # Preserve this order - it's a bracket order from saved state
                    self.logger.info(f"  [PRESERVED] Order {order_id} (bracket order) - Status: {status}")
                    preserved_count += 1
                else:
                    # Cancel this order
                    try:
                        self.broker.ib.cancelOrder(trade.order)
                        self.logger.debug(f"  Cancelled order {order_id}")
                        cancelled_count += 1
                    except Exception as e:
                        self.logger.warning(f"  Failed to cancel order {order_id}: {e}")

            if cancelled_count > 0 or preserved_count > 0:
                self.logger.info(f"[STARTUP] Orders: {cancelled_count} cancelled, {preserved_count} preserved (bracket orders)")

        except Exception as e:
            self.logger.warning(f"Error in selective order cancellation: {e}")

    def _reconcile_orders_and_positions(self) -> bool:
        """
        Reconcile internal state with actual TWS orders and positions. (Issue 5, 6)

        This is critical for:
        1. Detecting stale orders that might have filled from previous sessions
        2. Ensuring position tracking matches actual TWS positions
        3. Preventing duplicate orders

        IMPORTANT: Must check for saved state FIRST to preserve bracket orders!

        Returns:
            True if state is consistent, False if discrepancies found
        """
        if not self.broker:
            return True

        try:
            # CRITICAL: Check for saved state FIRST to get bracket order IDs
            # We need to preserve these orders during reconciliation
            protected_order_ids = set()
            if not self._startup_reconciliation_done and self.state_manager:
                saved_state = self.state_manager.load_state()
                if saved_state and saved_state.bracket_order_info:
                    # Get bracket order IDs from saved state - these should NOT be cancelled
                    target_id = saved_state.bracket_order_info.target_order_id
                    sl_id = saved_state.bracket_order_info.sl_order_id
                    if target_id:
                        protected_order_ids.add(target_id)
                    if sl_id:
                        protected_order_ids.add(sl_id)
                    self.logger.info(f"[RECOVERY] Found saved bracket orders to preserve: Target={target_id}, SL={sl_id}")

            # ISSUE 5 FIX: Only run Global Cancel at STARTUP to clear draft orders
            # Draft orders (not transmitted) are only visible in TWS, not via API
            # During running, we skip the expensive Global Cancel to reduce noise
            if not self._startup_reconciliation_done:
                cancel_stale = getattr(self.config, 'cancel_stale_orders_on_startup', True)
                if cancel_stale:
                    if protected_order_ids:
                        # We have bracket orders to preserve - cancel selectively
                        self.logger.debug(f"Cancelling stale orders (preserving bracket orders: {protected_order_ids})...")
                        self._cancel_unfilled_orders_except(protected_order_ids)
                    else:
                        # No bracket orders to preserve - cancel all
                        self.logger.debug("Running Global Cancel to clear any stale/draft orders...")
                        self._cancel_unfilled_orders(include_global_cancel=True)
                self._startup_reconciliation_done = True

            # Step 1: Check for any remaining open/pending orders in TWS
            # ISSUE 5 FIX: Request ALL open orders including from previous sessions
            if self.broker.ib:
                self.broker.ib.reqAllOpenOrders()
                self.broker.ib.sleep(1)  # Wait for orders to be received
            open_trades = self.broker.ib.openTrades() if self.broker.ib else []
            spx_orders = []

            for trade in open_trades:
                contract = trade.contract
                # Check if this is an SPX-related order
                if hasattr(contract, 'symbol') and contract.symbol in ('SPX', 'SPXW'):
                    spx_orders.append(trade)
                elif hasattr(contract, 'comboLegs') and contract.comboLegs:
                    # Combo order - check if any leg is SPX
                    spx_orders.append(trade)

            if spx_orders:
                # Don't warn about bracket exit orders - they're supposed to stay open
                bracket_order_ids = set()
                if self._bracket_order_info:
                    bracket_order_ids.add(self._bracket_order_info.get('target_order_id'))
                    bracket_order_ids.add(self._bracket_order_info.get('sl_order_id'))

                unexpected_orders = [t for t in spx_orders if t.order.orderId not in bracket_order_ids]
                if unexpected_orders:
                    self.logger.warning(f"Found {len(unexpected_orders)} unexpected SPX orders after Global Cancel")
                    for trade in unexpected_orders:
                        order = trade.order
                        status = trade.orderStatus.status
                        self.logger.warning(f"  Order {order.orderId}: {order.action} {order.totalQuantity} - Status: {status}")

            # Step 2: Check TWS positions vs internal tracking
            positions = self.broker.request_positions()
            option_positions = {}

            for key, pos_info in positions.items():
                if (pos_info.sec_type == "OPT" and
                    pos_info.symbol == self.config.underlying_symbol and
                    pos_info.position != 0):
                    option_positions[key] = pos_info

            internal_has_position = self.position_manager.has_open_position() if self.position_manager else False
            tws_has_positions = len(option_positions) > 0

            # Step 3: Detect position mismatch (Issue 6)
            if tws_has_positions and not internal_has_position:
                # TWS has positions but we're not tracking them
                # These are manual positions - IGNORE them and continue normal operation
                # Only log once to avoid spam in the logs
                if not self._manual_positions_logged:
                    self.logger.info("=" * 60)
                    self.logger.info("[MANUAL POSITIONS] Untracked positions in TWS (ignoring)")
                    self.logger.info("=" * 60)
                    self.logger.info(f"  TWS has {len(option_positions)} untracked legs:")
                    for key, pos in option_positions.items():
                        self.logger.info(f"    - {pos.symbol} {pos.strike}{pos.right} x{pos.position}")
                    self.logger.info("")
                    self.logger.info("  Engine will IGNORE these and continue normal operation.")
                    self.logger.info("=" * 60)
                    self._manual_positions_logged = True

                # Track for informational purposes but DON'T block
                self._manual_positions_detected = True
                self._manual_position_symbols = set(f"{p.strike}{p.right}" for p in option_positions.values())
                # Continue - don't return False

            elif internal_has_position and not tws_has_positions:
                # We think we have a position but TWS doesn't
                self.logger.warning("=" * 60)
                self.logger.warning("[POSITION STATE MISMATCH] Internal position not found in TWS")
                self.logger.warning("=" * 60)
                self.logger.warning("  Engine thinks it has a position but TWS shows none!")
                self.logger.warning("  Clearing internal position state...")
                self.logger.warning("=" * 60)

                # Clear internal position state by resetting the position manager
                # Don't call close_position() as that requires valid exit parameters
                if self.position_manager:
                    self.position_manager.current_position = None

                # ORB strategy: One trade per session - shutdown after position closed
                self.logger.info("")
                self.logger.info("=" * 70)
                self.logger.info("***   ORB TRADE COMPLETED - ENGINE SHUTTING DOWN   ***")
                self.logger.info("=" * 70)
                self.logger.info("Position state cleared (not found in TWS)")
                self.logger.info("Strategy rule: One trade per ORB session")
                self.logger.info("=" * 70)
                self._safe_state_transition(EngineState.SHUTDOWN, "Position state cleared - ORB trade completed")
                self._running = False
                return False

            elif internal_has_position and tws_has_positions:
                # Both have positions - verify quantities match
                tracked_position = self.position_manager.current_position
                if tracked_position:
                    internal_contracts = tracked_position.contracts
                    tws_contracts = min(abs(int(pos.position)) for pos in option_positions.values())

                    if internal_contracts != tws_contracts:
                        self.logger.warning("=" * 60)
                        self.logger.warning("POSITION SIZE MISMATCH")
                        self.logger.warning("=" * 60)
                        self.logger.warning(f"  Internal tracking: {internal_contracts} contracts")
                        self.logger.warning(f"  TWS actual: {tws_contracts} contracts")
                        self.logger.warning("")
                        self.logger.warning("  A stale order may have filled, inflating the position.")
                        self.logger.warning("  Updating internal tracking to match TWS...")
                        self.logger.warning("=" * 60)

                        # Update internal tracking to match TWS
                        tracked_position.contracts = tws_contracts
                        return False

            return True

        except Exception as e:
            if self.logger:
                self.logger.error(f"Error reconciling orders/positions: {e}")
            return True  # Continue anyway to avoid blocking

    def initialize(self) -> bool:
        """Initialize all components."""
        # Note: Logger not yet initialized, use temporary console logger
        import logging
        temp_logger = logging.getLogger("clientInit")
        temp_logger.setLevel(logging.INFO)
        if not temp_logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s - %(message)s", "%H:%M:%S"))
            temp_logger.addHandler(handler)

        temp_logger.info("=" * 60)
        temp_logger.info("Initializing client Options Trading Engine")
        temp_logger.info("=" * 60)

        # Load configuration
        self.config_loader = ConfigLoader(self.config_dir)
        if not self.config_loader.load_all():
            temp_logger.error("=" * 60)
            temp_logger.error("[CONFIGURATION FAILED] Cannot load configuration files")
            temp_logger.error("=" * 60)
            temp_logger.error(f"  Config directory: {self.config_dir}")
            temp_logger.error("  Validation errors:")
            for error in self.config_loader.get_validation_errors():
                temp_logger.error(f"    - {error}")
            temp_logger.error("")
            temp_logger.error("  Please check:")
            temp_logger.error("    - All required config files exist in config directory")
            temp_logger.error("    - Config values are valid (numbers, times, etc.)")
            temp_logger.error("    - No syntax errors in config files")
            temp_logger.error("  Action: ENGINE SHUTTING DOWN")
            temp_logger.error("=" * 60)
            # Set state directly since logger not yet initialized
            self.state = EngineState.SHUTDOWN
            return False

        self.config = self.config_loader.config

        # Check for multiple engine protection (lock file)
        if not self._acquire_engine_lock(temp_logger):
            temp_logger.error("=" * 60)
            temp_logger.error("[ENGINE LOCK FAILED] Another engine instance is running")
            temp_logger.error("=" * 60)
            temp_logger.error(f"  Client ID: {self.config.client_id}")
            temp_logger.error("  Only one engine instance per client ID is allowed")
            temp_logger.error("")
            temp_logger.error("  Please check:")
            temp_logger.error("    - No other engine is running with the same client ID")
            temp_logger.error("    - If previous engine crashed, delete the lock file manually")
            temp_logger.error(f"    - Lock file: {self._lock_file_path}")
            temp_logger.error("  Action: ENGINE SHUTTING DOWN")
            temp_logger.error("=" * 60)
            self.state = EngineState.SHUTDOWN
            return False

        # Initialize logger
        self.logger = TradingLogger(
            log_dir=self.config.log_dir,
            log_level=self.config.log_level,
            log_trades=self.config.log_trades,
            log_signals=self.config.log_signals
        )

        self.logger.log_startup("2.0.0", self.config_dir)

        # Log all trading concept explanations ONCE at startup
        self.logger.log_all_explanations(self.config)

        # Initialize IBKRBroker - Single point of contact for all IBKR operations
        self.broker = IBKRBroker(self.logger, self.config)

        # Create market data adapter for indicators
        self._market_data_adapter = MarketDataAdapter(self.broker, self.config, self.logger)

        # Initialize indicators
        self.orb = OpeningRangeBreakout(
            self.config, self._market_data_adapter, self.logger
        )
        self.trend = TrendFilter(
            self.config, self._market_data_adapter, self.logger
        )
        self.volatility = VolatilityAnalyzer(
            self.config, self.broker, self.logger
        )
        self.signal_generator = SignalGenerator(
            self.config, self._market_data_adapter,
            self.orb, self.trend, self.volatility, self.logger
        )

        # Initialize strategy components
        self.strategy_selector = StrategySelector(
            self.config_loader, self.logger
        )
        self.strategy_builder = StrategyBuilder(
            self.config, self.broker, self.logger
        )

        # Initialize risk management
        self.position_manager = PositionManager(self.config, self.logger)
        self.risk_calculator = RiskCalculator(self.config, self.logger)
        self.position_monitor = PositionMonitor(
            self.config, self.position_manager, self.broker, self.logger
        )

        # Set up callbacks
        self.position_monitor.set_exit_callback(self._on_exit_triggered)
        self._market_data_adapter.register_bar_callback(self._on_new_bar)

        # Initialize state persistence for crash recovery
        from pathlib import Path
        self.state_manager = StateManager(
            state_dir=Path("state"),
            underlying_symbol=self.config.underlying_symbol,
            logger=self.logger,
            engine_id="client_engine"
        )

        # Print config summary
        self.config_loader.print_config_summary()
        self.logger.info("Engine initialization complete")

        return True

    def connect(self) -> bool:
        """Connect to IBKR using the broker."""
        self.logger.info("Connecting to IBKR...")

        try:
            # Single connect call handles everything
            if not self.broker.connect():
                self.logger.error("=" * 60)
                self.logger.error("[CONNECTION FAILED] Cannot connect to IBKR TWS/Gateway")
                self.logger.error("=" * 60)
                self.logger.error("  Possible causes:")
                self.logger.error("    - TWS/Gateway is not running")
                self.logger.error("    - Wrong port or host configuration")
                self.logger.error("    - API connections not enabled in TWS")
                self.logger.error("    - Another application using the same client ID")
                self.logger.error("    - Firewall blocking connection")
                self.logger.error("")
                self.logger.error("  Troubleshooting steps:")
                self.logger.error("    1. Ensure TWS or IB Gateway is running and logged in")
                self.logger.error("    2. Check API settings: Configure > API > Settings")
                self.logger.error("    3. Verify 'Enable ActiveX and Socket Clients' is checked")
                self.logger.error("    4. Confirm port matches config (7497=paper, 7496=live)")
                self.logger.error("    5. Try a different client ID")
                self.logger.error("=" * 60)
                self._safe_state_transition(EngineState.SHUTDOWN, "Failed to connect to IBKR")
                return False

            # Print broker status
            self.broker.print_status()

            # Start market data first - CRITICAL for strategy operation
            if not self._start_market_data():
                return False  # Shutdown already triggered in _start_market_data

            # Load historical IV for IVP calculation
            self.volatility.fetch_and_load_historical_iv()

            # Pre-fetch and cache option chain at startup (when connection is fresh)
            self._prefetch_option_chain()

            # Run health checks after market data is fetched
            self._run_startup_health_checks()

            self.logger.info("Connected to IBKR successfully")
            return True

        except (ConnectionError, ConnectionTimeoutError) as e:
            self.logger.error("=" * 60)
            self.logger.error("[CONNECTION ERROR] IBKR connection failed with exception")
            self.logger.error("=" * 60)
            self.logger.error(f"  Error: {e.message}")
            self.logger.error("  This is a critical error - engine cannot operate")
            self.logger.error("=" * 60)
            self._safe_state_transition(EngineState.SHUTDOWN, f"Connection error: {e.message}")
            return False

    def _prefetch_option_chain(self):
        """Pre-fetch and cache option chain at startup when connection is fresh."""
        self.logger.debug("Pre-fetching option chain for caching...")
        try:
            # Get current price for ATM reference from broker
            current_price = self.broker.get_latest_price()
            if not current_price or current_price <= 0:
                # NO FALLBACK - cannot operate without real market price
                self.logger.error("=" * 60)
                self.logger.error("[PRICE FETCH FAILED] Cannot get underlying price from IBKR")
                self.logger.error("=" * 60)
                self.logger.error("  Cannot prefetch option chain without valid price")
                self.logger.error("  Possible causes:")
                self.logger.error("    - IBKR not connected or market data not subscribed")
                self.logger.error("    - Market closed and no cached price available")
                self.logger.error("  Action: ENGINE SHUTDOWN - cannot operate without price data")
                self.logger.error("=" * 60)
                self._safe_state_transition(EngineState.SHUTDOWN, "Price fetch failed - no market data")
                return

            # Fetch option chain (will be cached by broker)
            chain = self.broker.get_option_chain_by_rule(
                symbol=self.config.underlying_symbol,
                expiry_rule=self.config.default_expiry_rule,
                underlying_price=current_price,
                refresh=True
            )

            if chain:
                self.logger.debug(f"Option chain cached: {len(chain.strikes)} strikes, {len(chain.calls)} calls, {len(chain.puts)} puts")
            else:
                self.logger.warning("Could not pre-fetch option chain - will retry when needed")
        except Exception as e:
            self.logger.warning(f"Option chain pre-fetch failed: {e} - will retry when needed")

    def _run_startup_health_checks(self):
        """Run basic startup checks."""
        self.logger.debug("Running startup checks...")

        # Check execution mode
        if self._is_signal_only_mode():
            self.logger.info("  [MODE] SIGNAL-ONLY MODE ENABLED - No orders will be placed")
        else:
            self.logger.info("  [MODE] LIVE TRADING MODE - Orders will be placed on IBKR")

        # Check broker connection
        if self.broker and self.broker.is_connected():
            self.logger.debug("  [OK] Broker connected")
        else:
            self.logger.warning("  [WARN] Broker not connected")

        # Check if we have market data
        price = self.broker.get_latest_price() if self.broker else None
        if price and price > 0:
            self.logger.debug(f"  [OK] Market data available: ${price:.2f}")
        else:
            self.logger.warning("  [WARN] No market data yet")

        # Issue 3: Check auto-recovery setting
        # NOTE: auto_recover_positions only allows recovery from SAVED STATE (crash recovery)
        # It does NOT mean "recover any position found in TWS"
        auto_recover = getattr(self.config, 'auto_recover_positions', False)
        if auto_recover:
            self.logger.info("  [MODE] AUTO-RECOVERY ENABLED - Will recover from saved state if available")
        else:
            self.logger.info("  [MODE] AUTO-RECOVERY DISABLED - All existing positions treated as manual")

        # Issue 5, 6: Reconcile orders and positions at startup
        self.logger.debug("  Reconciling orders and positions with TWS...")
        self._reconcile_orders_and_positions()

        # Check for existing positions to recover
        self._recover_existing_positions()

        # Log final manual position status (informational only - doesn't block execution)
        if self._manual_positions_detected:
            self.logger.info("  [INFO] Manual positions detected - will be ignored")

    def _recover_existing_positions(self):
        """
        Check for existing option positions in TWS and recover monitoring.
        This allows the engine to resume monitoring after a crash/restart.

        IMPORTANT: Engine only recovers positions it created (with saved state file).
        ANY positions found without matching saved state are treated as MANUAL.

        Recovery flow:
        1. Check for saved state file (created when engine opens a position)
        2. If saved state exists and reconciles with IBKR -> recover position
        3. If no saved state OR reconciliation fails -> treat ALL positions as manual

        This ensures the engine NEVER tries to manage positions it didn't create.
        """
        self.logger.debug("Checking for existing positions...")

        # Step 1: Check for saved state (crash recovery)
        if self.state_manager:
            saved_state = self.state_manager.load_state()
            if saved_state:
                self.logger.info(f"  Found saved state: {saved_state.position.position_id}")

                # Reconcile with IBKR
                result = self.state_manager.reconcile_with_ibkr(saved_state, self.broker)

                if result.status == ReconciliationStatus.EXACT_MATCH:
                    self.logger.info("=" * 60)
                    self.logger.info("[CRASH RECOVERY] Position reconciled successfully")
                    self.logger.info("=" * 60)
                    self._recover_from_saved_state(saved_state)
                    return

                elif result.status == ReconciliationStatus.NO_MATCH:
                    self.logger.info("  Position was closed externally - clearing saved state")
                    self.state_manager.clear_state()
                    # Fall through to normal position detection

                elif result.status == ReconciliationStatus.IBKR_UNAVAILABLE:
                    self.logger.warning("=" * 60)
                    self.logger.warning("[RECOVERY WARNING] Cannot connect to IBKR for reconciliation")
                    self.logger.warning("=" * 60)
                    self.logger.warning(f"  Saved state exists: {saved_state.position.position_id}")
                    self.logger.warning("  Cannot verify position with IBKR")
                    self.logger.warning("  Engine will block new entries until reconciled")
                    self._manual_positions_detected = True
                    self._manual_positions_logged = True
                    return

                elif result.status == ReconciliationStatus.EXTRA_POSITIONS:
                    # Saved position matches IBKR, but IBKR has additional manual positions
                    # Recover the saved position and ignore the extras
                    self.logger.info("=" * 60)
                    self.logger.info("[CRASH RECOVERY] Position reconciled with manual positions detected")
                    self.logger.info("=" * 60)
                    self.logger.info(f"  Saved position: {saved_state.position.position_id} - MATCHED ✓")
                    self.logger.info(f"  Additional manual positions in IBKR will be IGNORED")
                    for warning in result.warnings:
                        self.logger.info(f"    - {warning}")
                    self.logger.info("  Engine will recover saved position and continue monitoring")
                    self.logger.info("=" * 60)
                    self._recover_from_saved_state(saved_state)
                    return

                else:
                    # Other failures (QUANTITY_MISMATCH, PARTIAL_MATCH, etc.)
                    self.logger.warning("=" * 60)
                    self.logger.warning(f"[RECOVERY FAILED] {result.status.value}")
                    self.logger.warning("=" * 60)
                    self.logger.warning(f"  Reason: {result.reason}")
                    for warning in result.warnings:
                        self.logger.warning(f"  - {warning}")
                    self.logger.warning("  Treating as manual positions for safety")
                    self._manual_positions_detected = True
                    self._manual_positions_logged = True
                    return

        try:
            # Get all positions from broker
            positions = self.broker.request_positions()

            if not positions:
                self.logger.debug("  No existing positions found")
                return

            # Filter for option positions on our underlying
            option_positions = {}
            for key, pos_info in positions.items():
                if (pos_info.sec_type == "OPT" and
                    pos_info.symbol == self.config.underlying_symbol and
                    pos_info.position != 0):
                    option_positions[key] = pos_info
                    self.logger.info(f"  Found position: {pos_info.symbol} {pos_info.strike} {pos_info.right} x{pos_info.position}")

            if not option_positions:
                self.logger.info("  No option positions for underlying found")
                return

            # IMPORTANT: If we reach here, it means NO SAVED STATE exists or doesn't match.
            # Without saved state, we CANNOT know if these are engine-created or manual positions.
            #
            # We check the signal CSV to confirm if positions are from this engine.
            # If CSV is also deleted, we cannot confirm and must warn user.

            # Check signal CSV to confirm if positions are from this engine
            is_engine_spread, csv_exists, reason = self._detect_orphan_engine_spread(option_positions)

            if is_engine_spread:
                # Confirmed engine spread via signal CSV - try to recover it automatically
                # This handles: state file deleted, crash during entry, etc.
                self.logger.warning("=" * 60)
                self.logger.warning("[ORPHAN SPREAD DETECTED - ATTEMPTING RECOVERY]")
                self.logger.warning("=" * 60)
                self.logger.warning(f"  Found {len(option_positions)} option legs confirmed as engine spread:")
                for key, pos in option_positions.items():
                    self.logger.warning(f"    - {pos.symbol} {pos.strike}{pos.right} x{pos.position}")
                self.logger.warning("")
                self.logger.warning("  Confirmation: Matched executed trade in signal CSV")
                self.logger.warning("")
                self.logger.warning("  Possible causes:")
                self.logger.warning("    - State file was deleted")
                self.logger.warning("    - Crash during entry (before state saved)")
                self.logger.warning("    - Engine restarted without state file")
                self.logger.warning("")
                self.logger.warning("  Attempting automatic recovery...")

                # Try to recover the orphan spread
                recovery_success = self._recover_orphan_spread(option_positions)

                if recovery_success:
                    self.logger.info("=" * 60)
                    self.logger.info("[RECOVERY SUCCESS] Orphan spread recovered!")
                    self.logger.info("=" * 60)
                    self.logger.info("  Position is now being monitored by engine")
                    self.logger.info("  Bracket orders have been placed")
                    return  # Exit - position is now managed
                else:
                    self.logger.error("=" * 60)
                    self.logger.error("[RECOVERY FAILED] Could not recover orphan spread")
                    self.logger.error("=" * 60)
                    self.logger.error("  IMMEDIATE ACTION REQUIRED:")
                    self.logger.error("    1. Check TWS for this position")
                    self.logger.error("    2. Manually place exit orders or close position")
                    self.logger.error("  Engine will IGNORE this position.")
                    self.logger.error("=" * 60)

            elif not csv_exists:
                # Signal CSV not found - CANNOT confirm if positions are from engine
                # This is a critical situation - both state file AND signal CSV are missing
                self.logger.error("=" * 60)
                self.logger.error("[CRITICAL] UNCONFIRMED POSITIONS - MANUAL ACTION REQUIRED")
                self.logger.error("=" * 60)
                self.logger.error(f"  Found {len(option_positions)} option legs in TWS:")
                for key, pos in option_positions.items():
                    self.logger.error(f"    - {pos.symbol} {pos.strike}{pos.right} x{pos.position}")
                self.logger.error("")
                self.logger.error("  PROBLEM: Cannot confirm if these are engine positions")
                self.logger.error(f"    - State file: MISSING")
                self.logger.error(f"    - Signal CSV: MISSING ({reason})")
                self.logger.error("")
                self.logger.error("  CANNOT AUTO-RECOVER - Both verification sources are missing!")
                self.logger.error("")
                self.logger.error("  IMMEDIATE ACTION REQUIRED:")
                self.logger.error("    1. CHECK TWS - Are these YOUR positions?")
                self.logger.error("    2. If YES (engine positions):")
                self.logger.error("       - Manually place exit orders (target + stop-loss)")
                self.logger.error("       - Or close the position manually")
                self.logger.error("    3. If NO (manual positions):")
                self.logger.error("       - Engine will ignore them, manage yourself")
                self.logger.error("")
                self.logger.error("  Engine will IGNORE these positions and continue.")
                self.logger.error("=" * 60)

            else:
                # Signal CSV exists but no match found - these are manual positions
                self.logger.warning("=" * 60)
                self.logger.warning("[MANUAL POSITIONS DETECTED - IGNORING]")
                self.logger.warning("=" * 60)
                self.logger.warning(f"  Found {len(option_positions)} option legs in TWS:")
                for key, pos in option_positions.items():
                    self.logger.warning(f"    - {pos.symbol} {pos.strike}{pos.right} x{pos.position}")
                self.logger.warning("")
                self.logger.warning("  Verification: Signal CSV checked - no matching executed trade")
                self.logger.warning("  These positions are NOT from this engine.")
                self.logger.warning("")
                self.logger.warning("  ENGINE BEHAVIOR:")
                self.logger.warning("    - Will IGNORE these manual positions completely")
                self.logger.warning("    - Will continue normal operation (entry/exit)")
                self.logger.warning("    - Will only monitor positions IT creates")
                self.logger.warning("")
                self.logger.warning("  Note: Close manual positions in TWS when ready.")
                self.logger.warning("=" * 60)

            # Mark as manual positions - engine will NOT manage these
            self._manual_positions_detected = True
            self._manual_positions_logged = True
            self._manual_position_symbols = set(f"{p.strike}{p.right}" for p in option_positions.values())

        except Exception as e:
            self.logger.warning(f"Position recovery failed: {e}")

    def _recover_from_saved_state(self, saved_state):
        """
        Recover position from saved state file (crash recovery).

        This method reconstructs the Position and BuiltStrategy from
        the saved state and resumes position monitoring.
        """
        from .state_persistence import SavedPosition
        from .trade_management.entry_management import BuiltStrategy, BuiltLeg
        from .broker import OptionContract
        from .enums import OptionType, OrderSide
        from datetime import datetime

        self.logger.info("Recovering position from saved state...")

        saved_pos = saved_state.position

        # Fetch actual IBKR positions to get contracts with valid conIds
        ibkr_positions = self.broker.request_positions() or {}
        ibkr_contract_map = {}  # (strike, right) -> IBKR contract
        ibkr_position_map = {}  # (strike, right) -> position quantity
        for key, pos_info in ibkr_positions.items():
            if pos_info.sec_type == "OPT" and pos_info.symbol == self.config.underlying_symbol:
                ibkr_contract_map[(pos_info.strike, pos_info.right)] = pos_info.contract
                ibkr_position_map[(pos_info.strike, pos_info.right)] = pos_info.position

        # ===== VALIDATION: Verify ALL legs from state file exist on IBKR =====
        self.logger.info("[VALIDATION] Verifying saved position legs exist on IBKR...")
        missing_legs = []
        mismatched_qty_legs = []

        for saved_leg in saved_pos.legs:
            leg_key = (saved_leg.strike, saved_leg.option_type)
            ibkr_qty = ibkr_position_map.get(leg_key, 0)

            # Determine expected quantity (negative for SELL, positive for BUY)
            expected_qty = saved_leg.quantity * saved_pos.contracts
            if saved_leg.side == "SELL":
                expected_qty = -expected_qty

            if leg_key not in ibkr_position_map:
                missing_legs.append(f"{saved_leg.strike} {saved_leg.option_type} (not found on IBKR)")
            elif ibkr_qty != expected_qty:
                mismatched_qty_legs.append(
                    f"{saved_leg.strike} {saved_leg.option_type} (IBKR: {ibkr_qty}, expected: {expected_qty})"
                )

        if missing_legs:
            # Position legs are missing from IBKR - position was closed manually
            self.logger.warning("=" * 60)
            self.logger.warning("[STATE MISMATCH] Position legs MISSING from IBKR!")
            self.logger.warning("=" * 60)
            self.logger.warning("  State file shows position, but IBKR has NO matching legs:")
            for leg_info in missing_legs:
                self.logger.warning(f"    - {leg_info}")
            self.logger.warning("")
            self.logger.warning("  LIKELY CAUSE: Position was closed manually in TWS")
            self.logger.warning("")
            self.logger.warning("  ACTION: Clearing stale state file")
            self.logger.warning("  Engine will be ready for new entries")
            self.logger.warning("=" * 60)

            # Clear the stale state file
            if self.state_manager:
                self.state_manager.clear_state()

            return  # Don't recover - position doesn't exist on IBKR

        if mismatched_qty_legs:
            # Quantity mismatch - partial close or different position
            self.logger.warning("=" * 60)
            self.logger.warning("[STATE MISMATCH] Position quantities don't match IBKR!")
            self.logger.warning("=" * 60)
            self.logger.warning("  Quantity mismatches:")
            for leg_info in mismatched_qty_legs:
                self.logger.warning(f"    - {leg_info}")
            self.logger.warning("")
            self.logger.warning("  LIKELY CAUSE: Partial close in TWS, or different position")
            self.logger.warning("")
            self.logger.warning("  ACTION: Clearing stale state file")
            self.logger.warning("  CHECK TWS - manually manage remaining position if any")
            self.logger.warning("=" * 60)

            # Clear the stale state file
            if self.state_manager:
                self.state_manager.clear_state()

            return  # Don't recover - mismatch detected

        self.logger.info("  ✓ All position legs verified on IBKR")

        # Build legs from saved state using IBKR contracts
        legs = []
        for saved_leg in saved_pos.legs:
            opt_type = OptionType.from_string(saved_leg.option_type)
            side = OrderSide.from_string(saved_leg.side)

            # Try to get actual IBKR contract with valid conId
            ibkr_contract = ibkr_contract_map.get((saved_leg.strike, saved_leg.option_type))
            if ibkr_contract is None:
                # Fallback: create minimal contract (may not work for market data)
                from ibapi.contract import Contract
                self.logger.warning(f"No IBKR contract found for {saved_leg.strike} {saved_leg.option_type} - using minimal contract")
                ibkr_contract = Contract()
                ibkr_contract.symbol = self.config.underlying_symbol
                ibkr_contract.secType = "OPT"
                ibkr_contract.exchange = "SMART"
                ibkr_contract.currency = "USD"
                ibkr_contract.strike = saved_leg.strike
                ibkr_contract.right = saved_leg.option_type
                ibkr_contract.lastTradeDateOrContractMonth = saved_leg.expiry
                ibkr_contract.tradingClass = getattr(self.config, 'underlying_trading_class', 'SPXW')
            else:
                # Ensure exchange is set for market data requests
                if not ibkr_contract.exchange:
                    ibkr_contract.exchange = "SMART"

            option_contract = OptionContract(
                symbol=self.config.underlying_symbol,
                strike=saved_leg.strike,
                expiry=saved_leg.expiry,
                option_type=opt_type,
                contract=ibkr_contract
            )

            leg = BuiltLeg(
                leg_index=saved_leg.leg_index,
                option_type=opt_type,
                side=side,
                target_delta=saved_leg.target_delta,
                actual_delta=saved_leg.actual_delta,
                strike=saved_leg.strike,
                expiry=saved_leg.expiry,
                quantity=saved_leg.quantity,
                contract=option_contract,
                entry_price=saved_leg.entry_price,
                current_price=saved_leg.entry_price
            )
            legs.append(leg)

        # Create BuiltStrategy
        built_strategy = BuiltStrategy(
            strategy_id=saved_pos.strategy_id,
            legs=legs,
            entry_spread=saved_pos.entry_spread,
            is_credit=saved_pos.is_credit,
            underlying_price=saved_pos.underlying_price_entry
        )

        # Parse entry time
        entry_time = datetime.fromisoformat(saved_pos.entry_time) if saved_pos.entry_time else datetime.now(US_EASTERN)

        # Create position directly in position manager
        # We bypass create_position to preserve original position_id
        from .data_classes import Position
        position = Position(
            position_id=saved_pos.position_id,
            strategy_id=saved_pos.strategy_id,
            built_strategy=built_strategy,
            contracts=saved_pos.contracts,
            entry_time=entry_time,
            entry_spread=saved_pos.entry_spread,
            target_spread=saved_pos.target_spread,
            stop_spread=saved_pos.stop_spread,
            is_credit=saved_pos.is_credit,
            direction_bias=saved_pos.direction_bias,
            vol_regime=saved_pos.vol_regime,
            trend_regime=saved_pos.trend_regime,
            underlying_price_entry=saved_pos.underlying_price_entry,
            underlying_price_current=self.broker.get_latest_price() or saved_pos.underlying_price_entry
        )

        # Set directly in position manager
        self.position_manager.current_position = position

        self.logger.info("=" * 60)
        self.logger.info("[POSITION RECOVERED FROM SAVED STATE]")
        self.logger.info("=" * 60)
        self.logger.info(f"  Position ID: {position.position_id}")
        self.logger.info(f"  Strategy: {position.strategy_id}")
        self.logger.info(f"  Legs: {len(legs)}")
        for leg in legs:
            self.logger.info(f"    - {leg.strike} {leg.option_type.value} {leg.side.value}")
        self.logger.info(f"  Entry Spread: ${position.entry_spread:.2f}")
        self.logger.info(f"  Target: ${position.target_spread:.2f}")
        self.logger.info(f"  Stop: ${position.stop_spread:.2f}")
        self.logger.info(f"  Contracts: {position.contracts}")
        self.logger.info("=" * 60)

        # Clear manual positions flag - we've successfully recovered from state
        # These positions are now tracked by the engine, not "manual"
        self._manual_positions_detected = False
        self._manual_positions_logged = False
        self._manual_position_symbols.clear()

        # Restore bracket order info if available and verify they exist on IBKR
        if saved_state.bracket_order_info:
            saved_target_id = saved_state.bracket_order_info.target_order_id
            saved_sl_id = saved_state.bracket_order_info.sl_order_id

            self.logger.info("  Verifying saved bracket orders exist on IBKR...")
            self.logger.info(f"    Saved Target order ID: {saved_target_id}")
            self.logger.info(f"    Saved SL order ID: {saved_sl_id}")

            # Verify orders still exist on IBKR
            verify_result = self.broker.verify_bracket_orders_exist(saved_target_id, saved_sl_id)

            if verify_result['any_filled']:
                # One of the exit orders already filled - position was closed by bracket order
                exit_type = verify_result['filled_order']
                self.logger.info("=" * 60)
                self.logger.info(f"[POSITION CLOSED] Exit order '{exit_type}' was FILLED while engine was down")
                self.logger.info("=" * 60)
                self.logger.info("  Position was closed automatically by bracket order system")
                self.logger.info("  Clearing state file - engine ready for new entries")
                self.state_manager.clear_state()
                # Do NOT set manual_positions_detected - engine should be ready for new trades
                return

            if verify_result['both_exist']:
                # Both orders still exist - restore the info
                self._bracket_order_info = {
                    'target_order_id': saved_target_id,
                    'sl_order_id': saved_sl_id,
                    'oca_group': saved_state.bracket_order_info.oca_group,
                    'target_price': saved_state.bracket_order_info.target_price,
                    'sl_trigger_price': saved_state.bracket_order_info.sl_trigger_price,
                }
                self.logger.info("  [OK] Both exit orders verified on IBKR")
                self.logger.info(f"    Target status: {verify_result['target_status']}")
                self.logger.info(f"    SL status: {verify_result['sl_status']}")
            else:
                # Orders missing - need to re-place them
                self.logger.warning("=" * 60)
                self.logger.warning("[RECOVERY] Exit orders MISSING from IBKR!")
                self.logger.warning("=" * 60)
                self.logger.warning(f"  Target exists: {verify_result['target_exists']} (status: {verify_result['target_status']})")
                self.logger.warning(f"  SL exists: {verify_result['sl_exists']} (status: {verify_result['sl_status']})")
                self.logger.warning("  Re-placing exit orders automatically...")

                # Build legs for exit order placement
                exit_legs = []
                for leg in legs:
                    leg_dict = {
                        'contract': leg.contract.contract,  # Get IBKR contract
                        'action': leg.side.value,  # Original entry action
                        'quantity': leg.quantity,
                    }
                    exit_legs.append(leg_dict)

                # Re-place exit orders
                exit_result = self.broker.place_exit_orders_only(
                    legs=exit_legs,
                    quantity=position.contracts,
                    entry_fill_price=position.entry_spread,
                    credit_target_pct=getattr(self.config, 'credit_target_factor', 0.30),
                    credit_sl_pct=getattr(self.config, 'credit_stop_factor', 0.30),
                    debit_target_pct=getattr(self.config, 'debit_target_factor', 1.50),
                    debit_sl_pct=getattr(self.config, 'debit_stop_factor', 0.50),
                    sl_limit_offset=self.config.sl_limit_offset,
                )

                if exit_result['success']:
                    self.logger.info("=" * 60)
                    self.logger.info("[RECOVERY] Exit orders RE-PLACED successfully!")
                    self.logger.info("=" * 60)
                    self._bracket_order_info = {
                        'target_order_id': exit_result['target_order_id'],
                        'sl_order_id': exit_result['sl_order_id'],
                        'oca_group': exit_result['oca_group'],
                        'target_price': exit_result['target_price'],
                        'sl_trigger_price': exit_result['sl_trigger_price'],
                    }
                    self.logger.info(f"  New Target order ID: {exit_result['target_order_id']}")
                    self.logger.info(f"  New SL order ID: {exit_result['sl_order_id']}")
                    self.logger.info(f"  New OCA Group: {exit_result['oca_group']}")

                    # Update state file with new bracket order info
                    self.state_manager.save_state(
                        position,
                        self.broker,
                        bracket_order_info=self._bracket_order_info
                    )
                    self.logger.info("  State file updated with new bracket order info")
                else:
                    self.logger.error("=" * 60)
                    self.logger.error("[RECOVERY] Failed to re-place exit orders!")
                    self.logger.error("=" * 60)
                    self.logger.error(f"  Error: {exit_result['error_message']}")
                    self.logger.error("  Position recovered but WITHOUT exit orders!")
                    self.logger.error("  Please manually place exit orders in TWS")
        else:
            # No bracket order info in saved state
            self.logger.warning("=" * 60)
            self.logger.warning("[RECOVERY] No bracket order info in saved state")
            self.logger.warning("=" * 60)

            # First, scan IBKR for existing exit orders (in case they were placed but not saved)
            self.logger.info("  Scanning IBKR for existing exit orders...")
            position_conids = [leg.contract.contract.conId for leg in legs]
            scan_result = self.broker.scan_for_existing_exit_orders(position_conids)

            if scan_result['found'] and scan_result['target_order_id'] and scan_result['sl_order_id']:
                # Found existing exit orders - adopt them
                self.logger.info("=" * 60)
                self.logger.info("[RECOVERY] Found EXISTING exit orders on IBKR!")
                self.logger.info("=" * 60)
                self._bracket_order_info = {
                    'target_order_id': scan_result['target_order_id'],
                    'sl_order_id': scan_result['sl_order_id'],
                    'oca_group': scan_result['oca_group'],
                    'target_price': None,  # Unknown from scan
                    'sl_trigger_price': None,  # Unknown from scan
                }
                self.logger.info(f"  Adopted Target order ID: {scan_result['target_order_id']}")
                self.logger.info(f"  Adopted SL order ID: {scan_result['sl_order_id']}")
                self.logger.info(f"  OCA Group: {scan_result['oca_group']}")

                # Update state file with found bracket order info
                self.state_manager.save_state(
                    position,
                    self.broker,
                    bracket_order_info=self._bracket_order_info
                )
                self.logger.info("  State file updated with found bracket order info")
            else:
                # No existing exit orders found - place new ones
                self.logger.warning("  No existing exit orders found on IBKR")
                self.logger.warning("  Placing new exit orders...")

                # Build legs for exit order placement
                exit_legs = []
                for leg in legs:
                    leg_dict = {
                        'contract': leg.contract.contract,  # Get IBKR contract
                        'action': leg.side.value,  # Original entry action
                        'quantity': leg.quantity,
                    }
                    exit_legs.append(leg_dict)

                # Place exit orders
                exit_result = self.broker.place_exit_orders_only(
                    legs=exit_legs,
                    quantity=position.contracts,
                    entry_fill_price=position.entry_spread,
                    credit_target_pct=getattr(self.config, 'credit_target_factor', 0.30),
                    credit_sl_pct=getattr(self.config, 'credit_stop_factor', 0.30),
                    debit_target_pct=getattr(self.config, 'debit_target_factor', 1.50),
                    debit_sl_pct=getattr(self.config, 'debit_stop_factor', 0.50),
                    sl_limit_offset=self.config.sl_limit_offset,
                )

                if exit_result['success']:
                    self.logger.info("=" * 60)
                    self.logger.info("[RECOVERY] Exit orders PLACED successfully!")
                    self.logger.info("=" * 60)
                    self._bracket_order_info = {
                        'target_order_id': exit_result['target_order_id'],
                        'sl_order_id': exit_result['sl_order_id'],
                        'oca_group': exit_result['oca_group'],
                        'target_price': exit_result['target_price'],
                        'sl_trigger_price': exit_result['sl_trigger_price'],
                    }
                    self.logger.info(f"  Target order ID: {exit_result['target_order_id']}")
                    self.logger.info(f"  SL order ID: {exit_result['sl_order_id']}")
                    self.logger.info(f"  OCA Group: {exit_result['oca_group']}")

                    # Update state file with bracket order info
                    self.state_manager.save_state(
                        position,
                        self.broker,
                        bracket_order_info=self._bracket_order_info
                    )
                    self.logger.info("  State file updated with bracket order info")
                else:
                    self.logger.error("=" * 60)
                    self.logger.error("[RECOVERY] Failed to place exit orders!")
                    self.logger.error("=" * 60)
                    self.logger.error(f"  Error: {exit_result['error_message']}")
                    self.logger.error("  Position recovered but WITHOUT exit orders!")
                    self.logger.error("  Please manually place exit orders in TWS")

        # Start position monitoring
        self.position_monitor.start()
        self._safe_state_transition(EngineState.POSITION_OPEN, "Position recovered from saved state")
        self.logger.info("Position monitoring resumed from crash recovery")

    def _start_market_data(self) -> bool:
        """Start market data subscription with retry logic.

        Returns:
            True if successful, False if failed (triggers shutdown)
        """
        contract = self.broker.create_underlying_contract()

        # Subscribe to real-time ticks
        self.broker.subscribe_market_data(contract)

        # Request historical data with retries - CRITICAL for ORB calculation
        if not self._fetch_historical_data_with_retry(contract, max_retries=3):
            # Historical data is CRITICAL - cannot calculate ORB without it
            self.logger.error("=" * 60)
            self.logger.error("[HISTORICAL DATA FETCH FAILED] ENGINE SHUTTING DOWN")
            self.logger.error("=" * 60)
            self.logger.error("  Historical bar data is REQUIRED for ORB calculation")
            self.logger.error("  Without this data, the strategy cannot function properly")
            self.logger.error("")
            self.logger.error("  Possible causes:")
            self.logger.error("    1. Market is closed (no recent data available)")
            self.logger.error("    2. IBKR data subscription issue (check TWS/Gateway)")
            self.logger.error("    3. Network connectivity problem")
            self.logger.error("    4. API rate limiting (too many requests)")
            self.logger.error("")
            self.logger.error("  Troubleshooting steps:")
            self.logger.error("    1. Ensure TWS/Gateway is running and logged in")
            self.logger.error("    2. Check that market data subscriptions are active")
            self.logger.error("    3. Verify SPX index data is available in TWS")
            self.logger.error("    4. Try restarting TWS/Gateway")
            self.logger.error("    5. Start engine during US market hours (9:30 AM - 4:00 PM ET)")
            self.logger.error("=" * 60)
            self._safe_state_transition(EngineState.SHUTDOWN, "Historical data fetch failed - cannot operate without market data")
            return False

        return True

    def _fetch_historical_data_with_retry(self, contract, max_retries: int = 3) -> bool:
        """Fetch historical data with retry logic.

        Returns:
            True if data was fetched successfully, False if all attempts failed
        """
        current_time_et = datetime.now(US_EASTERN).strftime("%H:%M:%S ET")

        for attempt in range(max_retries):
            self.logger.debug(f"Fetching historical data (attempt {attempt + 1}/{max_retries})...")
            self.logger.debug(f"  Symbol: {contract.symbol}")
            self.logger.debug(f"  Duration: 2 D, Bar Size: {self.config.bar_interval_minutes} mins")
            self.logger.debug(f"  Current time: {current_time_et}")

            try:
                bars = self.broker.request_historical_data(
                    contract,
                    duration="2 D",
                    bar_size=f"{self.config.bar_interval_minutes} mins",
                    keep_up_to_date=True,
                    callback=self._on_bar_update,
                    timeout=30.0
                )

                if bars:
                    self._market_data_adapter.load_bars(bars)
                    self.logger.debug(f"Loaded {len(bars)} historical bars")

                    # Log sample of fetched data (with CT→ET timezone conversion)
                    self.logger.log_price_bars_fetched(
                        bars=bars,
                        symbol=self.config.underlying_symbol,
                        show_count=5,
                        timezone_offset_hours=self.config.ibkr_bar_timezone_offset_hours
                    )
                    return True
                else:
                    self.logger.error(f"Historical data fetch attempt {attempt + 1}/{max_retries} FAILED")
                    self.logger.error(f"  IBKR returned 0 bars for {contract.symbol}")
                    self.logger.error(f"  This usually means market is closed or data subscription issue")
                    if attempt < max_retries - 1:
                        self.logger.info(f"  Waiting 5 seconds before retry ({max_retries - attempt - 1} attempts remaining)...")
                        time.sleep(5)

            except Exception as e:
                self.logger.error(f"Historical data fetch attempt {attempt + 1}/{max_retries} EXCEPTION: {e}")
                if attempt < max_retries - 1:
                    self.logger.info(f"  Waiting 5 seconds before retry ({max_retries - attempt - 1} attempts remaining)...")
                    time.sleep(5)

        self.logger.error(f"All {max_retries} historical data fetch attempts FAILED")
        return False

    def _refetch_market_data(self, force: bool = False) -> bool:
        """
        Refetch market data during session if needed.

        Args:
            force: If True, always refetch regardless of existing data

        Returns:
            True if data was fetched successfully
        """
        if not force and self._market_data_adapter and len(self._market_data_adapter.get_bars()) > 0:
            # Check if we have recent bars (within last 10 minutes)
            bars = self._market_data_adapter.get_bars()
            if bars:
                latest_bar_time = bars[-1].timestamp
                # Remove timezone info for comparison to avoid naive vs aware datetime issues
                if latest_bar_time.tzinfo is not None:
                    latest_bar_time = latest_bar_time.replace(tzinfo=None)
                # Use ET time since IBKR bar timestamps are in ET
                now_et = self._get_current_et_time()
                time_since_last_bar = now_et - latest_bar_time
                # If latest bar is older than 10 minutes, we need to refetch
                if time_since_last_bar.total_seconds() < 600:
                    return True  # Have recent data

        self.logger.info("Attempting to refetch market data...")
        contract = self.broker.create_underlying_contract()
        return self._fetch_historical_data_with_retry(contract, max_retries=2)

    def _on_bar_update(self, bar: OHLCBar):
        """Callback for new bar from IBKRWrapper"""
        self._market_data_adapter.add_bar(bar)

    def _on_new_bar(self, bar: OHLCBar):
        """Callback for new bar from market data adapter"""
        if not self._running:
            return

        # Check circuit breaker (Issue 13)
        if not self._check_circuit_breaker():
            return

        # Check connection health (Issue 10)
        if not self._check_connection_health():
            self._record_error("Connection unhealthy")
            return

        # Update signal generator
        signal = self.signal_generator.update(bar)

        if signal and signal.signal_ok:
            # Log signal regardless of execution mode
            self.logger.info(f"Signal generated: {signal.direction_bias.value if signal.direction_bias else 'N/A'}")

            # Issue 1, 2: Check signal-only mode
            if self._is_signal_only_mode():
                self.logger.info("[SIGNAL-ONLY MODE] Signal recorded but NOT executing trade")
                self._log_signal_only(signal)
                return

            # Normal execution path (manual positions are ignored)
            if not self.position_manager.has_open_position():
                # Issue 5: Cancel unfilled orders before new signal
                self._cancel_unfilled_orders()
                self._execute_signal(signal)

    def _log_signal_only(self, signal: 'Signal'):
        """Log signal details in signal-only mode without execution."""
        self.logger.info("=" * 50)
        self.logger.info("[SIGNAL-ONLY MODE - NO EXECUTION]")
        self.logger.info(f"   Direction: {signal.direction_bias.value if signal.direction_bias else 'N/A'}")
        self.logger.info(f"   Vol Regime: {signal.vol_regime.value if signal.vol_regime else 'N/A'}")
        self.logger.info(f"   Trend Regime: {signal.trend_regime.value if signal.trend_regime else 'N/A'}")
        self.logger.info(f"   Underlying Price: ${signal.underlying_price:.2f}")
        self.logger.info(f"   OR High: ${signal.or_high:.2f}" if signal.or_high else "   OR High: N/A")
        self.logger.info(f"   OR Low: ${signal.or_low:.2f}" if signal.or_low else "   OR Low: N/A")
        self.logger.info(f"   Reason: {signal.reason}")
        self.logger.info("=" * 50)

        # Still log to signal CSV for analysis
        self.logger.log_signal({
            "underlying_price": signal.underlying_price,
            "or_high": signal.or_high,
            "or_low": signal.or_low,
            "direction_bias": signal.direction_bias.value if signal.direction_bias else None,
            "trend_regime": signal.trend_regime.value if signal.trend_regime else None,
            "vol_regime": signal.vol_regime.value if signal.vol_regime else None,
            "iv_atm": signal.iv_atm,
            "ivp": signal.ivp,
            "signal_ok": True,
            "strategy_selected": "SIGNAL_ONLY_MODE",
            "reason": "Signal-only mode - no execution"
        })

    def run(self):
        """Main engine run loop"""
        self._running = True

        # Check if US options market is open before starting
        # This prevents starting the engine during non-market hours
        if not is_us_options_market_open():
            current_time = datetime.now(US_EASTERN).strftime("%H:%M:%S ET")
            self.logger.error("=" * 60)
            self.logger.error("[MARKET CLOSED] Cannot start engine - US options market is closed")
            self.logger.error(f"  Current time: {current_time}")
            self.logger.error("  US Options Market Hours: 9:30 AM - 4:00 PM ET (Mon-Fri)")
            self.logger.error("  Please start the engine during market hours")
            self.logger.error("=" * 60)
            self._safe_state_transition(EngineState.SHUTDOWN, "Market closed - cannot start engine")
            return

        try:
            self._wait_for_session_start()

            if not self._running:
                return

            self._trading_loop()

        except clientError as e:
            self.logger.error(f"Engine error [{e.code}]: {e.message}")
            self._safe_state_transition(EngineState.ERROR, f"Engine error [{e.code}]: {e.message}")
            raise
        except Exception as e:
            self.logger.error(f"Engine error: {e}")
            self._safe_state_transition(EngineState.ERROR, f"Engine error: {e}")
            raise
        finally:
            self._cleanup()

    def _wait_for_session_start(self):
        """Wait until session start time"""
        # If we recovered a position, skip waiting and keep POSITION_OPEN state
        if self.state == EngineState.POSITION_OPEN:
            self.logger.info("Position recovered - skipping session wait, going directly to monitoring")
            return

        self._safe_state_transition(EngineState.WAITING_FOR_SESSION, "Waiting for market session")

        # Convert IST config times to ET for comparison with current ET time
        session_start_et = TimezoneManager.convert_ist_to_eastern(self.config.session_start_time)
        session_end_et = TimezoneManager.convert_ist_to_eastern(self.config.session_end_time)

        self.logger.debug(f"[SESSION TIMES] Config IST -> Converted to US Eastern:")
        self.logger.debug(f"   Session Start: {self.config.session_start_time} IST -> {session_start_et} ET")
        self.logger.debug(f"   Session End: {self.config.session_end_time} IST -> {session_end_et} ET")

        last_log_minute = -1

        while self._running:
            current_time = datetime.now(US_EASTERN).time()
            current_minute = datetime.now(US_EASTERN).minute

            if is_time_in_session(
                current_time,
                session_start_et,
                session_end_et
            ):
                self.logger.log_session_start()
                self.logger.info(f"   Session Window: {session_start_et} - {session_end_et} ET")
                self.logger.info(f"   ORB Collection: {self.orb.orb_start if self.orb else 'N/A'} - {self.orb.orb_end if self.orb else 'N/A'} ET")
                self.logger.info(f"   Breakout Window: {self.orb.breakout_start if self.orb else 'N/A'} - {self.orb.breakout_end if self.orb else 'N/A'} ET")
                self.logger.info(f"   Time Cutoff: {TimezoneManager.convert_ist_to_eastern(self.config.cutoff_time)} ET")
                break

            if current_minute != last_log_minute:
                self.logger.log_waiting_status(
                    str(session_start_et),
                    current_time.strftime("%H:%M:%S"),
                    "Market session not yet started"
                )
                last_log_minute = current_minute

            time.sleep(1)

            if self._shutdown_event.is_set():
                return

    def _trading_loop(self):
        """Main trading loop with circuit breaker and connection health checks."""
        # If we recovered a position, skip ORB collection and go straight to monitoring
        if self.state == EngineState.POSITION_OPEN:
            self.logger.info("Recovered position detected - skipping ORB collection, monitoring position")
        else:
            self._safe_state_transition(EngineState.COLLECTING_ORB, "Starting ORB data collection")

        self._cycle_count = 0
        self._orb_failure_logged = False

        # Convert IST config times to ET for comparison with current ET time
        session_start_et = TimezoneManager.convert_ist_to_eastern(self.config.session_start_time)
        session_end_et = TimezoneManager.convert_ist_to_eastern(self.config.session_end_time)

        # Detect manual positions at start (Issue 3)
        self._detect_manual_positions()

        while self._running:
            try:
                # Check circuit breaker before processing (Issue 13)
                if not self._check_circuit_breaker():
                    self.logger.warning("Circuit breaker tripped - pausing execution")
                    time.sleep(10)
                    continue

                # Check connection health (Issue 10, 11)
                if not self._check_connection_health():
                    self._record_error("Connection lost")
                    time.sleep(5)
                    continue

                current_time = datetime.now(US_EASTERN).time()
                self._cycle_count += 1

                # Issue 3: Periodically re-check for manual positions (every 30 cycles ~ 1 min)
                # Issue 5, 6: Also reconcile orders/positions to detect stale fills
                if self._cycle_count % 30 == 0:
                    self._detect_manual_positions()
                    self._reconcile_orders_and_positions()
                    # Check for config file changes
                    self._check_config_changes()

                if is_session_ended(
                    current_time,
                    session_start_et,
                    session_end_et
                ):
                    self._safe_state_transition(EngineState.SESSION_ENDED, "Session end time reached")
                    self.logger.log_session_end()
                    # End-of-day reconciliation
                    self._handle_end_of_day_reconciliation()
                    break

                # Immediate shutdown check (Issue 12)
                if self._shutdown_event.wait(timeout=0.1):
                    self.logger.info("Shutdown event received - stopping immediately")
                    break

                # Quick check for shutdown flag (Issue 12)
                if not self._running:
                    break

                self._process_state()

                # Record success for circuit breaker
                self._record_success()

                # Short sleep for responsiveness (Issue 12)
                time.sleep(0.9)

            except StrategyBuildError as e:
                # Strategy build failures (e.g., can't get option prices) are TRANSIENT
                # Don't count towards circuit breaker - just log and retry
                self.logger.log_error_detailed(
                    f"TRADING_LOOP_ERROR_{e.code}",
                    e.message,
                    {"state": self.state.name, "cycle": self._cycle_count, "context": e.context}
                )
                self.logger.info("Strategy build failed (transient) - will retry on next cycle")
                time.sleep(5)  # Wait before retry
            except clientError as e:
                self._record_error(e.message)
                self.logger.log_error_detailed(
                    f"TRADING_LOOP_ERROR_{e.code}",
                    e.message,
                    {"state": self.state.name, "cycle": self._cycle_count, "context": e.context}
                )
                # Check if we should continue after error
                if not self._check_circuit_breaker():
                    self.logger.error("Too many errors - stopping trading loop")
                    break
                time.sleep(5)
            except Exception as e:
                self._record_error(str(e))
                self.logger.log_error_detailed(
                    "TRADING_LOOP_ERROR",
                    str(e),
                    {"state": self.state.name, "cycle": self._cycle_count}
                )
                # Check if we should continue after error
                if not self._check_circuit_breaker():
                    self.logger.error("Too many errors - stopping trading loop")
                    break
                time.sleep(5)

    def _process_state(self):
        """Process current engine state"""
        # Issue 3: When manual positions are detected, continue normal signal generation
        # but block execution. This allows user to see what signals WOULD be generated.
        # Execution blocking happens in _can_place_order() and _execute_signal()

        # Note: We no longer skip to MONITORING_SIGNALS when manual positions detected.
        # Instead, we continue through normal states but block actual order placement.

        if self.state == EngineState.COLLECTING_ORB:
            self._handle_orb_collection()
        elif self.state == EngineState.WAITING_FOR_BREAKOUT:
            self._handle_waiting_for_breakout()
        elif self.state == EngineState.MONITORING_SIGNALS:
            self._handle_signal_monitoring()
        elif self.state == EngineState.IDLE_NO_TRADE:
            self._handle_idle_no_trade()
        elif self.state == EngineState.POSITION_OPEN:
            self._handle_position_monitoring()

    def _handle_orb_collection(self):
        """Handle ORB collection phase"""
        current_time = datetime.now(US_EASTERN).time()
        orb_end = self.orb.orb_end

        # Try to refetch market data every 30 seconds if we have no bars or stale bars
        if self._cycle_count % 30 == 0:
            bars = self._market_data_adapter.get_bars() if self._market_data_adapter else []

            # Check if bars are stale (no recent bars within last 10 minutes)
            # NOTE: IBKR may return bars in different timezone - use configurable offset to convert to ET
            bars_are_stale = False
            if bars:
                latest_bar_time = bars[-1].timestamp
                # Remove timezone info for comparison to avoid naive vs aware datetime issues
                if latest_bar_time.tzinfo is not None:
                    latest_bar_time = latest_bar_time.replace(tzinfo=None)
                # Use configurable offset to convert bar timestamp to ET
                offset_hours = self.config.ibkr_bar_timezone_offset_hours
                latest_bar_time_et = latest_bar_time + timedelta(hours=offset_hours)
                now_et = self._get_current_et_time()
                # Make now_et naive as well for consistent comparison
                if now_et.tzinfo is not None:
                    now_et = now_et.replace(tzinfo=None)
                time_since_last_bar = now_et - latest_bar_time_et
                bars_are_stale = time_since_last_bar.total_seconds() > 600
                if bars_are_stale:
                    self.logger.warning(f"Market data appears stale (last bar: {latest_bar_time}, offset={offset_hours}h), attempting refetch...")

            if not bars or bars_are_stale:
                self.logger.info("No recent market data available, attempting to fetch...")
                self._refetch_market_data(force=True)
                # Reload bars after refetch
                bars = self._market_data_adapter.get_bars() if self._market_data_adapter else []

            price = self.broker.get_latest_price()
            if price:
                now = datetime.now(US_EASTERN)
                # Create timezone-aware orb_end_dt for proper comparison with now
                orb_end_dt = datetime.combine(now.date(), orb_end).replace(tzinfo=US_EASTERN)
                time_remaining = str(orb_end_dt - now).split('.')[0] if orb_end_dt > now else "0:00:00"

                # Log bar count for visibility
                bar_count = len(bars) if bars else 0
                self.logger.log_orb_status(
                    phase="COLLECTING",
                    current_price=price,
                    time_remaining=time_remaining,
                    bar_count=bar_count
                )

        if current_time >= orb_end:
            if self.orb.calculate_opening_range():
                or_high, or_low = self.orb.get_or_range()
                price = self.broker.get_latest_price()

                if self._safe_state_transition(EngineState.WAITING_FOR_BREAKOUT, "ORB calculation complete"):
                    self.logger.log_orb_status(
                        phase="COMPLETE - WAITING FOR BREAKOUT",
                        or_high=or_high,
                        or_low=or_low,
                        current_price=price
                    )
                    self._orb_waiting_status_logged = True
            else:
                # Try to refetch data one more time before giving up
                if not self._orb_failure_logged:
                    self.logger.warning("ORB calculation failed - attempting final data fetch...")
                    # Force refetch to get the latest bars
                    if self._refetch_market_data(force=True):
                        # Try ORB calculation again after refetch
                        if self.orb.calculate_opening_range():
                            or_high, or_low = self.orb.get_or_range()
                            price = self.broker.get_latest_price()
                            if self._safe_state_transition(EngineState.WAITING_FOR_BREAKOUT, "ORB calculation complete after refetch"):
                                self.logger.log_orb_status(
                                    phase="COMPLETE - WAITING FOR BREAKOUT",
                                    or_high=or_high,
                                    or_low=or_low,
                                    current_price=price
                                )
                                self._orb_waiting_status_logged = True
                            return

                    # ORB calculation failed - log error and SHUTDOWN
                    # NO FALLBACK - we require proper ORB data for accurate trading decisions
                    # Without valid ORB, the entire strategy is compromised - shutdown gracefully
                    bars_count = len(self._market_data_adapter.get_bars()) if self._market_data_adapter else 0
                    todays_bars = len(self._market_data_adapter.get_todays_bars()) if self._market_data_adapter else 0

                    self.logger.error("=" * 60)
                    self.logger.error("[ORB CALCULATION FAILED] Unable to calculate Opening Range")
                    self.logger.error("=" * 60)
                    self.logger.error(f"  Total bars in buffer: {bars_count}")
                    self.logger.error(f"  Today's bars: {todays_bars}")
                    orb_end = self.orb.orb_end if self.orb else "N/A"
                    self.logger.error(f"  ORB window: {self.config.orb_start_time} - {orb_end} (IST)")
                    self.logger.error("  Possible causes:")
                    self.logger.error("    - Market data not received during ORB collection window")
                    self.logger.error("    - Timezone mismatch between config and bar timestamps")
                    self.logger.error("    - IBKR connection issues during ORB window")
                    self.logger.error("  Action: ENGINE SHUTTING DOWN - strategy cannot run without valid ORB")
                    self.logger.error("=" * 60)

                    self._orb_failure_logged = True
                    self._safe_state_transition(EngineState.SHUTDOWN, "ORB calculation failed - shutting down")

    def _handle_waiting_for_breakout(self):
        """Handle waiting for breakout phase"""
        current_time = datetime.now(US_EASTERN).time()

        # Check for breakout on every cycle
        price = self.broker.get_latest_price()

        # Create a synthetic bar from current real-time price for breakout check
        # We use the real-time tick price, not the historical 5-min bar close,
        # because breakout should be detected immediately when price breaches the ORB
        current_bar = OHLCBar(
            timestamp=datetime.now(US_EASTERN),
            open=price,
            high=price,
            low=price,
            close=price,
            volume=0
        )

        breakout_direction = self.orb.check_breakout(current_bar)

        if breakout_direction is not None:
            # Breakout detected! Generate and execute signal
            self.logger.debug("Breakout confirmed - generating trading signal...")

            # Generate the signal using force_signal_check with current price
            signal = self.signal_generator.force_signal_check(price)

            if signal and signal.signal_ok:
                # In signal-only mode, log signal but don't change state to POSITION_OPEN
                if self._is_signal_only_mode():
                    self.logger.debug("[SIGNAL-ONLY] Breakout signal generated - logging only, no execution")
                    self._execute_signal(signal)  # Will log and return early
                    # Stay in MONITORING_SIGNALS state instead of POSITION_OPEN
                    self._safe_state_transition(EngineState.MONITORING_SIGNALS, "Signal-only mode - continuing to monitor")
                else:
                    # Execute signal - state will transition to POSITION_OPEN inside _execute_signal() on success
                    self.logger.debug("Breakout signal generated - executing trade...")
                    self._execute_signal(signal)
            else:
                # Signal not valid (e.g., trend not aligned)
                reason = signal.reason if signal else "Signal generation failed"
                self.logger.log_no_trade_reason(
                    reason,
                    direction=breakout_direction.value,
                    vol_regime=None,
                    trend_regime=None
                )
                self._safe_state_transition(EngineState.MONITORING_SIGNALS, f"Breakout detected but signal invalid: {reason}")
            return

        # Log status only once at start (not every minute)
        if not self._orb_waiting_status_logged:
            or_high, or_low = self.orb.get_or_range()

            # Check if we have an open position - show different status
            if self.position_manager.has_open_position():
                time_remaining = "MONITORING POSITION"
            else:
                now = datetime.now(US_EASTERN)
                # Convert IST config times to ET for proper comparison
                breakout_end_et = TimezoneManager.convert_ist_to_eastern(self.config.orb_breakout_end_time)
                breakout_start_et = TimezoneManager.convert_ist_to_eastern(self.config.orb_breakout_start_time)

                # Handle midnight-spanning sessions (in ET)
                if breakout_end_et < breakout_start_et:
                    # Overnight session: end is next day
                    if current_time >= breakout_start_et:
                        # Before midnight - end is tomorrow
                        breakout_end_dt = datetime.combine(now.date() + timedelta(days=1), breakout_end_et).replace(tzinfo=US_EASTERN)
                    else:
                        # After midnight - end is today
                        breakout_end_dt = datetime.combine(now.date(), breakout_end_et).replace(tzinfo=US_EASTERN)
                else:
                    breakout_end_dt = datetime.combine(now.date(), breakout_end_et).replace(tzinfo=US_EASTERN)

                time_remaining = str(breakout_end_dt - now).split('.')[0] if breakout_end_dt > now else "WINDOW CLOSED"

            self.logger.log_orb_status(
                phase="WAITING FOR BREAKOUT",
                or_high=or_high,
                or_low=or_low,
                current_price=price,
                time_remaining=time_remaining
            )
            self._orb_waiting_status_logged = True

        # Check if breakout window has expired (handle overnight sessions)
        # IMPORTANT: Config times are in IST, current_time is in ET - must convert!
        breakout_start_et = TimezoneManager.convert_ist_to_eastern(self.config.orb_breakout_start_time)
        breakout_end_et = TimezoneManager.convert_ist_to_eastern(self.config.orb_breakout_end_time)
        spans_midnight = breakout_end_et < breakout_start_et

        if spans_midnight:
            # Overnight: window is active if time >= start OR time <= end
            in_window = current_time >= breakout_start_et or current_time <= breakout_end_et
        else:
            # Normal: window is active if start <= time <= end
            in_window = breakout_start_et <= current_time <= breakout_end_et

        if not in_window:
            if self._safe_state_transition(EngineState.IDLE_NO_TRADE, "Breakout window expired without breakout"):
                # Log SESSION SUMMARY for trader clarity
                or_high, or_low = self.orb.get_or_range() if self.orb else (None, None)
                session_end_et = TimezoneManager.convert_ist_to_eastern(self.config.session_end_time)
                self.logger.info("=" * 50)
                self.logger.info("[SESSION SUMMARY]")
                if or_high and or_low:
                    self.logger.info(f"   ORB Range: {or_low:.2f} - {or_high:.2f}")
                self.logger.info("   Breakout: NONE (price stayed within range)")
                self.logger.info("   Direction: NEUTRAL")
                self.logger.info("   Action: NO TRADE TODAY")
                self.logger.info(f"   Status: Engine idle until {session_end_et} ET")
                self.logger.info("=" * 50)

    def _handle_idle_no_trade(self):
        """Handle idle state when no trade for today - just wait for session end."""
        pass  # Nothing to do, just waiting for session end

    def _handle_signal_monitoring(self):
        """Handle signal monitoring"""
        if self.signal_generator.is_signal_generated():
            signal = self.signal_generator.get_last_signal()
            if signal and signal.signal_ok:
                self._execute_signal(signal)

    def _handle_position_monitoring(self):
        """Handle position monitoring - check for exits and log status."""
        # NOTE: Manual positions in TWS are IGNORED - they don't affect engine operation.
        # The engine only monitors positions it created (tracked via position_manager).
        # The _manual_positions_detected flag is informational only.

        # In signal-only mode, we should never be in POSITION_OPEN state
        # This is a safety check in case we somehow got here
        if self._is_signal_only_mode():
            self.logger.warning("[SIGNAL-ONLY] Unexpected POSITION_OPEN state - transitioning to MONITORING_SIGNALS")
            self._safe_state_transition(EngineState.MONITORING_SIGNALS, "Signal-only mode - no position monitoring")
            return

        # Check for market halt (every 10 cycles to avoid spam)
        if not hasattr(self, '_halt_check_counter'):
            self._halt_check_counter = 0
            self._halt_warned = False
        self._halt_check_counter += 1

        if self._halt_check_counter >= 10:
            self._halt_check_counter = 0
            halt_status = self.broker.check_market_halt()
            if halt_status['is_halted']:
                if not self._halt_warned:
                    self.logger.warning("=" * 60)
                    self.logger.warning("⚠️ MARKET HALT DETECTED")
                    self.logger.warning("=" * 60)
                    self.logger.warning(f"  Reason: {halt_status['reason']}")
                    self.logger.warning(f"  Bid: {halt_status['bid']}, Ask: {halt_status['ask']}")
                    self.logger.warning("  Bracket orders remain active - IBKR will execute when market resumes")
                    self.logger.warning("=" * 60)
                    self._halt_warned = True
            else:
                if self._halt_warned:
                    self.logger.info("Market data resumed - halt condition cleared")
                    self._halt_warned = False

        # When using bracket orders, IBKR handles exits via SL/Target orders
        # Software monitoring is not needed - just check if exit orders are still active
        if getattr(self.config, 'use_bracket_orders', False) and self._bracket_order_info:
            self._handle_bracket_order_monitoring()
            return

        if not self.position_manager.has_open_position():
            # Position closed - ORB strategy allows only one trade per session
            self.logger.info("")
            self.logger.info("=" * 70)
            self.logger.info("***   ORB TRADE COMPLETED - ENGINE SHUTTING DOWN   ***")
            self.logger.info("=" * 70)
            self.logger.info("Position closed externally")
            self.logger.info("Strategy rule: One trade per ORB session")
            self.logger.info("=" * 70)
            self._safe_state_transition(EngineState.SHUTDOWN, "Position closed - ORB trade completed")
            self._running = False
            return

        # Check time remaining until market close and show warnings
        self._check_time_to_close_warnings()

        # Check for exit conditions
        exit_reason = self.position_monitor.check_position()

        # Log P&L status after each check
        self.position_monitor.log_position_status()

        if exit_reason:
            position = self.position_manager.current_position
            self._on_exit_triggered(position, exit_reason)
            return

        # Log P&L periodically (every 6 cycles ~30 seconds at 5s/cycle)
        if self._cycle_count % 6 == 0:
            position = self.position_manager.current_position
            if position:
                current_spread = position.current_spread or position.entry_spread
                self.logger.info(f"[P&L] Entry: {position.entry_spread:.2f} | Current: {current_spread:.2f} | Unrealized: ${position.unrealized_pnl:.2f}")

    def _handle_bracket_order_monitoring(self):
        """
        Monitor bracket orders - IBKR handles exits via SL/Target orders.
        Uses broker's check_bracket_order_status for accurate fill tracking.
        """
        if not self._bracket_order_info:
            return

        target_id = self._bracket_order_info.get('target_order_id')
        sl_id = self._bracket_order_info.get('sl_order_id')
        oca_group = self._bracket_order_info.get('oca_group')
        entry_price = self._bracket_order_info.get('fill_price', 0)

        # Use broker method to check bracket order status
        status = self.broker.check_bracket_order_status(target_id, sl_id, oca_group)

        # Log bracket order status ONCE when position opens
        if not self._bracket_order_info.get('_status_logged', False):
            position = self.position_manager.current_position
            target_price = self._bracket_order_info.get('target_price', 0)
            sl_price = self._bracket_order_info.get('sl_trigger_price', 0)

            self.logger.info("=" * 50)
            self.logger.info("[EXIT ORDERS] Reversed combo on IBKR")
            self.logger.info("=" * 50)

            # Show reversed legs
            if position and position.built_strategy:
                for leg in position.built_strategy.legs:
                    # Reverse the side for exit
                    exit_side = "BUY" if leg.side.value == "SELL" else "SELL"
                    self.logger.info(f"   {exit_side:4} {leg.option_type.value} {leg.strike:.0f}")

            self.logger.info("   " + "-" * 30)
            self.logger.info(f"   Target: BUY @ ${abs(target_price):.2f}")
            self.logger.info(f"   Stop:   BUY @ ${abs(sl_price):.2f} trigger")
            self.logger.info(f"   OCA Group: {oca_group}")
            self.logger.info("=" * 50)
            self._bracket_order_info['_status_logged'] = True

        # Check time remaining until market close and show warnings
        self._check_time_to_close_warnings()

        # Check for forced exit at cutoff_time (even with bracket orders active)
        if self.config.exit_on_time and self.config.cutoff_time:
            current_time = datetime.now(US_EASTERN).time()
            cutoff_et = TimezoneManager.convert_ist_to_eastern(self.config.cutoff_time)

            if current_time >= cutoff_et:
                # Time cutoff reached - cancel bracket orders and force MARKET exit
                self.logger.error("=" * 60)
                self.logger.error("⏰ CUTOFF TIME REACHED - FORCING MARKET EXIT")
                self.logger.error("=" * 60)
                self.logger.error(f"  Current time: {current_time.strftime('%H:%M:%S')} ET")
                self.logger.error(f"  Cutoff time: {cutoff_et.strftime('%H:%M:%S')} ET")
                self.logger.error(f"  Action: Cancelling bracket orders, placing MARKET exit")
                self.logger.error("=" * 60)

                # Cancel existing bracket orders
                if target_id:
                    self.broker.cancel_order(target_id)
                    self.logger.info(f"  Cancelled target order: {target_id}")
                if sl_id:
                    self.broker.cancel_order(sl_id)
                    self.logger.info(f"  Cancelled SL order: {sl_id}")

                # Clear bracket order info
                self._bracket_order_info = None

                # Trigger forced market exit
                position = self.position_manager.current_position
                if position:
                    self._on_exit_triggered(position, ExitReason.TIME)
                return

        # Update spread and log P&L every cycle (second-by-second monitoring)
        # Note: Bracket order status is already checked via check_bracket_order_status() above
        if self._cycle_count % 1 == 0:  # Every cycle
            position = self.position_manager.current_position
            if position and position.built_strategy:
                # Get LIVE spread from broker using spread tracker
                try:
                    current_spread = self.position_monitor.spread_tracker.get_current_spread(position.built_strategy)
                    underlying_price = self.broker.get_price(self.config.underlying_symbol)
                    # Update position with live spread
                    self.position_manager.update_position(current_spread, underlying_price)
                except Exception as e:
                    # Fallback to entry spread if quote fails
                    self.logger.debug(f"Spread quote failed: {e}")
                    current_spread = position.entry_spread

                # Detect stale prices during IBKR disconnections
                stale_indicator = ""
                current_time = datetime.now(US_EASTERN)

                # Check if spread value has changed
                if self._last_spread_value is not None and abs(current_spread - self._last_spread_value) > 0.001:
                    # Spread changed - update tracking
                    self._last_spread_value = current_spread
                    self._last_spread_change_time = current_time
                elif self._last_spread_value is None:
                    # First reading - initialize tracking
                    self._last_spread_value = current_spread
                    self._last_spread_change_time = current_time
                else:
                    # Same spread value - check if stale
                    if self._last_spread_change_time:
                        seconds_unchanged = (current_time - self._last_spread_change_time).total_seconds()
                        if seconds_unchanged >= self._stale_price_threshold_seconds:
                            stale_indicator = " [STALE DATA - IBKR DISCONNECTED]"

                # Log P&L with strategy info
                spread_type = "CREDIT" if position.is_credit else "DEBIT"
                strategy_name = position.strategy_id.replace("STRAT_", "")  # e.g., CREDIT_PUT
                self.logger.info(f"[P&L] [{strategy_name}] [{spread_type}] Entry: {position.entry_spread:.2f} | Current: {current_spread:.2f} | Unrealized: ${position.unrealized_pnl:.2f}{stale_indicator}")

        # Handle partial fills - update bracket order info with remaining quantity
        if status.get('partial_fill') and not status['position_closed']:
            filled_qty = status.get('filled_quantity', 0)
            remaining_qty = status.get('remaining_quantity', 0)
            total_qty = status.get('total_quantity', 0)
            exit_type = status.get('exit_type', 'unknown')

            # Log partial fill warning
            if not self._bracket_order_info.get('_partial_fill_logged', False):
                self.logger.warning("=" * 60)
                self.logger.warning(f"[PARTIAL FILL] {exit_type.upper()} order partially filled")
                self.logger.warning(f"  Filled: {filled_qty} / {total_qty} contracts")
                self.logger.warning(f"  Remaining: {remaining_qty} contracts")
                self.logger.warning(f"  Fill price: ${abs(status.get('fill_price', 0)):.2f}")
                self.logger.warning("=" * 60)
                self._bracket_order_info['_partial_fill_logged'] = True

            # Update bracket order info with partial fill data
            self._bracket_order_info['partial_fill'] = True
            self._bracket_order_info['filled_quantity'] = filled_qty
            self._bracket_order_info['remaining_quantity'] = remaining_qty
            self._bracket_order_info['total_quantity'] = total_qty

            # Update position contracts to reflect remaining quantity
            position = self.position_manager.current_position
            if position and remaining_qty > 0:
                # Track partial P&L from filled portion
                partial_pnl = 0
                if status.get('fill_price'):
                    exit_spread = -status['fill_price']
                    # Use self.config.contract_size (100 for options), not position.contract_size which doesn't exist
                    contract_size = self.config.contract_size
                    if position.is_credit:
                        partial_pnl = (position.entry_spread - exit_spread) * filled_qty * contract_size
                    else:
                        partial_pnl = (exit_spread - position.entry_spread) * filled_qty * contract_size
                    self._bracket_order_info['partial_realized_pnl'] = self._bracket_order_info.get('partial_realized_pnl', 0) + partial_pnl
                    self.logger.info(f"[PARTIAL P&L] Realized ${partial_pnl:.2f} on {filled_qty} contracts")

        # Check if position was closed by bracket order
        if status['position_closed']:
            # Log prominent exit trigger message immediately
            exit_type_display = "TARGET HIT" if status['exit_type'] == 'target' else "STOP-LOSS HIT" if status['exit_type'] == 'sl' else "EXIT TRIGGERED"
            fill_price_display = f"@ ${abs(status['fill_price']):.2f}" if status['fill_price'] else ""
            self.logger.info("")
            self.logger.info("")
            self.logger.info("*" * 70)
            self.logger.info(f"***   {exit_type_display} {fill_price_display}   ***")
            self.logger.info("*" * 70)
            self.logger.info("")

            # ===== VALIDATION: Verify OUR position legs are closed on IBKR =====
            # Note: Trader may have OTHER SPX positions opened manually - we only check our legs
            self.logger.info("[VALIDATION] Verifying exit fill...")
            import time
            self.broker.ib.sleep(0.5)  # Allow IBKR to update position data

            # Get our position's leg strikes to check
            position = self.position_manager.current_position
            our_leg_strikes = set()
            if position and position.built_strategy:
                for leg in position.built_strategy.legs:
                    our_leg_strikes.add((leg.strike, leg.option_type.value))

            # Check IBKR positions for our specific legs
            spx_positions = [p for p in self.broker.ib.positions() if p.contract.symbol == 'SPX']
            our_legs_on_ibkr = []
            other_positions = []

            for pos in spx_positions:
                leg_key = (pos.contract.strike, pos.contract.right)
                if leg_key in our_leg_strikes:
                    our_legs_on_ibkr.append(pos)
                else:
                    other_positions.append(pos)

            # If our legs still showing, retry once more after a short delay
            if our_legs_on_ibkr:
                self.logger.debug(f"Our position legs still showing after bracket fill, retrying in 1s...")
                time.sleep(1)
                spx_positions = [p for p in self.broker.ib.positions() if p.contract.symbol == 'SPX']
                our_legs_on_ibkr = []
                other_positions = []
                for pos in spx_positions:
                    leg_key = (pos.contract.strike, pos.contract.right)
                    if leg_key in our_leg_strikes:
                        our_legs_on_ibkr.append(pos)
                    else:
                        other_positions.append(pos)

            if not our_legs_on_ibkr:
                self.logger.info("  ✓ Position CLOSED verified: Our position legs closed on IBKR")
                if other_positions:
                    self.logger.info(f"  ℹ️ Note: {len(other_positions)} other SPX position(s) exist (not from this system)")
                # No positions - exit completed successfully

                position = self.position_manager.current_position
                if position:
                    underlying_price = self.broker.get_price(self.config.underlying_symbol)

                    # Use actual fill price from IBKR
                    # For reversed combo exits, IBKR reports:
                    # - Negative fill_price = we received money (selling to close debit spread)
                    # - Positive fill_price = we paid money (buying to close credit spread)
                    #
                    # Our spread convention (BUY - SELL from original position perspective):
                    # - Debit spread: positive entry, positive exit (value we receive)
                    # - Credit spread: negative entry, negative exit (cost to close)
                    #
                    # Solution: Always negate the fill_price to convert from IBKR's
                    # cashflow perspective to our spread value perspective
                    raw_fill_price = status['fill_price'] if status['fill_price'] else entry_price * 0.5
                    exit_spread = -raw_fill_price
                    self.logger.debug(f"Reversed combo exit: raw_fill={raw_fill_price:.4f} -> exit_spread={exit_spread:.4f} (is_credit={position.is_credit})")

                    # Determine exit reason
                    if status['exit_type'] == 'target':
                        exit_reason = ExitReason.TARGET
                    elif status['exit_type'] == 'sl':
                        exit_reason = ExitReason.STOP  # STOP not STOP_LOSS
                    else:
                        exit_reason = ExitReason.MANUAL

                    closed_position = self.position_manager.close_position(
                        exit_spread=exit_spread,
                        exit_reason=exit_reason,
                        underlying_price=underlying_price
                    )

                    # Calculate duration
                    duration_minutes = None
                    entry_time_str = None
                    exit_time_str = None
                    if closed_position.entry_time and closed_position.exit_time:
                        duration = closed_position.exit_time - closed_position.entry_time
                        duration_minutes = int(duration.total_seconds() / 60)
                        entry_time_str = closed_position.entry_time.strftime("%H:%M")
                        exit_time_str = closed_position.exit_time.strftime("%H:%M")

                    # Include any partial P&L from earlier partial fills
                    total_realized_pnl = closed_position.realized_pnl
                    partial_pnl = self._bracket_order_info.get('partial_realized_pnl', 0) if self._bracket_order_info else 0
                    if partial_pnl != 0:
                        total_realized_pnl += partial_pnl
                        self.logger.info(f"[TOTAL P&L] Final: ${closed_position.realized_pnl:.2f} + Partial: ${partial_pnl:.2f} = ${total_realized_pnl:.2f}")

                    # Simple trader-friendly exit log
                    # CRITICAL: Use closed_position.exit_spread (actual fill) not exit_spread variable
                    self.logger.log_trade_closed_simple(
                        exit_reason=exit_reason.value,
                        entry_spread=closed_position.entry_spread,
                        exit_spread=closed_position.exit_spread,
                        realized_pnl=total_realized_pnl,
                        contracts=closed_position.contracts,
                        target_spread=closed_position.target_spread,
                        stop_spread=closed_position.stop_spread,
                        entry_time=entry_time_str,
                        exit_time=exit_time_str,
                        duration_minutes=duration_minutes
                    )

                    # Write trade to CSV (same as _finalize_exit)
                    expiry = ""
                    if closed_position.built_strategy and closed_position.built_strategy.legs:
                        first_leg = closed_position.built_strategy.legs[0]
                        if hasattr(first_leg, 'contract') and hasattr(first_leg.contract, 'expiry'):
                            expiry = first_leg.contract.expiry

                    trade_data = {
                        "position_id": closed_position.position_id,
                        "strategy_id": closed_position.strategy_id,
                        "direction_bias": closed_position.direction_bias,
                        "vol_regime": closed_position.vol_regime,
                        "trend_regime": closed_position.trend_regime,
                        "expiry": expiry,
                        "legs": closed_position.leg_details,
                        "entry_spread": closed_position.entry_spread,
                        "exit_spread": closed_position.exit_spread,
                        "target_spread": closed_position.target_spread,
                        "stop_spread": closed_position.stop_spread,
                        "exit_reason": exit_reason.value,
                        "contracts": closed_position.contracts,
                        "pnl_per_contract": total_realized_pnl / closed_position.contracts if closed_position.contracts > 0 else 0,
                        "total_pnl": total_realized_pnl,
                        "entry_time": closed_position.entry_time.strftime("%Y-%m-%d %H:%M:%S"),
                        "exit_time": closed_position.exit_time.strftime("%Y-%m-%d %H:%M:%S") if closed_position.exit_time else None,
                        "underlying_price_entry": closed_position.underlying_price_entry,
                        "underlying_price_exit": closed_position.underlying_price_current
                    }
                    self.logger.log_trade_exit(trade_data)

                # Position already verified closed above (spx_positions check)
                self.logger.info("✓ POSITION VERIFIED CLOSED ON IBKR")

                # Clear bracket order info and reset warning count
                self._bracket_order_info = None
                self._bracket_close_warning_count = 0

                # Reset stale price tracking
                self._last_spread_value = None
                self._last_spread_change_time = None

                # Reset time-to-close warnings for next trade
                self._warning_10min_shown = False
                self._warning_5min_shown = False
                self._warning_2min_shown = False

                # Reset exit attempt counter
                self._exit_attempt_count = 0

                # Clear state file
                self.state_manager.clear_state()

                # Determine next state based on current time (same logic as _on_exit_triggered)
                current_time = datetime.now(US_EASTERN).time()
                cutoff_time = TimezoneManager.convert_ist_to_eastern(self.config.cutoff_time)
                breakout_start = TimezoneManager.convert_ist_to_eastern(self.config.orb_breakout_start_time)
                breakout_end = TimezoneManager.convert_ist_to_eastern(self.config.orb_breakout_end_time)

                # Check if spans midnight (overnight session)
                spans_midnight = breakout_end < breakout_start

                if spans_midnight:
                    in_breakout_window = current_time >= breakout_start or current_time <= breakout_end
                else:
                    in_breakout_window = breakout_start <= current_time <= breakout_end

                # Check cutoff time
                if spans_midnight:
                    past_cutoff = cutoff_time <= current_time < breakout_start
                else:
                    past_cutoff = current_time >= cutoff_time

                # ORB strategy: One trade per session - shutdown after bracket order completes
                self.logger.info("")
                self.logger.info("=" * 70)
                self.logger.info("***   ORB TRADE COMPLETED - ENGINE SHUTTING DOWN   ***")
                self.logger.info("=" * 70)
                self.logger.info("Bracket order exit completed")
                self.logger.info("Strategy rule: One trade per ORB session")
                self.logger.info("=" * 70)
                self._safe_state_transition(EngineState.SHUTDOWN, "Bracket exit completed - ORB trade completed")
                self._running = False
            else:
                # Still have OUR position legs - something unexpected
                # Track consecutive warnings and force close after threshold
                if not hasattr(self, '_bracket_close_warning_count'):
                    self._bracket_close_warning_count = 0
                self._bracket_close_warning_count += 1

                self.logger.warning(f"Bracket orders show filled but our {len(our_legs_on_ibkr)} position leg(s) still on IBKR (warning {self._bracket_close_warning_count}/5)")
                for pos in our_legs_on_ibkr:
                    self.logger.warning(f"  - {pos.contract.strike} {pos.contract.right} qty={pos.position}")

                # After 5 consecutive warnings, trust the bracket order fill and proceed
                if self._bracket_close_warning_count >= 5:
                    self.logger.warning("=" * 60)
                    self.logger.warning("[RECOVERY] Forcing position close after 5 warnings")
                    self.logger.warning("=" * 60)
                    self.logger.warning("  Bracket orders filled but IBKR position data inconsistent")
                    self.logger.warning("  Trusting bracket order fill status and clearing state")

                    position = self.position_manager.current_position
                    if position:
                        # Use estimated exit price (target price since that's what filled)
                        exit_spread = -status.get('fill_price', position.target_spread)
                        if status['exit_type'] == 'target':
                            exit_reason = ExitReason.TARGET
                        elif status['exit_type'] == 'sl':
                            exit_reason = ExitReason.STOP
                        else:
                            exit_reason = ExitReason.MANUAL

                        underlying_price = self.broker.get_price(self.config.underlying_symbol)
                        closed_position = self.position_manager.close_position(
                            exit_spread=exit_spread,
                            exit_reason=exit_reason,
                            underlying_price=underlying_price
                        )

                        # Calculate duration
                        duration_minutes = None
                        entry_time_str = None
                        exit_time_str = None
                        if closed_position.entry_time and closed_position.exit_time:
                            duration = closed_position.exit_time - closed_position.entry_time
                            duration_minutes = int(duration.total_seconds() / 60)
                            entry_time_str = closed_position.entry_time.strftime("%H:%M")
                            exit_time_str = closed_position.exit_time.strftime("%H:%M")

                        self.logger.log_trade_closed_simple(
                            exit_reason=exit_reason.value,
                            entry_spread=closed_position.entry_spread,
                            exit_spread=closed_position.exit_spread,
                            realized_pnl=closed_position.realized_pnl,
                            contracts=closed_position.contracts,
                            target_spread=closed_position.target_spread,
                            stop_spread=closed_position.stop_spread,
                            entry_time=entry_time_str,
                            exit_time=exit_time_str,
                            duration_minutes=duration_minutes
                        )

                    # Verify OUR position legs closed on IBKR after forced recovery
                    time.sleep(0.5)
                    final_positions = [p for p in self.broker.ib.positions() if p.contract.symbol == 'SPX']

                    # Check only our specific legs, not other manual positions
                    our_remaining_legs = []
                    other_positions_count = 0
                    for pos in final_positions:
                        leg_key = (pos.contract.strike, pos.contract.right)
                        if leg_key in our_leg_strikes:
                            our_remaining_legs.append(pos)
                        else:
                            other_positions_count += 1

                    if not our_remaining_legs:
                        self.logger.info("✓ POSITION VERIFIED CLOSED ON IBKR (after recovery)")
                        if other_positions_count > 0:
                            self.logger.info(f"  ℹ️ Note: {other_positions_count} other SPX position(s) exist (not from this system)")
                    else:
                        self.logger.warning("=" * 50)
                        self.logger.warning("⚠️ POSITION VERIFICATION FAILED")
                        self.logger.warning("=" * 50)
                        self.logger.warning(f"   {len(our_remaining_legs)} of OUR position leg(s) still showing on IBKR")
                        for pos in our_remaining_legs:
                            self.logger.warning(f"   - {pos.contract.strike} {pos.contract.right} qty={pos.position}")
                        self.logger.warning("")
                        self.logger.warning("   ACTION REQUIRED: Check TWS to verify position is closed")
                        self.logger.warning("=" * 50)

                    # Clear state
                    self._bracket_order_info = None
                    self._bracket_close_warning_count = 0
                    self.state_manager.clear_state()

                    # Determine next state based on current time
                    current_time = datetime.now(US_EASTERN).time()
                    cutoff_time = TimezoneManager.convert_ist_to_eastern(self.config.cutoff_time)
                    breakout_start = TimezoneManager.convert_ist_to_eastern(self.config.orb_breakout_start_time)
                    breakout_end = TimezoneManager.convert_ist_to_eastern(self.config.orb_breakout_end_time)
                    spans_midnight = breakout_end < breakout_start

                    if spans_midnight:
                        in_breakout_window = current_time >= breakout_start or current_time <= breakout_end
                        past_cutoff = cutoff_time <= current_time < breakout_start
                    else:
                        in_breakout_window = breakout_start <= current_time <= breakout_end
                        past_cutoff = current_time >= cutoff_time

                    # ORB strategy: One trade per session - shutdown after bracket forced recovery
                    self.logger.info("")
                    self.logger.info("=" * 70)
                    self.logger.info("***   ORB TRADE COMPLETED - ENGINE SHUTTING DOWN   ***")
                    self.logger.info("=" * 70)
                    self.logger.info("Bracket exit completed (forced recovery)")
                    self.logger.info("Strategy rule: One trade per ORB session")
                    self.logger.info("=" * 70)
                    self._safe_state_transition(EngineState.SHUTDOWN, "Bracket exit (forced recovery) - ORB trade completed")
                    self._running = False

    def _can_place_order(self) -> tuple[bool, str]:
        """
        Check if we can place a new order. (Issues 5, 6)

        Returns:
            Tuple of (can_place, reason)
        """
        # Issue 1, 2: Check signal-only mode first
        if self._is_signal_only_mode():
            return False, "Signal-only mode enabled - no order placement"

        # Check 0: Entry cutoff time - block new entries near market close
        if self.config.entry_cutoff_time is not None:
            current_time_et = datetime.now(US_EASTERN).time()
            current_time_ist = datetime.now(IST).time()
            # Convert IST config time to ET for comparison
            entry_cutoff_et = TimezoneManager.convert_ist_to_eastern(self.config.entry_cutoff_time)
            session_end_et = TimezoneManager.convert_ist_to_eastern(self.config.session_end_time)
            # Config times are already in IST
            entry_cutoff_ist = self.config.entry_cutoff_time
            session_end_ist = self.config.session_end_time
            if current_time_et >= entry_cutoff_et:
                self.logger.warning("=" * 60)
                self.logger.warning("⚠️ ENTRY BLOCKED - Too close to market close")
                self.logger.warning("=" * 60)
                self.logger.warning(f"  Current time: {current_time_et.strftime('%H:%M:%S')} ET / {current_time_ist.strftime('%H:%M:%S')} IST")
                self.logger.warning(f"  Entry cutoff: {entry_cutoff_et.strftime('%H:%M:%S')} ET / {entry_cutoff_ist.strftime('%H:%M:%S')} IST")
                self.logger.warning(f"  Market close: {session_end_et.strftime('%H:%M:%S')} ET / {session_end_ist.strftime('%H:%M:%S')} IST")
                self.logger.warning(f"  Reason: Not enough time to manage position before close")
                self.logger.warning("=" * 60)
                return False, "Entry blocked - past entry cutoff time"

        # Check 1: Already have an open position tracked internally
        if self.position_manager.has_open_position():
            return False, "Already have open position (internal)"

        # Check 2: Have a pending order that hasn't timed out
        if self._pending_order_id is not None:
            # Issue 5: Check if we should cancel the stale order
            order_timeout = getattr(self.config, 'order_timeout_seconds', 60.0)
            if self._last_order_time:
                elapsed = (datetime.now(US_EASTERN) - self._last_order_time).total_seconds()
                if elapsed > order_timeout:
                    # Cancel the stale order (Issue 5, 6)
                    self.logger.warning(f"Cancelling stale pending order: {self._pending_order_id}")
                    self._cancel_unfilled_orders()
                    self._pending_order_id = None
                else:
                    return False, f"Pending order {self._pending_order_id} still active"

        # Check 3: Check cooldown period
        if self._last_order_time is not None:
            elapsed = (datetime.now(US_EASTERN) - self._last_order_time).total_seconds()
            if elapsed < self._order_cooldown_seconds:
                remaining = self._order_cooldown_seconds - elapsed
                return False, f"Order cooldown active ({remaining:.0f}s remaining)"

        # Check 4: Check TWS for actual positions - Issue 6
        # NOTE: Manual positions in TWS are IGNORED - only our tracked positions matter
        try:
            tws_positions = self.broker.get_positions()
            # Filter for SPX/SPXW options
            spx_positions = [p for p in tws_positions
                           if p.get('symbol', '').upper() in ('SPX', 'SPXW')]

            # Only block if WE have a position (position_manager has one)
            # Manual TWS positions without internal tracking are IGNORED
            if self.position_manager.has_open_position():
                # We have our own position - can't open another
                return False, "Already have open position (internal tracking)"

            # If TWS has positions but we don't track them, just log and continue
            # These are manual positions - we ignore them
            if spx_positions and not self._manual_positions_logged:
                self.logger.info(f"[IGNORING] {len(spx_positions)} manual SPX positions in TWS - continuing with engine operation")
                self._manual_positions_logged = True
        except Exception as e:
            self.logger.warning(f"Could not check TWS positions: {e}")

        # Check 5: Max retries - removed to allow continuous retries
        # The engine will keep retrying until market close or successful fill
        # Retry count is tracked for logging purposes only
        if self._order_retry_count > 0:
            self.logger.debug(f"Order retry attempt #{self._order_retry_count + 1}")

        # Check 6: Circuit breaker (Issue 13)
        if not self._check_circuit_breaker():
            return False, "Circuit breaker tripped - no new orders"

        # Check 7: Daily P&L limits
        pnl_ok, pnl_reason = self._check_daily_pnl_limits()
        if not pnl_ok:
            return False, pnl_reason

        # Check 8: Market hours validation
        if not is_us_options_market_open():
            return False, "Market is closed - cannot place orders"

        return True, "OK"

    def _check_daily_pnl_limits(self) -> tuple:
        """
        Check if daily P&L limits have been hit.

        Returns:
            Tuple of (can_trade, reason)
        """
        # Skip if already flagged
        if self._daily_pnl_limit_hit:
            return False, self._daily_pnl_limit_reason or "Daily P&L limit reached"

        # Get daily P&L from position manager
        if self.position_manager:
            summary = self.position_manager.get_daily_summary()
            self._daily_realized_pnl = summary.get('total_pnl', 0)

        # Check daily loss limit
        daily_loss_limit = getattr(self.config, 'daily_loss_limit', 0)
        if daily_loss_limit > 0 and self._daily_realized_pnl < 0:
            if abs(self._daily_realized_pnl) >= daily_loss_limit:
                self._daily_pnl_limit_hit = True
                self._daily_pnl_limit_reason = f"Daily loss limit hit (${abs(self._daily_realized_pnl):.2f} >= ${daily_loss_limit:.2f})"
                self.logger.warning("=" * 60)
                self.logger.warning("⚠️ DAILY LOSS LIMIT REACHED")
                self.logger.warning("=" * 60)
                self.logger.warning(f"   Daily P&L: ${self._daily_realized_pnl:.2f}")
                self.logger.warning(f"   Loss Limit: ${daily_loss_limit:.2f}")
                self.logger.warning("   NO NEW TRADES TODAY")
                self.logger.warning("=" * 60)
                return False, self._daily_pnl_limit_reason

        # Check daily profit target
        daily_profit_target = getattr(self.config, 'daily_profit_target', 0)
        if daily_profit_target > 0 and self._daily_realized_pnl > 0:
            if self._daily_realized_pnl >= daily_profit_target:
                self._daily_pnl_limit_hit = True
                self._daily_pnl_limit_reason = f"Daily profit target hit (${self._daily_realized_pnl:.2f} >= ${daily_profit_target:.2f})"
                self.logger.info("=" * 60)
                self.logger.info("🎯 DAILY PROFIT TARGET REACHED")
                self.logger.info("=" * 60)
                self.logger.info(f"   Daily P&L: ${self._daily_realized_pnl:.2f}")
                self.logger.info(f"   Profit Target: ${daily_profit_target:.2f}")
                self.logger.info("   NO NEW TRADES TODAY - Target achieved!")
                self.logger.info("=" * 60)
                return False, self._daily_pnl_limit_reason

        return True, "OK"

    def _check_config_changes(self):
        """
        Check if config files have been modified during runtime.
        Warns if position is open and config changed.
        """
        if not self.config_loader or not self.config_loader.config_dir:
            return

        try:
            # Check main parameters.csv
            params_file = self.config_loader.config_dir / "parameters.csv"
            if params_file.exists():
                current_mtime = params_file.stat().st_mtime

                # Initialize on first check
                if self._config_mtime is None:
                    self._config_mtime = current_mtime
                    return

                # Check if modified
                if current_mtime != self._config_mtime:
                    self._config_mtime = current_mtime

                    # Warn if position is open
                    if self.position_manager and self.position_manager.has_open_position():
                        if not self._config_change_warned:
                            self._config_change_warned = True
                            self.logger.warning("=" * 60)
                            self.logger.warning("⚠️ CONFIG FILE CHANGED DURING OPEN POSITION")
                            self.logger.warning("=" * 60)
                            self.logger.warning("   Config changes will NOT take effect until:")
                            self.logger.warning("   - Current position is closed")
                            self.logger.warning("   - Engine is restarted")
                            self.logger.warning("")
                            self.logger.warning("   Current position will continue with ORIGINAL settings")
                            self.logger.warning("=" * 60)
                    else:
                        # No position open - just log info
                        self._config_change_warned = False
                        self.logger.info("[CONFIG] Configuration file modified - changes will apply to next trade")
        except Exception as e:
            self.logger.debug(f"Config change check error: {e}")

    def _reset_order_tracking(self):
        """Reset order tracking after successful fill or end of day"""
        self._pending_order_id = None
        self._last_order_time = None
        self._order_retry_count = 0

    def _execute_signal(self, signal: Signal):
        """Execute a trading signal"""
        # SIGNAL-ONLY MODE: Log signal details but don't execute
        # This is the single point of enforcement for signal-only mode
        if self._is_signal_only_mode():
            self._log_signal_only(signal)
            # Also log to CSV for later analysis
            self.logger.log_signal({
                'timestamp': datetime.now(US_EASTERN).strftime('%Y-%m-%d %H:%M:%S'),
                'direction_bias': signal.direction_bias.value if signal.direction_bias else '',
                'vol_regime': signal.vol_regime.value if signal.vol_regime else '',
                'trend_regime': signal.trend_regime.value if signal.trend_regime else '',
                'underlying_price': signal.underlying_price,
                'or_high': signal.or_high,
                'or_low': signal.or_low,
                'signal_ok': signal.signal_ok,
                'reason': signal.reason or 'Signal-only mode',
                'use_orb': self.config.use_orb,
                'use_trend_filter': self.config.use_trend_filter,
                'use_ivp': self.config.use_ivp,
            })
            return  # Exit early - no execution in signal-only mode

        # NOTE: Manual positions in TWS are IGNORED - they don't block execution.
        # The engine proceeds with its own trading regardless of manual positions.

        # Issue 5: Cancel any unfilled orders BEFORE processing new signal
        # This prevents duplicate orders if previous order is still pending
        self._cancel_unfilled_orders()

        # Issue 6: Reconcile with TWS to detect stale fills that may have occurred
        if not self._reconcile_orders_and_positions():
            self.logger.warning("Skipping signal execution: Order/position reconciliation failed")
            self._record_blocked_iteration("Order/position reconciliation failed")
            return

        # Check if we can place an order first
        can_place, reason = self._can_place_order()
        if not can_place:
            self.logger.info(f"Skipping signal execution: {reason}")
            self._record_blocked_iteration(reason)
            return

        # Check if we need to wait for new bar after trade close
        if self._last_trade_close_bar is not None:
            current_bar = self._get_current_bar_timestamp()
            if current_bar is not None and current_bar <= self._last_trade_close_bar:
                self.logger.debug(f"Waiting for new bar after trade close (closed on bar {self._last_trade_close_bar})")
                return
            else:
                # New bar started, clear the block
                self._last_trade_close_bar = None
                self.logger.info("[NEW BAR] Ready to enter after previous trade close")

        # Reset blocked iterations counter - we're about to take action
        self._reset_blocked_iterations()

        # Visual separator before taking position
        self.logger.info("")
        self.logger.info("")
        self.logger.info("*" * 70)
        self.logger.info("***   SIGNAL DETECTED - PREPARING TO ENTER POSITION   ***")
        self.logger.info("*" * 70)
        self.logger.info("")
        self.logger.info("=" * 60)
        self.logger.info("*** EXECUTING TRADING SIGNAL ***")
        self.logger.info("=" * 60)
        self.current_signal = signal

        # Log signal evaluation
        self.logger.log_signal_evaluation({
            'direction_bias': signal.direction_bias.value if signal.direction_bias else 'N/A',
            'trend_regime': signal.trend_regime.value if signal.trend_regime else 'N/A',
            'vol_regime': signal.vol_regime.value if signal.vol_regime else 'N/A',
            'underlying_price': signal.underlying_price,
            'or_high': signal.or_high,
            'or_low': signal.or_low,
            'ma_short': signal.ma_short,
            'ma_long': signal.ma_long,
            'iv_atm': signal.iv_atm,
            'ivp': signal.ivp,
            'signal_ok': signal.signal_ok,
            'reason': signal.reason
        })

        # Select strategy
        selection = self.strategy_selector.select_strategy(signal)

        self.logger.log_strategy_selection(
            strategy_id=selection.strategy_id,
            direction=signal.direction_bias.value if signal.direction_bias else 'N/A',
            vol_regime=signal.vol_regime.value if signal.vol_regime else 'N/A',
            trend_regime=signal.trend_regime.value if signal.trend_regime else 'N/A',
            is_valid=selection.is_valid,
            reason=selection.reason
        )

        # Get ORB state for later use
        orb_state = self.signal_generator.orb.get_state() if self.signal_generator and self.signal_generator.orb else None

        if not selection.is_valid:
            self.logger.log_no_trade_reason(
                selection.reason,
                direction=signal.direction_bias.value if signal.direction_bias else None,
                vol_regime=signal.vol_regime.value if signal.vol_regime else None,
                trend_regime=signal.trend_regime.value if signal.trend_regime else None
            )
            return

        # Check if US options market is open before attempting to build strategy
        if not is_us_options_market_open():
            current_time_et = datetime.now(US_EASTERN).strftime("%H:%M:%S ET")
            self.logger.error("=" * 60)
            self.logger.error("[MARKET CLOSED] Cannot execute strategy - US options market is closed")
            self.logger.error("=" * 60)
            self.logger.error(f"  Current time: {current_time_et}")
            self.logger.error("  SPX options trade: 9:30 AM - 4:15 PM ET")
            self.logger.error("  Cannot get live option quotes when market is closed")
            self.logger.error("  Action: ENGINE SHUTTING DOWN")
            self.logger.error("=" * 60)
            self._safe_state_transition(EngineState.SHUTDOWN, "Market closed during signal execution")
            return

        # Build strategy
        underlying_price = self.broker.get_latest_price()
        self.logger.debug(f"Building strategy {selection.strategy_id} at underlying ${underlying_price:.2f}")

        built_strategy = self.strategy_builder.build_strategy(
            selection.template,
            underlying_price
        )

        if built_strategy.error:
            self.logger.error("=" * 60)
            self.logger.error("[STRATEGY BUILD FAILED] Cannot build trading strategy")
            self.logger.error("=" * 60)
            self.logger.error(f"  Strategy ID: {selection.strategy_id}")
            self.logger.error(f"  Underlying Price: ${underlying_price:.2f}")
            self.logger.error(f"  Error: {built_strategy.error}")
            self.logger.error("")
            self.logger.error("  Possible causes:")
            self.logger.error("    - Option chain not available for requested strikes")
            self.logger.error("    - Invalid delta targets in strategy template")
            self.logger.error("    - No options found matching criteria")
            self.logger.error("    - IBKR contract qualification failed")
            self.logger.error("  Action: ENGINE SHUTTING DOWN")
            self.logger.error("=" * 60)
            self.logger.log_error_detailed(
                "STRATEGY_BUILD_FAILED",
                built_strategy.error,
                {"strategy_id": selection.strategy_id, "underlying_price": underlying_price}
            )
            self._safe_state_transition(EngineState.SHUTDOWN, f"Strategy build failed: {built_strategy.error}")
            return

        # Refresh prices with real market quotes before executing
        self.logger.debug(f"Requesting fresh market quotes for {len(built_strategy.legs)} option legs...")
        try:
            # Get contracts from all legs
            contracts = [leg.contract for leg in built_strategy.legs]

            # Request fresh market data
            refresh_success = self.broker.get_option_quotes(contracts)

            if refresh_success:
                self.logger.debug("Successfully retrieved real market quotes for all legs")
                # Update prices and recalculate entry spread
                for leg in built_strategy.legs:
                    fresh_price = leg.contract.get_price(self.config.option_entry_price_field)
                    leg.current_price = fresh_price
                    # Apply slippage
                    if leg.side.value == 'BUY':
                        leg.entry_price = fresh_price + self.config.slippage_per_leg
                    else:
                        leg.entry_price = fresh_price - self.config.slippage_per_leg
                    leg.entry_price = max(0.01, leg.entry_price)
                    self.logger.debug(f"  Leg {leg.leg_index}: Fresh price ${fresh_price:.2f}, Entry ${leg.entry_price:.2f}")

                # Recalculate entry spread with fresh prices
                built_strategy.entry_spread = built_strategy.calculate_spread_value(
                    self.config.option_entry_price_field
                )
                self.logger.debug(f"Updated entry spread with real quotes: ${built_strategy.entry_spread:.2f}")
            else:
                self.logger.warning("⚠️  Could not retrieve all market quotes - using estimated prices")
        except Exception as e:
            self.logger.error(f"Error refreshing prices: {e} - using estimated prices")

        self.current_strategy = built_strategy

        # Log built strategy details
        leg_details = []
        for leg in built_strategy.legs:
            leg_details.append({
                'option_type': leg.option_type.value,
                'strike': leg.strike,
                'side': leg.side.value,
                'delta_target': leg.target_delta,
                'delta_actual': leg.actual_delta
            })

        self.logger.log_strategy_build_details(
            strategy_id=built_strategy.strategy_id,
            legs=leg_details,
            entry_spread=built_strategy.entry_spread,
            is_credit=built_strategy.is_credit,
            underlying_price=underlying_price
        )

        # Log signal with leg details to CSV (updated with strike prices)
        legs_for_signal = [
            {"type": leg.option_type.value, "strike": leg.strike, "side": leg.side.value}
            for leg in built_strategy.legs
        ]
        self.logger.log_signal({
            "underlying_price": underlying_price,
            "or_high": signal.or_high,
            "or_low": signal.or_low,
            "breakout_price": orb_state.breach_price if orb_state else None,
            "ma_short": signal.ma_short,
            "ma_long": signal.ma_long,
            "direction_bias": signal.direction_bias.value if signal.direction_bias else None,
            "trend_regime": signal.trend_regime.value if signal.trend_regime else None,
            "vol_regime": signal.vol_regime.value if signal.vol_regime else None,
            "iv_atm": signal.iv_atm,
            "ivp": signal.ivp,
            "signal_ok": True,
            "strategy_selected": built_strategy.strategy_id,
            "legs": legs_for_signal,
            "entry_spread": built_strategy.entry_spread,
            "use_orb": self.config.use_orb,
            "use_trend_filter": self.config.use_trend_filter,
            "use_ivp": self.config.use_ivp,
            "orb_window_minutes": self.config.orb_window_minutes,
            "reason": "Strategy built with strikes"
        })

        # Calculate risk parameters
        risk_params = self.risk_calculator.calculate_risk_metrics(built_strategy)

        # Log risk parameters
        self.logger.debug("-" * 50)
        self.logger.debug("[RISK PARAMETERS]")
        self.logger.debug(f"   Contracts: {risk_params.contracts}")
        self.logger.debug(f"   Target Spread: ${risk_params.target_spread:.4f}")
        self.logger.debug(f"   Stop Spread: ${risk_params.stop_spread:.4f}")
        self.logger.debug(f"   Max Risk: ${risk_params.max_risk:.2f}")
        self.logger.debug(f"   Max Reward: ${risk_params.max_reward:.2f}")
        self.logger.debug(f"   Risk/Reward Ratio: {risk_params.risk_reward_ratio:.2f}")
        self.logger.debug("-" * 50)

        # Validate
        is_valid, reason = self.risk_calculator.validate_position(risk_params)
        if not is_valid:
            self.logger.error("=" * 60)
            self.logger.error("[POSITION VALIDATION FAILED] Risk parameters rejected")
            self.logger.error("=" * 60)
            self.logger.error(f"  Reason: {reason}")
            self.logger.error(f"  Contracts: {risk_params.contracts}")
            self.logger.error(f"  Max Risk: ${risk_params.max_risk:.2f}")
            self.logger.error(f"  Max Reward: ${risk_params.max_reward:.2f}")
            self.logger.error("")
            self.logger.error("  Possible causes:")
            self.logger.error("    - Position size exceeds max loss limit")
            self.logger.error("    - Risk/reward ratio out of acceptable range")
            self.logger.error("    - Margin requirements not met")
            self.logger.error("  Action: SKIPPING THIS TRADE - will retry on next bar")
            self.logger.error("=" * 60)
            self.logger.warning(f"[TRADE SKIPPED] {reason} - will retry on next bar")
            # Set bar timestamp to prevent immediate retry on same bar
            self._last_trade_close_bar = self._get_current_bar_timestamp()
            # Return to scanning state instead of shutdown
            self._safe_state_transition(EngineState.MONITORING_SIGNALS, f"Trade skipped: {reason}")
            return

        self.logger.debug("Position validated successfully, executing entry...")

        # Execute entry using IBKRWrapper
        # Pass signal and underlying_price for immediate state save callback
        result = self._execute_entry(
            built_strategy,
            risk_params.contracts,
            signal=signal,
            underlying_price=underlying_price
        )

        if result["success"]:
            fill_spread = result.get("fill_price", built_strategy.entry_spread)

            # Check if position was already created by on_entry_filled callback
            # (for immediate state save during bracket order flow)
            if self.position_manager.current_position is not None:
                position = self.position_manager.current_position
                self.logger.debug("Using position created by entry callback")
            else:
                direction_bias = signal.direction_bias.value if signal.direction_bias else ""
                vol_regime = signal.vol_regime.value if signal.vol_regime else ""
                trend_regime = signal.trend_regime.value if signal.trend_regime else ""

                position = self.position_manager.create_position(
                    built_strategy=built_strategy,
                    contracts=risk_params.contracts,
                    direction_bias=direction_bias,
                    vol_regime=vol_regime,
                    trend_regime=trend_regime,
                    underlying_price=underlying_price,
                    fill_spread=fill_spread  # Use actual fill price, not estimate
                )

            # Reset time-to-close warnings for new position
            self._warning_10min_shown = False
            self._warning_5min_shown = False
            self._warning_2min_shown = False

            # Reset stale price tracking for new position
            self._last_spread_value = None
            self._last_spread_change_time = None

            # Simple trader-friendly log - one clean block
            leg_summary = []
            for leg in built_strategy.legs:
                leg_summary.append({
                    'option_type': leg.option_type.value,
                    'strike': leg.strike,
                    'side': leg.side.value,
                    'fill_price': leg.current_price
                })

            # Get actual target/stop prices from bracket order if available
            target_price = self._bracket_order_info.get('target_price', position.target_spread) if self._bracket_order_info else position.target_spread
            stop_price = self._bracket_order_info.get('sl_trigger_price', position.stop_spread) if self._bracket_order_info else position.stop_spread

            self.logger.log_trade_opened_simple(
                legs=leg_summary,
                contracts=position.contracts,
                is_credit=position.is_credit,
                entry_spread=fill_spread,
                target_price=target_price,
                stop_price=stop_price
            )

            self.position_monitor.start()
            self._safe_state_transition(EngineState.POSITION_OPEN, "Position opened, starting monitoring")

            # Save state for crash recovery (include bracket order info if available)
            if self.state_manager:
                self.state_manager.save_state(position, self.broker, self._bracket_order_info)

            # Log signal with executed=True for orphan spread detection
            # This allows recovery from crashes by matching IBKR positions to signal CSV
            legs_for_executed_signal = [
                {"type": leg.option_type.value, "strike": leg.strike, "side": leg.side.value}
                for leg in built_strategy.legs
            ]
            self.logger.log_signal({
                "underlying_price": underlying_price,
                "or_high": signal.or_high,
                "or_low": signal.or_low,
                "breakout_price": orb_state.breach_price if orb_state else None,
                "direction_bias": signal.direction_bias.value if signal.direction_bias else None,
                "trend_regime": signal.trend_regime.value if signal.trend_regime else None,
                "ivp": signal.ivp,
                "signal_ok": True,
                "executed": True,  # Mark as executed for orphan detection
                "strategy_selected": built_strategy.strategy_id,
                "legs": legs_for_executed_signal,
                "entry_spread": fill_spread,
                "reason": "EXECUTED"
            })

            # Verify OUR position legs exist on IBKR
            import time
            time.sleep(0.5)  # Allow IBKR to update
            spx_positions = [p for p in self.broker.ib.positions() if p.contract.symbol == 'SPX']

            # Get our position's expected leg strikes
            our_leg_strikes = set()
            if position.built_strategy:
                for leg in position.built_strategy.legs:
                    our_leg_strikes.add((leg.strike, leg.option_type.value))

            # Check how many of our legs are on IBKR
            our_legs_found = 0
            for pos in spx_positions:
                leg_key = (pos.contract.strike, pos.contract.right)
                if leg_key in our_leg_strikes:
                    our_legs_found += 1

            if our_legs_found == len(our_leg_strikes):
                self.logger.info(f"✓ POSITION VERIFIED OPEN ON IBKR ({our_legs_found}/{len(our_leg_strikes)} legs)")
            elif our_legs_found > 0:
                self.logger.warning("=" * 50)
                self.logger.warning("⚠️ PARTIAL POSITION ON IBKR")
                self.logger.warning("=" * 50)
                self.logger.warning(f"   Only {our_legs_found}/{len(our_leg_strikes)} of our position legs found")
                self.logger.warning("   CHECK TWS - Position may be incomplete")
                self.logger.warning("=" * 50)
            else:
                self.logger.warning("=" * 50)
                self.logger.warning("⚠️ POSITION VERIFICATION WARNING")
                self.logger.warning("=" * 50)
                self.logger.warning("   No SPX positions found on IBKR after entry")
                self.logger.warning("   Entry may not have filled - CHECK TWS IMMEDIATELY")
                self.logger.warning("=" * 50)

        else:
            # Order failed - could be entry rejection OR exit order issue after entry filled
            error_msg = result.get("error", "Unknown error")

            # CRITICAL: If entry was filled but exit orders failed, we still have an open position!
            # FALLBACK: Continue with engine-based monitoring instead of shutting down
            if result.get("entry_filled"):
                fill_spread = result.get("fill_price", built_strategy.entry_spread)

                self.logger.warning("=" * 60)
                self.logger.warning("[ENTRY FILLED BUT EXIT ORDERS FAILED]")
                self.logger.warning("=" * 60)
                self.logger.warning(f"  Entry filled @ {fill_spread:.4f}")
                self.logger.warning(f"  Exit order error: {error_msg}")

                # Check if position was already created by callback
                if self.position_manager.current_position is not None:
                    position = self.position_manager.current_position
                    self.logger.warning("  State already saved by entry callback")
                else:
                    # Fallback: create position and save state
                    direction_bias = signal.direction_bias.value if signal.direction_bias else ""
                    vol_regime = signal.vol_regime.value if signal.vol_regime else ""
                    trend_regime = signal.trend_regime.value if signal.trend_regime else ""

                    self.logger.warning("  Creating position and saving state...")
                    position = self.position_manager.create_position(
                        built_strategy=built_strategy,
                        contracts=risk_params.contracts,
                        direction_bias=direction_bias,
                        vol_regime=vol_regime,
                        trend_regime=trend_regime,
                        underlying_price=underlying_price,
                        fill_spread=fill_spread
                    )

                    # Reset time-to-close warnings for new position
                    self._warning_10min_shown = False
                    self._warning_5min_shown = False
                    self._warning_2min_shown = False

                # ========== BRACKET ORDER FAILURE FALLBACK ==========
                # Try to place exit orders one more time, if that fails use engine monitoring
                self.logger.warning("  Attempting to place exit orders again...")

                # Build legs for exit order placement
                legs_for_exit = []
                for leg in built_strategy.legs:
                    action = "BUY" if leg.side == OrderSide.BUY else "SELL"
                    legs_for_exit.append({
                        "contract": leg.contract.contract,
                        "action": action,
                        "quantity": leg.quantity,
                        "price": leg.current_price
                    })

                # Retry placing exit orders
                exit_retry_result = self.broker.place_exit_orders_only(
                    legs=legs_for_exit,
                    quantity=risk_params.contracts,
                    entry_fill_price=fill_spread,
                    credit_target_pct=self.config.credit_target_factor,
                    credit_sl_pct=self.config.credit_stop_factor,
                    debit_target_pct=self.config.debit_target_factor,
                    debit_sl_pct=self.config.debit_stop_factor,
                    slippage=self.config.slippage_per_leg,
                    sl_limit_offset=self.config.sl_limit_offset,
                )

                if exit_retry_result['success']:
                    # Exit orders placed successfully on retry
                    self._bracket_order_info = {
                        'target_order_id': exit_retry_result['target_order_id'],
                        'sl_order_id': exit_retry_result['sl_order_id'],
                        'oca_group': exit_retry_result['oca_group'],
                        'fill_price': fill_spread,
                        'target_price': exit_retry_result['target_price'],
                        'sl_trigger_price': exit_retry_result['sl_trigger_price'],
                    }
                    self.logger.info("=" * 60)
                    self.logger.info("[EXIT ORDERS RETRY SUCCESS]")
                    self.logger.info(f"  Target order ID: {exit_retry_result['target_order_id']}")
                    self.logger.info(f"  SL order ID: {exit_retry_result['sl_order_id']}")
                    self.logger.info(f"  OCA Group: {exit_retry_result['oca_group']}")
                    self.logger.info("=" * 60)

                    # Save state with bracket order info
                    if self.state_manager:
                        self.state_manager.save_state(position, self.broker, self._bracket_order_info)
                        self.logger.info("  State saved with bracket order info")
                else:
                    # Exit orders failed again - FALLBACK to engine-based monitoring
                    self.logger.warning("=" * 60)
                    self.logger.warning("[FALLBACK] Exit order retry failed")
                    self.logger.warning(f"  Error: {exit_retry_result.get('error_message', 'Unknown')}")
                    self.logger.warning("  SWITCHING TO ENGINE-BASED EXIT MONITORING")
                    self.logger.warning("  Engine will monitor P&L and trigger exits via software")
                    self.logger.warning("=" * 60)

                    # Clear bracket order info - use engine monitoring
                    self._bracket_order_info = None

                    # Save state without bracket order info
                    if self.state_manager:
                        self.state_manager.save_state(position, self.broker, None)
                        self.logger.warning("  State saved (no bracket orders - using engine monitoring)")

                # Log signal with executed=True for orphan spread detection
                legs_for_executed_signal = [
                    {"type": leg.option_type.value, "strike": leg.strike, "side": leg.side.value}
                    for leg in built_strategy.legs
                ]
                self.logger.log_signal({
                    "underlying_price": underlying_price,
                    "or_high": signal.or_high if signal else None,
                    "or_low": signal.or_low if signal else None,
                    "direction_bias": signal.direction_bias.value if signal and signal.direction_bias else None,
                    "trend_regime": signal.trend_regime.value if signal and signal.trend_regime else None,
                    "ivp": signal.ivp if signal else None,
                    "signal_ok": True,
                    "executed": True,  # Mark as executed for orphan detection
                    "strategy_selected": built_strategy.strategy_id,
                    "legs": legs_for_executed_signal,
                    "entry_spread": fill_spread,
                    "reason": "EXECUTED (fallback)"
                })

                # Get target/stop prices for logging
                target_price = self._bracket_order_info.get('target_price', position.target_spread) if self._bracket_order_info else position.target_spread
                stop_price = self._bracket_order_info.get('sl_trigger_price', position.stop_spread) if self._bracket_order_info else position.stop_spread

                # Log trade opened
                leg_summary = []
                for leg in built_strategy.legs:
                    leg_summary.append({
                        'option_type': leg.option_type.value,
                        'strike': leg.strike,
                        'side': leg.side.value,
                        'fill_price': leg.current_price
                    })

                self.logger.log_trade_opened_simple(
                    legs=leg_summary,
                    contracts=position.contracts,
                    is_credit=position.is_credit,
                    entry_spread=fill_spread,
                    target_price=target_price,
                    stop_price=stop_price
                )

                # Start position monitoring and transition to POSITION_OPEN
                self._pending_order_id = None
                self._reset_order_tracking()
                self.position_monitor.start()
                self._safe_state_transition(EngineState.POSITION_OPEN, "Position opened (exit order fallback mode)")

                self.logger.warning("=" * 60)
                if self._bracket_order_info:
                    self.logger.info("[RECOVERY] Bracket orders active - IBKR handles exits")
                else:
                    self.logger.warning("[RECOVERY] Engine monitoring active - software handles exits")
                self.logger.warning("=" * 60)

                return  # Don't proceed to shutdown

            else:
                self.logger.error("=" * 60)
                self.logger.error("[ORDER FAILED] Could not fill order")
                self.logger.error("=" * 60)
                self.logger.error(f"  Strategy ID: {selection.strategy_id}")
                self.logger.error(f"  Contracts: {risk_params.contracts}")
                self.logger.error(f"  Error: {error_msg}")
                self.logger.error("=" * 60)

            self._safe_state_transition(EngineState.SHUTDOWN, f"Order failed: {error_msg}")

    def _execute_entry(
        self,
        built_strategy: BuiltStrategy,
        contracts: int,
        signal: "TradingSignal" = None,
        underlying_price: float = 0.0
    ) -> Dict[str, Any]:
        """Execute entry using IBKRWrapper with retry mechanism"""
        self.logger.debug(f"Executing entry for {built_strategy.strategy_id}")
        self.logger.debug(f"Contracts: {contracts}")
        self.logger.debug(f"Retry attempt: {self._order_retry_count + 1}/{self._max_order_retries}")

        # IMPORTANT: Cancel any pending orders before placing a new one
        # This prevents duplicate orders in TWS during retries
        if self._pending_order_id is not None:
            self.logger.debug(f"Cancelling previous pending order before retry: {self._pending_order_id}")
            try:
                self.broker.cancel_all_open_orders()
                time.sleep(1)  # Give TWS time to process cancellation
            except Exception as e:
                self.logger.warning(f"Could not cancel previous order: {e}")
            self._pending_order_id = None

        # Build leg orders
        legs = []
        for leg in built_strategy.legs:
            action = "BUY" if leg.side == OrderSide.BUY else "SELL"

            # Validate leg has valid current price - NO SILENT FALLBACK
            if leg.current_price <= 0:
                self.logger.error("=" * 60)
                self.logger.error("[ENTRY PRICE INVALID] Cannot build order - leg has no valid price")
                self.logger.error("=" * 60)
                self.logger.error(f"  Leg: {leg.option_type.value} strike={leg.strike}")
                self.logger.error(f"  current_price: {leg.current_price}")
                self.logger.error(f"  entry_price: {leg.entry_price}")
                self.logger.error("  Possible causes:")
                self.logger.error("    - Market data not available for this option")
                self.logger.error("    - Option contract not subscribed in IBKR")
                self.logger.error("    - Network/connectivity issues with IBKR")
                self.logger.error("  Impact: Cannot place order with invalid price")
                self.logger.error("  Action: ENGINE SHUTDOWN - cannot trade without valid prices")
                self.logger.error("=" * 60)
                raise CriticalError(
                    f"Leg price invalid: {leg.option_type.value} strike={leg.strike} current_price={leg.current_price}",
                    context={"leg": str(leg), "current_price": leg.current_price, "entry_price": leg.entry_price}
                )

            price = leg.current_price

            legs.append({
                "contract": leg.contract.contract,
                "action": action,
                "quantity": leg.quantity,
                "price": price
            })

        if not legs:
            return {"success": False, "error": "Failed to build leg orders"}

        # Track this order attempt
        self._last_order_time = datetime.now(US_EASTERN)
        self._order_retry_count += 1

        # Check if using bracket orders (IBKR handles SL + Target)
        if getattr(self.config, 'use_bracket_orders', False):
            # Create callback to save state immediately after entry fills
            # This ensures state is saved even if crash happens during exit order placement
            def on_entry_filled_callback(fill_price: float):
                """Save state immediately after entry fills (before exit orders)."""
                self.logger.info("[STATE SAVE] Saving state immediately after entry fill...")
                direction_bias = signal.direction_bias.value if signal and signal.direction_bias else ""
                vol_regime = signal.vol_regime.value if signal and signal.vol_regime else ""
                trend_regime = signal.trend_regime.value if signal and signal.trend_regime else ""

                # Create preliminary position for state saving
                position = self.position_manager.create_position(
                    built_strategy=built_strategy,
                    contracts=contracts,
                    direction_bias=direction_bias,
                    vol_regime=vol_regime,
                    trend_regime=trend_regime,
                    underlying_price=underlying_price,
                    fill_spread=fill_price
                )

                # Reset time-to-close warnings for new position
                self._warning_10min_shown = False
                self._warning_5min_shown = False
                self._warning_2min_shown = False

                # Save state immediately
                if self.state_manager:
                    self.state_manager.save_state(position, self.broker)
                    self.logger.info(f"{position.get_log_tag()} [STATE SAVE] State saved: {position.position_id}")

            # Visual separator before bracket order execution
            self.logger.info("")
            self.logger.info("")
            self.logger.info("*" * 70)
            self.logger.info("***   PLACING BRACKET ORDER ON IBKR   ***")
            self.logger.info("*" * 70)
            self.logger.info("")

            # Use bracket order method - broker auto-detects credit/debit from fill price
            bracket_result = self.broker.place_spread_with_bracket(
                legs=legs,
                quantity=contracts,
                credit_target_pct=self.config.credit_target_factor,
                credit_sl_pct=self.config.credit_stop_factor,
                debit_target_pct=self.config.debit_target_factor,
                debit_sl_pct=self.config.debit_stop_factor,
                slippage=self.config.slippage_per_leg,
                sl_limit_offset=self.config.sl_limit_offset,
                enable_chasing=self.config.enable_price_chasing,
                chase_interval=self.config.chase_interval_seconds,
                max_chase_time=self.config.max_chase_time_seconds,
                on_entry_filled=on_entry_filled_callback,
            )

            if bracket_result['success']:
                self._pending_order_id = None
                self._reset_order_tracking()
                # Store bracket order info for monitoring
                self._bracket_order_info = bracket_result
                self.logger.info(f"Bracket order filled @ {bracket_result['fill_price']:.4f}")
                self.logger.info(f"  Target order ID: {bracket_result['target_order_id']}")
                self.logger.info(f"  SL order ID: {bracket_result['sl_order_id']}")
                self.logger.info(f"  OCA Group: {bracket_result['oca_group']}")
                return {"success": True, "fill_price": bracket_result['fill_price'], "bracket_result": bracket_result}
            else:
                self.logger.error(f"Bracket order failed: {bracket_result['error_message']}")
                # Pass entry_filled flag so caller can save state even if exit orders failed
                return {
                    "success": False,
                    "error": bracket_result['error_message'],
                    "entry_filled": bracket_result.get('entry_filled', False),
                    "fill_price": bracket_result.get('fill_price')
                }

        # Use broker to place spread order (standard mode - software monitors exits)
        # Using LIMIT orders with chasing for better price control
        slippage = self.config.slippage_per_leg
        combo_order = self.broker.place_spread_order(
            legs=legs,
            quantity=contracts,
            use_market=False,  # LIMIT orders with price chasing enabled
            slippage=slippage,
            enable_chasing=self.config.enable_price_chasing,  # Enable intelligent price chasing from config
            chase_interval=self.config.chase_interval_seconds,  # Chase interval from config
            max_chase_time=self.config.max_chase_time_seconds,  # Max chase time from config
            max_price_deviation_pct=self.config.max_price_deviation_pct  # Max price deviation from config
        )

        # Track the pending order
        self._pending_order_id = combo_order.combo_id
        self.logger.info(f"Order submitted: {combo_order.combo_id}, waiting for fill...")

        # Keep waiting for the SAME order until filled or market close
        wait_attempt = 0
        while True:
            wait_attempt += 1
            self.logger.info(f"[ORDER WAITING] Attempt {wait_attempt} - waiting for order {combo_order.combo_id}")

            # Wait for fill with 60s timeout, then check again
            filled_order = self.broker.wait_for_spread_fill(combo_order.combo_id, timeout=60.0)

            if filled_order and filled_order.status.value == "FILLED":
                fill_price = self._calculate_combo_fill_price(filled_order)
                # Clear pending order on success
                self._pending_order_id = None
                self._reset_order_tracking()
                self.logger.info(f"Order filled successfully at {fill_price:.4f}")
                return {"success": True, "fill_price": fill_price, "combo_order": filled_order}

            # Check if order was cancelled/rejected by IBKR - place NEW order
            order_status = getattr(filled_order, 'status', None)
            if order_status and order_status.value in ("CANCELLED", "REJECTED", "ERROR"):
                self.logger.warning(f"Order {combo_order.combo_id} was {order_status.value} by IBKR")
                self.logger.info("[RETRY] Will place new order with fresh quotes...")
                self._pending_order_id = None

                # Wait before placing new order
                time.sleep(5)

                # Check market still open before retrying
                if not is_us_options_market_open():
                    self.logger.warning("Market closed - cannot retry")
                    return {"success": False, "error": "Market closed"}

                # Break out of wait loop to place new order
                # Return special status to signal retry needed
                break

            # Check if market is still open
            if not is_us_options_market_open():
                self.logger.warning("Market closed - cancelling order and stopping")
                self.broker.cancel_all_open_orders()
                self._pending_order_id = None
                return {
                    "success": False,
                    "error": "Market closed while waiting for fill"
                }

            # Order still working - log status and keep waiting
            filled_count = combo_order.get_total_filled() if combo_order else 0
            total_legs = len(combo_order.leg_orders) if combo_order else 0

            if filled_count > 0:
                self.logger.warning(f"Partial fill: {filled_count}/{total_legs} legs - continuing to wait...")
            else:
                self.logger.info(f"[ORDER PENDING] Still waiting for fill... (attempt {wait_attempt})")

            # Small delay before checking again
            time.sleep(2)

        # If we broke out of loop (order rejected), recursively call to place new order
        self.logger.info("[RETRY] Placing new order...")
        return self._execute_entry(built_strategy, contracts)

    def _calculate_combo_fill_price(self, combo_order) -> float:
        """Calculate net fill price from combo order fills.

        For IBKR combo orders, the avgFillPrice on the trade object
        represents the NET spread price, not individual leg prices.
        We use this directly when available.
        """
        # Check if we have the combo avg fill price from any leg
        # (broker_insync sets the same avgFillPrice on all legs from trade.orderStatus.avgFillPrice)
        if combo_order.leg_orders:
            first_leg = combo_order.leg_orders[0]
            if first_leg.avg_fill_price != 0:
                # The avgFillPrice on leg_orders is actually the NET combo price from IBKR
                # We need to determine the sign based on whether it's a debit or credit spread
                # For entry: SELL leg price > BUY leg price = credit (negative spread)
                # For exit: we're reversing, so it's the opposite

                # Count buy vs sell legs to determine spread type
                buy_legs = sum(1 for leg in combo_order.leg_orders if leg.action == "BUY")
                sell_legs = len(combo_order.leg_orders) - buy_legs

                # For a closing order on a credit spread:
                # - We BUY back what we sold (the short leg)
                # - We SELL what we bought (the long leg)
                # - This results in a debit (positive value = cost to close)

                # The IBKR avgFillPrice is the absolute value of the spread
                # For exits, it's typically a debit (we pay to close)
                combo_price = first_leg.avg_fill_price

                self.logger.debug(f"Combo fill price from IBKR: {combo_price:.4f}")
                return combo_price

        # Fallback: calculate from individual leg fills (may not work for IBKR combos)
        net_price = 0.0
        for order_info in combo_order.leg_orders:
            fill_price = order_info.avg_fill_price
            quantity = order_info.filled_quantity

            if order_info.action in ("BUY", "COMBO"):
                net_price += fill_price * quantity
            else:
                net_price -= fill_price * quantity

        return net_price

    def _on_exit_triggered(self, position: Position, exit_reason: ExitReason):
        """Callback when position exit is triggered"""

        # Prevent duplicate exit attempts
        if self._exit_in_progress:
            self.logger.warning(f"Exit already in progress - skipping duplicate exit trigger")
            return

        # Check if we've exceeded max exit attempts
        if self._exit_attempt_count >= self._max_exit_attempts:
            self.logger.error(f"Max exit attempts ({self._max_exit_attempts}) reached - manual intervention required!")
            self.logger.error("Please close the position manually in TWS")
            return

        self._exit_in_progress = True
        self._exit_attempt_count += 1

        # Log exit trigger - only show attempt count on retries
        self.logger.info("")
        self.logger.info("")
        self.logger.info("*" * 70)
        self.logger.info(f"***   EXIT TRIGGERED: {exit_reason.value}   ***")
        if self._exit_attempt_count > 1:
            self.logger.info(f"***   Retry attempt {self._exit_attempt_count}/{self._max_exit_attempts}   ***")
        self.logger.info("*" * 70)
        self.logger.info("")

        result = self._execute_exit(position, exit_reason)

        if result["success"]:
            # Exit executed successfully
            underlying_price = self.broker.get_latest_price()

            # Apply correct sign to exit spread to match entry spread convention
            # Entry: credit = negative (received), debit = positive (paid)
            # Exit: credit = negative (cost to close), debit = positive (value received)
            # IBKR returns positive fill price, we need to negate for credit spreads
            exit_fill_price = result["fill_price"]
            if position.is_credit:
                exit_fill_price = -abs(exit_fill_price)  # Cost to close as negative

            closed_position = self.position_manager.close_position(
                exit_spread=exit_fill_price,
                exit_reason=exit_reason,
                underlying_price=underlying_price
            )

            # Calculate duration
            duration_minutes = None
            entry_time_str = None
            exit_time_str = None
            if closed_position.entry_time and closed_position.exit_time:
                duration = closed_position.exit_time - closed_position.entry_time
                duration_minutes = int(duration.total_seconds() / 60)
                entry_time_str = closed_position.entry_time.strftime("%H:%M")
                exit_time_str = closed_position.exit_time.strftime("%H:%M")

            # Simple trader-friendly exit log (consistent with bracket order mode)
            self.logger.log_trade_closed_simple(
                exit_reason=exit_reason.value,
                entry_spread=closed_position.entry_spread,
                exit_spread=closed_position.exit_spread,
                realized_pnl=closed_position.realized_pnl,
                contracts=closed_position.contracts,
                target_spread=closed_position.target_spread,
                stop_spread=closed_position.stop_spread,
                entry_time=entry_time_str,
                exit_time=exit_time_str,
                duration_minutes=duration_minutes
            )

            # Verify OUR position legs are closed on IBKR (not other manual positions)
            import time
            time.sleep(0.5)  # Allow IBKR to update
            spx_positions = [p for p in self.broker.ib.positions() if p.contract.symbol == 'SPX']

            # Get our position's leg strikes
            our_leg_strikes = set()
            if closed_position.built_strategy:
                for leg in closed_position.built_strategy.legs:
                    our_leg_strikes.add((leg.strike, leg.option_type.value))

            # Check only our specific legs
            our_remaining_legs = []
            other_positions_count = 0
            for pos in spx_positions:
                leg_key = (pos.contract.strike, pos.contract.right)
                if leg_key in our_leg_strikes:
                    our_remaining_legs.append(pos)
                else:
                    other_positions_count += 1

            if not our_remaining_legs:
                self.logger.info("✓ POSITION VERIFIED CLOSED ON IBKR")
                if other_positions_count > 0:
                    self.logger.info(f"  ℹ️ Note: {other_positions_count} other SPX position(s) exist (not from this system)")
            else:
                self.logger.warning("=" * 50)
                self.logger.warning("⚠️ POSITION VERIFICATION FAILED")
                self.logger.warning("=" * 50)
                self.logger.warning(f"   {len(our_remaining_legs)} of OUR position leg(s) still showing on IBKR")
                for pos in our_remaining_legs:
                    self.logger.warning(f"   - {pos.contract.strike} {pos.contract.right} qty={pos.position}")
                self.logger.warning("")
                self.logger.warning("   ACTION REQUIRED: Check TWS to verify position is closed")
                self.logger.warning("=" * 50)

            # Build trade data from closed_position for CSV logging
            # Get expiry from built strategy if available
            expiry = ""
            if closed_position.built_strategy and closed_position.built_strategy.legs:
                first_leg = closed_position.built_strategy.legs[0]
                if hasattr(first_leg, 'contract') and hasattr(first_leg.contract, 'expiry'):
                    expiry = first_leg.contract.expiry

            trade_data = {
                "position_id": closed_position.position_id,
                "strategy_id": closed_position.strategy_id,
                "direction_bias": closed_position.direction_bias,
                "vol_regime": closed_position.vol_regime,
                "trend_regime": closed_position.trend_regime,
                "expiry": expiry,
                "legs": closed_position.leg_details,
                "entry_spread": closed_position.entry_spread,
                "exit_spread": closed_position.exit_spread,
                "target_spread": closed_position.target_spread,
                "stop_spread": closed_position.stop_spread,
                "exit_reason": exit_reason.value,
                "contracts": closed_position.contracts,
                "pnl_per_contract": closed_position.realized_pnl / closed_position.contracts if closed_position.contracts > 0 else 0,
                "total_pnl": closed_position.realized_pnl,
                "entry_time": closed_position.entry_time.strftime("%Y-%m-%d %H:%M:%S"),
                "exit_time": closed_position.exit_time.strftime("%Y-%m-%d %H:%M:%S") if closed_position.exit_time else None,
                "underlying_price_entry": closed_position.underlying_price_entry,
                "underlying_price_exit": closed_position.underlying_price_current
            }
            self.logger.log_trade_exit(trade_data)

            self.position_monitor.stop()

            # Clear saved state after position close (crash recovery)
            if self.state_manager:
                self.state_manager.clear_state()

            # Reset time-to-close warnings for next trade
            self._warning_10min_shown = False
            self._warning_5min_shown = False
            self._warning_2min_shown = False

            # Reset exit attempt counter
            self._exit_attempt_count = 0

            # Reset stale price tracking
            self._last_spread_value = None
            self._last_spread_change_time = None

            # Set bar timestamp to prevent immediate re-entry on same bar
            self._last_trade_close_bar = self._get_current_bar_timestamp()
            self.logger.info(f"[POST-TRADE] Will wait for new bar before next entry")

            # Determine next state based on current time
            # If still within breakout window, go back to WAITING_FOR_BREAKOUT for potential re-entry
            # Otherwise, end the session
            old_state = self.state.name
            current_time = datetime.now(US_EASTERN).time()

            # Convert IST config times to ET for comparison with current ET time
            cutoff_time = TimezoneManager.convert_ist_to_eastern(self.config.cutoff_time)
            breakout_start = TimezoneManager.convert_ist_to_eastern(self.config.orb_breakout_start_time)
            breakout_end = TimezoneManager.convert_ist_to_eastern(self.config.orb_breakout_end_time)

            # Check if spans midnight (overnight session)
            spans_midnight = breakout_end < breakout_start

            if spans_midnight:
                in_breakout_window = current_time >= breakout_start or current_time <= breakout_end
            else:
                in_breakout_window = breakout_start <= current_time <= breakout_end

            # Also check cutoff time
            if spans_midnight:
                # For overnight, cutoff is typically after midnight (e.g., 02:00)
                past_cutoff = cutoff_time <= current_time < breakout_start
            else:
                past_cutoff = current_time >= cutoff_time

            # ORB strategy: One trade per session - shutdown after trade completes (SL/Target/Time exit)
            # Do NOT re-enter or monitor for new signals after trade closes
            self.logger.info("")
            self.logger.info("=" * 70)
            self.logger.info("***   ORB TRADE COMPLETED - ENGINE SHUTTING DOWN   ***")
            self.logger.info("=" * 70)
            self.logger.info(f"Exit Reason: {exit_reason.value}")
            self.logger.info("Strategy rule: One trade per ORB session")
            self.logger.info("=" * 70)
            self._safe_state_transition(EngineState.SHUTDOWN, f"Position closed: {exit_reason.value} - ORB trade completed")
            self._running = False  # Stop the main loop

            # Reset exit tracking on successful exit
            self._exit_in_progress = False
            self._exit_attempt_count = 0

        else:
            # Exit failed - allow retry but track attempts
            self._exit_in_progress = False  # Allow retry on next cycle

            error_msg = result.get("error", "Unknown error")
            self.logger.error("=" * 60)
            self.logger.error(f"{position.get_log_tag()} !!! EXIT FAILED !!!")
            self.logger.error("=" * 60)
            self.logger.error(f"{position.get_log_tag()}   ⚠️ POSITION STILL OPEN")
            self.logger.error(f"{position.get_log_tag()}   Position ID: {position.position_id}")
            self.logger.error(f"{position.get_log_tag()}   Exit Reason: {exit_reason.value}")
            self.logger.error(f"{position.get_log_tag()}   Error: {error_msg}")
            self.logger.error(f"{position.get_log_tag()}   Attempt: {self._exit_attempt_count}/{self._max_exit_attempts}")
            self.logger.error("")

            if self._exit_attempt_count >= self._max_exit_attempts:
                # Max attempts reached - now raise critical error
                self.logger.error("  CRITICAL: Max exit attempts reached!")
                self.logger.error("  Position may still be open in IBKR!")
                self.logger.error("  Possible causes:")
                self.logger.error("    - Order timeout (no fill within time limit)")
                self.logger.error("    - IBKR order rejected")
                self.logger.error("    - Market data unavailable for pricing")
                self.logger.error("    - Network or connection issue")
                self.logger.error("")
                self.logger.error("  REQUIRED MANUAL ACTION:")
                self.logger.error("    1. Check IBKR TWS for open positions")
                self.logger.error("    2. Manually close any open positions if needed")
                self.logger.error("  Action: ENGINE SHUTTING DOWN")
                self.logger.error("=" * 60)
                self.logger.log_error_detailed(
                    "EXIT_EXECUTION_FAILED",
                    error_msg,
                    {"position_id": position.position_id, "exit_reason": exit_reason.value}
                )
                raise CriticalError(
                    f"Exit execution failed after {self._max_exit_attempts} attempts: {error_msg}",
                    context={"position_id": position.position_id, "exit_reason": exit_reason.value}
                )
            else:
                # More attempts remaining - log and return to allow retry
                self.logger.warning(f"  Will retry exit on next cycle ({self._max_exit_attempts - self._exit_attempt_count} attempts remaining)")
                self.logger.error("=" * 60)
                # Don't raise error - return to allow retry on next position check cycle
                return

    def _execute_exit(self, position: Position, exit_reason: ExitReason) -> Dict[str, Any]:
        """Execute exit using IBKRWrapper"""
        from datetime import time
        # US_EASTERN already imported at module level from .utils.timezone

        self.logger.info(f"{position.get_log_tag()} Executing exit for {position.position_id}")
        self.logger.info(f"{position.get_log_tag()}   Exit reason: {exit_reason.value}")

        # CRITICAL: Check if market is open BEFORE attempting exit
        # Use is_us_options_market_open() which properly checks US Eastern time
        if not is_us_options_market_open():
            now_et = datetime.now(US_EASTERN)
            self.logger.error("=" * 60)
            self.logger.error(f"{position.get_log_tag()} [MARKET CLOSED] Cannot execute exit")
            self.logger.error("=" * 60)
            self.logger.error(f"{position.get_log_tag()}   Current time: {now_et.strftime('%H:%M:%S')} ET")
            self.logger.error(f"{position.get_log_tag()}   Market hours: 9:30 AM - 4:00 PM ET")
            self.logger.error(f"{position.get_log_tag()}   Exit Reason: {exit_reason.value}")
            self.logger.error(f"{position.get_log_tag()}   Position will remain OPEN")
            self.logger.error("")
            self.logger.error(f"{position.get_log_tag()}   ⚠️ CRITICAL: Position has overnight exposure!")
            if not self._bracket_order_info:
                self.logger.error(f"{position.get_log_tag()}   ⚠️ NO BRACKET ORDERS - Position is UNPROTECTED")
            else:
                self.logger.error(f"{position.get_log_tag()}   Bracket orders may still protect position")
            self.logger.error("")
            self.logger.error(f"{position.get_log_tag()}   ACTION REQUIRED:")
            self.logger.error(f"{position.get_log_tag()}   1. Check TWS for position status")
            self.logger.error(f"{position.get_log_tag()}   2. Manually close position when market opens")
            self.logger.error(f"{position.get_log_tag()}   3. Or monitor bracket orders overnight")
            self.logger.error("=" * 60)
            return {"success": False, "error": "Market closed - cannot execute exit"}

        # CRITICAL: For TIME exits (cutoff/EOD), cancel bracket orders and force MARKET order
        if exit_reason == ExitReason.TIME:
            self.logger.warning("=" * 60)
            self.logger.warning("[TIME EXIT] Cutoff time reached - forcing market exit")
            self.logger.warning("=" * 60)

            # IMPORTANT: Check if position still exists in IBKR
            # Bracket orders (target/SL) may have already closed the position
            all_positions = self.broker.get_positions()
            # Filter for options on our underlying
            ibkr_positions = [p for p in all_positions
                              if p.get('symbol') == self.config.underlying_symbol
                              and p.get('sec_type') == 'OPT'
                              and p.get('position', 0) != 0]
            position_exists_in_ibkr = False

            if ibkr_positions:
                # Check if any of our position legs still exist in IBKR
                for leg in position.built_strategy.legs:
                    for ibkr_pos in ibkr_positions:
                        if (ibkr_pos.get('strike') == leg.strike and
                            ibkr_pos.get('right') == leg.option_type.value and
                            ibkr_pos.get('expiry') == leg.expiry):
                            position_exists_in_ibkr = True
                            break
                    if position_exists_in_ibkr:
                        break

            if not position_exists_in_ibkr:
                self.logger.warning(f"{position.get_log_tag()}   Position already closed in IBKR (likely by bracket order)")
                self.logger.warning(f"{position.get_log_tag()}   Skipping TIME exit - marking position as closed")
                self.logger.warning("=" * 60)

                # Determine exit reason based on P&L (positive = target, negative = stop)
                # Use last known unrealized P&L to determine if bracket was target or stop
                bracket_exit_reason = ExitReason.TARGET if position.unrealized_pnl > 0 else ExitReason.STOP

                self.logger.info(f"{position.get_log_tag()}   Bracket order filled: {bracket_exit_reason.value}")
                self.logger.info(f"{position.get_log_tag()}   Last known P&L: ${position.unrealized_pnl:.2f}")

                # Clear bracket order info since position is closed
                self._bracket_order_info = None

                # Mark position as closed in engine (bracket order filled)
                # Use last known spread and current underlying price
                self.position_manager.close_position(
                    exit_spread=position.current_spread,
                    exit_reason=bracket_exit_reason,
                    underlying_price=position.underlying_price_current
                )

                return {"success": True, "message": "Position already closed by bracket order"}

            self.logger.warning("  Cancelling bracket orders (no longer needed)")
            self.logger.warning("  Will use MARKET orders to close immediately")
            self.logger.warning("  Priority: Exit before market close/expiration")

            # Cancel bracket orders first
            if self._bracket_order_info:
                target_id = self._bracket_order_info.get('target_order_id')
                sl_id = self._bracket_order_info.get('sl_order_id')
                if target_id and sl_id:
                    self.logger.info(f"  Cancelling bracket orders: Target={target_id}, SL={sl_id}")
                    try:
                        self.broker.cancel_bracket_orders(target_id, sl_id)
                        self.logger.info("  Bracket orders cancelled successfully")
                    except Exception as e:
                        self.logger.warning(f"  Failed to cancel brackets: {e} (will proceed anyway)")
                self._bracket_order_info = None
            self.logger.warning("=" * 60)

        # Build closing leg orders (reverse of opening)
        legs = []
        for leg in position.built_strategy.legs:
            action = "SELL" if leg.side == OrderSide.BUY else "BUY"

            # Validate leg has valid current price - NO SILENT FALLBACK
            if leg.current_price <= 0:
                self.logger.error("=" * 60)
                self.logger.error(f"{position.get_log_tag()} [EXIT PRICE INVALID] Cannot build closing order - leg has no valid price")
                self.logger.error("=" * 60)
                self.logger.error(f"{position.get_log_tag()}   Position: {position.position_id}")
                self.logger.error(f"{position.get_log_tag()}   Leg: {leg.option_type.value} strike={leg.strike}")
                self.logger.error(f"{position.get_log_tag()}   current_price: {leg.current_price}")
                self.logger.error(f"{position.get_log_tag()}   entry_price: {leg.entry_price}")
                self.logger.error("  Possible causes:")
                self.logger.error("    - Market data not available for this option")
                self.logger.error("    - Option contract not subscribed in IBKR")
                self.logger.error("    - Network/connectivity issues with IBKR")
                self.logger.error("  Impact: Cannot close position with invalid price")
                self.logger.error("  Action: ENGINE SHUTDOWN - cannot exit position without valid prices")
                self.logger.error("=" * 60)
                raise CriticalError(
                    f"Exit leg price invalid: {leg.option_type.value} strike={leg.strike} current_price={leg.current_price}",
                    context={"position_id": position.position_id, "leg": str(leg), "current_price": leg.current_price}
                )

            price = leg.current_price

            legs.append({
                "contract": leg.contract.contract,
                "action": action,
                "quantity": leg.quantity,
                "price": price
            })

        if not legs:
            return {"success": False, "error": "Failed to build closing leg orders"}

        # FORCE MARKET ORDER for TIME exits (cutoff/EOD) - ignore config
        if exit_reason == ExitReason.TIME:
            self.logger.warning("[TIME EXIT] Forcing MARKET order (ignoring config)")
            return self._execute_market_exit(legs, position)

        # Check config for exit order type: LIMIT (with chasing) or MARKET (with retry)
        exit_order_type = getattr(self.config, 'exit_order_type', 'LIMIT').upper()
        self.logger.info(f"Exit order type from config: {exit_order_type}")

        if exit_order_type == "MARKET":
            # MARKET ORDER MODE: Retry continuously until filled
            return self._execute_market_exit(legs, position)
        else:
            # LIMIT ORDER MODE: Use price chasing (same as entry)
            return self._execute_limit_exit(legs, position)

    def _execute_limit_exit(self, legs: List[Dict], position) -> Dict[str, Any]:
        """Execute exit using LIMIT orders with price chasing (same as entry)."""
        self.logger.info("Using LIMIT exit with price chasing")

        combo_order = self.broker.place_spread_order(
            legs=legs,
            quantity=position.contracts,
            use_market=False,
            slippage=self.config.slippage_per_leg,
            enable_chasing=self.config.enable_price_chasing,
            chase_interval=self.config.chase_interval_seconds,
            max_chase_time=self.config.max_chase_time_seconds,
            max_price_deviation_pct=self.config.max_price_deviation_pct
        )

        filled_order = self.broker.wait_for_spread_fill(combo_order.combo_id, timeout=60.0)

        self._log_exit_order_result(combo_order, filled_order)

        if filled_order and filled_order.status.value == "FILLED":
            fill_price = self._calculate_combo_fill_price(filled_order)
            self.logger.info(f"[EXIT SUCCESS] Fill Price: ${fill_price:.2f}")
            return {"success": True, "fill_price": fill_price, "combo_order": filled_order}
        else:
            # Combo order failed - try leg-by-leg fallback
            self.logger.warning("Combo exit order failed - trying leg-by-leg close as fallback")

            legged_result = self.broker.close_spread_legged(
                legs=legs,
                quantity=position.contracts,
                timeout=60.0
            )

            if legged_result and legged_result.status.value == "FILLED":
                fill_price = self._calculate_combo_fill_price(legged_result)
                self.logger.info(f"[EXIT SUCCESS via leg-by-leg] Fill Price: ${fill_price:.2f}")
                return {"success": True, "fill_price": fill_price, "combo_order": legged_result}
            else:
                error_msg = "Exit order not filled (both combo and leg-by-leg failed)"
                self.logger.error(f"[EXIT FAILED] {error_msg}")
                return {"success": False, "error": error_msg}

    def _execute_market_exit(self, legs: List[Dict], position) -> Dict[str, Any]:
        """Execute exit using MARKET combo orders with continuous retry until filled."""
        self.logger.info("Using MARKET exit with continuous retry")

        retry_interval = getattr(self.config, 'market_exit_retry_interval', 5.0)
        retry_count = 0

        # Log start of MARKET exit retries
        self.logger.info(f"[MARKET EXIT] Starting MARKET order exit (will retry until filled or market closes)")

        # Keep retrying until filled or market closes
        while True:
            retry_count += 1

            # Check if market is still open
            if not is_us_options_market_open():
                self.logger.error("Market closed - cannot complete MARKET exit")
                break

            # Place combo MARKET order
            combo_order = self.broker.place_spread_order(
                legs=legs,
                quantity=position.contracts,
                use_market=True,  # MARKET order
                slippage=0
            )

            filled_order = self.broker.wait_for_spread_fill(combo_order.combo_id, timeout=10.0)  # Short timeout for MARKET - retry faster

            if filled_order and filled_order.status.value == "FILLED":
                fill_price = self._calculate_combo_fill_price(filled_order)
                self.logger.info(f"[EXIT SUCCESS - MARKET] Fill Price: ${fill_price:.2f} (filled on attempt {retry_count})")
                return {"success": True, "fill_price": fill_price, "combo_order": filled_order}

            # Not filled - cancel and retry
            if retry_count == 1:
                # Log first retry
                self.logger.warning(f"MARKET order not filled - will retry every {retry_interval}s until filled")
            # Subsequent retries are silent - avoid log spam
            self.broker.cancel_all_open_orders()
            time.sleep(retry_interval)

        error_msg = f"MARKET exit failed after {retry_count} attempts (market closed)"
        self.logger.error(f"[EXIT FAILED] {error_msg}")
        return {"success": False, "error": error_msg}

    def _log_exit_order_result(self, combo_order, filled_order):
        """Log detailed exit order result."""
        self.logger.info("=" * 60)
        self.logger.info("[EXIT ORDER RESULT FROM BROKER]")
        self.logger.info(f"   Combo ID: {combo_order.combo_id}")
        self.logger.info(f"   Order Status: {filled_order.status.value if filled_order else 'NO RESPONSE'}")
        if filled_order:
            self.logger.info(f"   Legs Submitted: {len(filled_order.leg_orders)}")
            for i, leg in enumerate(filled_order.leg_orders):
                self.logger.info(f"   Leg {i+1}: Order ID={leg.order_id}, Status={leg.status.value}")
            if hasattr(filled_order, 'error_message') and filled_order.error_message:
                self.logger.error(f"   Error Message: {filled_order.error_message}")
        self.logger.info("=" * 60)

    def stop(self):
        """Stop the engine gracefully and immediately. (Issue 12)"""
        self.logger.info("=" * 60)
        self.logger.info("*** ENGINE SHUTDOWN INITIATED ***")
        self.logger.info("=" * 60)

        # Set flags first to stop all loops immediately
        self._running = False
        self._shutdown_event.set()

        # Issue 5, 6: Cancel all pending orders on shutdown
        # BUT: If using bracket orders with open position, DON'T cancel exit orders
        # (IBKR should continue managing Target + SL even after engine stops)
        if self.broker and self.broker.is_connected():
            try:
                use_bracket = getattr(self.config, 'use_bracket_orders', False)
                has_position = self.position_manager and self.position_manager.current_position is not None
                has_bracket_orders = self._bracket_order_info is not None

                if use_bracket and has_position and has_bracket_orders:
                    self.logger.info("Bracket order mode with open position - keeping exit orders active on IBKR")
                    self.logger.info(f"  Target order ID: {self._bracket_order_info.get('target_order_id')}")
                    self.logger.info(f"  SL order ID: {self._bracket_order_info.get('sl_order_id')}")
                    self.logger.info(f"  OCA Group: {self._bracket_order_info.get('oca_group')}")
                    # Only cancel entry orders if any, NOT exit orders
                    # Don't call cancel_all_open_orders()
                else:
                    self.logger.info("Cancelling all open orders on shutdown...")
                    self.broker.cancel_all_open_orders()
            except Exception as e:
                self.logger.warning(f"Error cancelling orders on shutdown: {e}")

        # Reset order tracking
        self._pending_order_id = None
        self._order_retry_count = 0

        self.logger.info("Shutdown flags set - engine will stop after current operation")

    def _handle_end_of_day_reconciliation(self):
        """
        End-of-day reconciliation at market close.

        Verifies:
        1. Position status matches expectations
        2. No orphaned orders on IBKR
        3. State file is in sync
        4. Logs daily summary
        """
        self.logger.info("=" * 60)
        self.logger.info("[END-OF-DAY RECONCILIATION]")
        self.logger.info("=" * 60)

        issues_found = []

        try:
            # 1. Check position status
            has_internal_position = self.position_manager and self.position_manager.current_position is not None

            # Get IBKR positions
            ibkr_positions = {}
            if self.broker and self.broker.is_connected():
                positions = self.broker.request_positions() or {}
                for key, pos_info in positions.items():
                    if (pos_info.sec_type == "OPT" and
                        pos_info.symbol == self.config.underlying_symbol and
                        pos_info.position != 0):
                        ibkr_positions[key] = pos_info

            has_ibkr_position = len(ibkr_positions) > 0

            self.logger.info(f"  Internal position: {'YES' if has_internal_position else 'NO'}")
            self.logger.info(f"  IBKR positions: {len(ibkr_positions)} legs")

            # Check for mismatch
            if has_internal_position and not has_ibkr_position:
                issues_found.append("MISMATCH: Internal position exists but no IBKR positions")
                self.logger.warning("  [WARNING] Internal position exists but no IBKR positions!")
                self.logger.warning("  Position may have been closed externally")
                # Clear internal position
                if self.position_manager:
                    self.position_manager.current_position = None
                if self.state_manager:
                    self.state_manager.clear_state()
                    self.logger.info("  State file cleared")

            elif not has_internal_position and has_ibkr_position:
                issues_found.append(f"MISMATCH: No internal position but {len(ibkr_positions)} IBKR legs found")
                self.logger.warning("  [WARNING] No internal position but IBKR has positions!")
                for key, pos in ibkr_positions.items():
                    self.logger.warning(f"    - {pos.symbol} {pos.strike}{pos.right} x{pos.position}")
                self.logger.warning("  These may be orphaned positions from previous session")

            elif has_internal_position and has_ibkr_position:
                # Position still open at EOD - CRITICAL ALERT
                position = self.position_manager.current_position

                self.logger.error("")
                self.logger.error("!" * 60)
                self.logger.error("!!! CRITICAL ALERT: POSITION OPEN OVERNIGHT !!!")
                self.logger.error("!" * 60)
                self.logger.error(f"{position.get_log_tag()} Position: {position.position_id}")
                self.logger.error(f"{position.get_log_tag()}   Entry: ${abs(position.entry_spread):.2f}")
                self.logger.error(f"{position.get_log_tag()}   Current: ${abs(position.current_spread):.2f}")
                self.logger.error(f"{position.get_log_tag()}   Unrealized P&L: ${position.unrealized_pnl:+.2f}")

                if not self._bracket_order_info:
                    self.logger.error(f"{position.get_log_tag()}   ⚠️ NO BRACKET ORDERS - UNPROTECTED!")
                    issues_found.append("Position open at EOD without bracket orders")
                else:
                    self.logger.error(f"{position.get_log_tag()}   ✓ Bracket orders active in IBKR")

                self.logger.error("")
                self.logger.error("   ACTION REQUIRED:")
                self.logger.error("   1. Check TWS for position status")
                self.logger.error("   2. Manually close position when market opens")
                if self._bracket_order_info:
                    self.logger.error("   3. Or let bracket orders protect overnight")
                else:
                    self.logger.error("   3. Position has NO automated protection!")
                self.logger.error("!" * 60)
                self.logger.error("")

            else:
                self.logger.info("  [OK] No positions - clean slate")

            # 2. Check for orphaned orders
            if self.broker and self.broker.is_connected():
                open_orders = self.broker.ib.openOrders() if hasattr(self.broker, 'ib') else []

                # Filter for our symbol
                our_orders = []
                for order in open_orders:
                    trade = self.broker.ib.trades()
                    # Check if any open orders for our underlying
                    for t in self.broker.ib.openTrades():
                        contract = t.contract
                        if hasattr(contract, 'symbol') and contract.symbol == self.config.underlying_symbol:
                            our_orders.append(t)

                if our_orders:
                    # Deduplicate orders by order_id (same order can appear multiple times)
                    unique_orders = {}
                    for t in our_orders:
                        order_id = t.order.orderId
                        if order_id not in unique_orders:
                            unique_orders[order_id] = t

                    self.logger.info(f"  Open orders on IBKR: {len(unique_orders)} unique orders")
                    for order_id, t in unique_orders.items():
                        status = t.orderStatus.status
                        # Check if this is a known bracket order
                        is_bracket = False
                        if self._bracket_order_info:
                            if order_id in [self._bracket_order_info.get('target_order_id'),
                                          self._bracket_order_info.get('sl_order_id')]:
                                is_bracket = True

                        if is_bracket:
                            self.logger.info(f"    Order {order_id}: {status} (bracket order - expected)")
                        else:
                            self.logger.warning(f"    Order {order_id}: {status} (ORPHANED?)")
                            issues_found.append(f"Potential orphaned order: {order_id}")
                else:
                    self.logger.info("  Open orders: None")

            # 3. Verify state file consistency
            if self.state_manager:
                saved_state = self.state_manager.load_state()
                if saved_state and not has_internal_position:
                    self.logger.warning("  [WARNING] State file exists but no internal position")
                    self.logger.warning("  Clearing stale state file...")
                    self.state_manager.clear_state()
                    issues_found.append("Stale state file cleared")
                elif not saved_state and has_internal_position:
                    self.logger.warning("  [WARNING] Internal position exists but no state file")
                    issues_found.append("Position exists without state file")
                elif saved_state and has_internal_position:
                    self.logger.info("  State file: In sync with internal position")
                else:
                    self.logger.info("  State file: None (expected)")

            # 4. Summary
            self.logger.info("-" * 60)
            if issues_found:
                self.logger.warning(f"  EOD Reconciliation: {len(issues_found)} issue(s) found")
                for i, issue in enumerate(issues_found, 1):
                    self.logger.warning(f"    {i}. {issue}")
            else:
                self.logger.info("  EOD Reconciliation: All checks passed")
            self.logger.info("=" * 60)

        except Exception as e:
            self.logger.error(f"  EOD Reconciliation error: {e}")
            self.logger.info("=" * 60)

    def _cleanup(self):
        """Cleanup resources"""
        self.logger.info("=" * 60)
        self.logger.info("*** ENGINE CLEANUP ***")
        self.logger.info("=" * 60)

        # Reset order tracking
        self._reset_order_tracking()
        self.logger.info("   Order tracking reset")

        if self.position_monitor:
            self.position_monitor.stop()
            self.logger.info("   Position monitor stopped")

        if self.broker:
            self.broker.print_status()
            self.broker.disconnect()
            self.logger.info("   IBKR disconnected")

        # Get daily summary from CSV file (persists across engine restarts)
        summary = self.logger.get_daily_summary_from_csv()
        self.logger.log_daily_summary_detailed(summary)

        # Release engine lock
        self._release_engine_lock()
        self.logger.info("   Engine lock released")

        self.logger.info("=" * 60)
        self.logger.info("*** ENGINE SHUTDOWN COMPLETE ***")
        self.logger.info("=" * 60)
        self._safe_state_transition(EngineState.SHUTDOWN, "Engine cleanup complete")

    def get_status(self) -> Dict[str, Any]:
        """Get engine status"""
        status = {
            "state": self.state.name,
            "connected": self.broker.is_connected() if self.broker else False,
            "has_position": self.position_manager.has_open_position() if self.position_manager else False,
            "signal_generated": self.signal_generator.is_signal_generated() if self.signal_generator else False,
            "underlying_price": self.broker.get_latest_price() if self.broker else None,
            "current_time": datetime.now(US_EASTERN).strftime("%H:%M:%S"),
            "broker_status": self.broker.get_status() if self.broker else None
        }

        return status


# ============================================================================
# Main Entry Point
# ============================================================================

def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(description="client Options Trading Engine")
    parser.add_argument(
        "--config",
        default="config",
        help="Configuration directory path"
    )
    args = parser.parse_args()

    engine = TradingEngine(config_dir=args.config)

    # Setup basic logging for main() before engine initializes
    import logging
    main_logger = logging.getLogger("clientMain")
    main_logger.setLevel(logging.INFO)
    if not main_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s - %(message)s", "%H:%M:%S"))
        main_logger.addHandler(handler)

    try:
        if not engine.initialize():
            main_logger.error("Initialization failed")
            return 1

        if not engine.connect():
            main_logger.error("Connection failed")
            return 1

        engine.run()

    except KeyboardInterrupt:
        main_logger.info("Shutdown requested...")
        engine.stop()

    return 0


if __name__ == "__main__":
    exit(main())
