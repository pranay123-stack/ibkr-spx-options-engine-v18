"""
Opening Range Breakout (ORB) Calculator

Calculates the opening range from the first N minutes of trading
and detects breakouts above/below the range.

Author: client Options Trading Engine
"""

from datetime import datetime, time as dt_time, timedelta
from typing import Optional, Tuple, List

from ...broker import IBKRBroker, OHLCBar
from ...utils.logging import TradingLogger
from ...utils.config import EngineConfig
from ...enums import DirectionBias
from ...data_classes import ORBState
from ...utils.timezone import TimezoneManager, US_EASTERN, IST


class OpeningRangeBreakout:
    """
    Opening Range Breakout (ORB) Calculator.

    Calculates the opening range from the first N minutes of trading
    and detects breakouts above/below the range.

    NOTE: Config times are in IST but bar timestamps from IBKR are in US Eastern.
    This class converts config times to US Eastern for proper comparison.
    """

    def __init__(
        self,
        config: EngineConfig,
        broker: IBKRBroker,
        logger: TradingLogger
    ):
        self.config = config
        self.broker = broker
        self.logger = logger

        # State
        self.state = ORBState()

        # Timing - Store both IST (for adapter calls) and ET (for comparison with bar timestamps)
        # IST times for passing to adapter (which does its own IST->ET conversion)
        self.orb_start_ist = config.orb_start_time
        self.orb_end_ist = TimezoneManager.add_minutes_to_time(config.orb_start_time, config.orb_window_minutes)
        self.breakout_start_ist = config.orb_breakout_start_time
        self.breakout_end_ist = config.orb_breakout_end_time

        # ET times for comparison with IBKR bar timestamps (which are in ET)
        self.orb_start = TimezoneManager.convert_ist_to_eastern(self.orb_start_ist)
        self.orb_end = TimezoneManager.convert_ist_to_eastern(self.orb_end_ist)
        self.breakout_start = TimezoneManager.convert_ist_to_eastern(self.breakout_start_ist)
        self.breakout_end = TimezoneManager.convert_ist_to_eastern(self.breakout_end_ist)

        logger.debug(f"[ORB TIMES] Config IST -> Converted to US Eastern:")
        logger.debug(f"   ORB Start: {self.orb_start_ist} IST -> {self.orb_start} ET")
        logger.debug(f"   ORB End: {self.orb_end_ist} IST -> {self.orb_end} ET")
        logger.debug(f"   Breakout Start: {self.breakout_start_ist} IST -> {self.breakout_start} ET")
        logger.debug(f"   Breakout End: {self.breakout_end_ist} IST -> {self.breakout_end} ET")

    def reset(self):
        """Reset ORB state for a new trading day"""
        self.state = ORBState()
        self.logger.debug("ORB state reset")

    def reset_breakout_for_reentry(self):
        """Reset only the breakout flag to allow re-entry while keeping OR range intact"""
        self.state.breakout_detected = False
        self.state.direction_bias = None
        self.state.breach_time = None
        self.state.breach_price = None
        self.logger.info("ORB breakout flag reset for potential re-entry")

    def _validate_bar(self, bar: OHLCBar) -> bool:
        """
        Validate OHLC bar data integrity. (Issue 7, 8)

        Args:
            bar: The bar to validate

        Returns:
            True if bar is valid, False otherwise
        """
        try:
            # Check for None values
            if bar is None:
                return False

            # Check all OHLC values are present and positive
            if bar.open is None or bar.high is None or bar.low is None or bar.close is None:
                self.logger.warning(f"Bar has None OHLC values: {bar}")
                return False

            if bar.open <= 0 or bar.high <= 0 or bar.low <= 0 or bar.close <= 0:
                self.logger.warning(f"Bar has non-positive OHLC values: O={bar.open}, H={bar.high}, L={bar.low}, C={bar.close}")
                return False

            # Check OHLC consistency: high >= low, high >= open/close, low <= open/close
            if bar.high < bar.low:
                self.logger.warning(f"Invalid bar: high ({bar.high}) < low ({bar.low})")
                return False

            if bar.high < bar.open or bar.high < bar.close:
                self.logger.warning(f"Invalid bar: high ({bar.high}) < open ({bar.open}) or close ({bar.close})")
                return False

            if bar.low > bar.open or bar.low > bar.close:
                self.logger.warning(f"Invalid bar: low ({bar.low}) > open ({bar.open}) or close ({bar.close})")
                return False

            # Check timestamp is valid
            if bar.timestamp is None:
                self.logger.warning("Bar has None timestamp")
                return False

            return True

        except Exception as e:
            self.logger.warning(f"Bar validation error: {e}")
            return False

    def calculate_opening_range(self) -> bool:
        """
        Calculate the opening range from collected bars. (Issue 7)
        Should be called after the ORB window completes.

        Returns True if OR was successfully calculated.
        """
        if self.state.orb_complete:
            return True

        # Get bars within ORB window for today
        # Pass IST times - the adapter handles IST->ET conversion
        today_bars = self.broker.get_bars_in_range(
            self.orb_start_ist,
            self.orb_end_ist
        )

        if not today_bars:
            self.logger.debug("No bars in ORB window yet")
            return False

        # Issue 7: Validate all bars before using
        valid_bars = [bar for bar in today_bars if self._validate_bar(bar)]

        if not valid_bars:
            self.logger.warning(f"No valid bars in ORB window (had {len(today_bars)} invalid bars)")
            return False

        if len(valid_bars) < len(today_bars):
            self.logger.warning(f"Filtered out {len(today_bars) - len(valid_bars)} invalid bars from ORB calculation")

        # Issue 7: Ensure we have enough bars for a meaningful ORB
        min_bars_required = max(1, self.config.orb_window_minutes // self.config.bar_interval_minutes // 2)
        if len(valid_bars) < min_bars_required:
            self.logger.warning(f"Insufficient bars for ORB: have {len(valid_bars)}, need at least {min_bars_required}")
            return False

        # Calculate OR high and low from validated bars
        self.state.orb_bars = valid_bars
        self.state.or_high = max(bar.high for bar in valid_bars)
        self.state.or_low = min(bar.low for bar in valid_bars)

        # Final sanity check on calculated range
        if self.state.or_high <= self.state.or_low:
            self.logger.error(f"Invalid ORB range calculated: high={self.state.or_high}, low={self.state.or_low}")
            return False

        self.state.orb_complete = True

        self.logger.log_orb_calculated(self.state.or_high, self.state.or_low)
        self.logger.info(f"ORB calculated from {len(valid_bars)} valid bars")

        return True

    def check_breakout(self, current_bar: OHLCBar) -> Optional[DirectionBias]:
        """
        Check if current bar breaks out of the opening range.

        Args:
            current_bar: The current/latest bar

        Returns:
            DirectionBias if breakout detected, None otherwise
        """
        if not self.config.use_orb:
            return DirectionBias.NEUTRAL

        if not self.state.orb_complete:
            self.logger.debug("ORB not complete, cannot check breakout")
            return None

        if self.state.breakout_detected:
            return self.state.direction_bias

        # Check if we're in the breakout window
        current_time = current_bar.timestamp.time()

        # Handle overnight session where breakout window spans midnight
        # e.g., breakout_start=20:05, breakout_end=01:30
        breakout_spans_midnight = self.breakout_end < self.breakout_start

        self.logger.debug(f"[BREAKOUT CHECK] current={current_time}, start={self.breakout_start}, end={self.breakout_end}, spans_midnight={breakout_spans_midnight}")

        if breakout_spans_midnight:
            # Overnight session: valid times are either >= start OR <= end
            in_breakout_window = current_time >= self.breakout_start or current_time <= self.breakout_end
            before_window = current_time < self.breakout_start and current_time > self.breakout_end
        else:
            # Normal session: valid times are >= start AND <= end
            in_breakout_window = self.breakout_start <= current_time <= self.breakout_end
            before_window = current_time < self.breakout_start

        self.logger.debug(f"[BREAKOUT CHECK] in_window={in_breakout_window}, before_window={before_window}")

        if before_window:
            self.logger.debug(f"Before breakout window (starts at {self.breakout_start})")
            return None

        if not in_breakout_window:
            # Timeout - no breakout, play neutral
            self.logger.info("ORB breakout window expired - setting NEUTRAL bias")
            self.state.direction_bias = DirectionBias.NEUTRAL
            self.state.breakout_detected = True
            return DirectionBias.NEUTRAL

        # Check for breakout based on bar close
        close = current_bar.close

        if close > self.state.or_high:
            # Bullish breakout
            self.state.direction_bias = DirectionBias.BULLISH
            self.state.breakout_detected = True
            self.state.breach_time = current_bar.timestamp
            self.state.breach_price = close

            self.logger.log_breakout_details(
                direction="BULLISH",
                breach_price=close,
                or_high=self.state.or_high,
                or_low=self.state.or_low,
                breach_time=current_bar.timestamp
            )
            return DirectionBias.BULLISH

        elif close < self.state.or_low:
            # Bearish breakout
            self.state.direction_bias = DirectionBias.BEARISH
            self.state.breakout_detected = True
            self.state.breach_time = current_bar.timestamp
            self.state.breach_price = close

            self.logger.log_breakout_details(
                direction="BEARISH",
                breach_price=close,
                or_high=self.state.or_high,
                or_low=self.state.or_low,
                breach_time=current_bar.timestamp
            )
            return DirectionBias.BEARISH

        # Still within range
        return None

    def update(self, current_bar: OHLCBar) -> Optional[DirectionBias]:
        """
        Main update method to be called on each new bar.

        Args:
            current_bar: The latest bar

        Returns:
            DirectionBias if signal generated, None otherwise
        """
        current_time = current_bar.timestamp.time()

        # Phase 1: Collecting ORB data
        if current_time <= self.orb_end and not self.state.orb_complete:
            self.logger.debug(f"Collecting ORB data: {current_time}")
            return None

        # Phase 2: ORB complete, calculate range
        if not self.state.orb_complete:
            if not self.calculate_opening_range():
                return None

        # Phase 3: Check for breakout
        return self.check_breakout(current_bar)

    def get_state(self) -> ORBState:
        """Get current ORB state"""
        return self.state

    def is_orb_complete(self) -> bool:
        """Check if ORB calculation is complete"""
        return self.state.orb_complete

    def is_breakout_detected(self) -> bool:
        """Check if breakout has been detected"""
        return self.state.breakout_detected

    def get_direction_bias(self) -> Optional[DirectionBias]:
        """Get the current direction bias"""
        return self.state.direction_bias

    def get_or_range(self) -> Tuple[Optional[float], Optional[float]]:
        """Get OR high and low"""
        return self.state.or_high, self.state.or_low

    def get_range_width(self) -> Optional[float]:
        """Get the width of the opening range"""
        if self.state.or_high is not None and self.state.or_low is not None:
            return self.state.or_high - self.state.or_low
        return None

    def get_range_midpoint(self) -> Optional[float]:
        """Get the midpoint of the opening range"""
        if self.state.or_high is not None and self.state.or_low is not None:
            return (self.state.or_high + self.state.or_low) / 2
        return None

    def is_in_breakout_window(self) -> bool:
        """Check if current time is within breakout detection window"""
        current_time = datetime.now(US_EASTERN).time()
        # Handle overnight session where breakout window spans midnight
        # e.g., breakout_start=20:05 ET, breakout_end=01:30 ET
        if self.breakout_end < self.breakout_start:
            # Overnight session: valid times are either >= start OR <= end
            return current_time >= self.breakout_start or current_time <= self.breakout_end
        # Normal session: valid times are >= start AND <= end
        return self.breakout_start <= current_time <= self.breakout_end

    def get_status_summary(self) -> dict:
        """Get summary of ORB status for logging"""
        return {
            "orb_enabled": self.config.use_orb,
            "orb_complete": self.state.orb_complete,
            "or_high": self.state.or_high,
            "or_low": self.state.or_low,
            "range_width": self.get_range_width(),
            "breakout_detected": self.state.breakout_detected,
            "direction_bias": self.state.direction_bias.value if self.state.direction_bias else None,
            "breach_time": self.state.breach_time.strftime("%H:%M:%S") if self.state.breach_time else None,
            "breach_price": self.state.breach_price
        }
