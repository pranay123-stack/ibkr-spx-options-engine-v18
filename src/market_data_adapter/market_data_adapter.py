"""
Market Data Adapter for Indicators

Provides market data interface for indicators.
Uses IBKRBroker for actual data fetching.

Includes market data staleness detection (Issue 9).

Author: client Options Trading Engine
"""

from datetime import datetime, timedelta, time as dt_time
from typing import Optional, List, Callable

from ..broker import IBKRBroker, OHLCBar
from ..utils.config import EngineConfig
from ..utils.logging import TradingLogger
from ..utils.timezone import US_EASTERN
from ..utils.market_calendar import get_market_status


def is_us_options_market_open() -> bool:
    """Check if US options market is open."""
    market_info = get_market_status()
    return market_info.is_open


class MarketDataAdapter:
    """
    Adapter to provide market data interface for indicators.
    Uses IBKRBroker for actual data fetching.

    Includes market data staleness detection (Issue 9).
    """

    def __init__(self, broker: IBKRBroker, config: EngineConfig, logger: TradingLogger):
        self.broker = broker
        self.config = config
        self.logger = logger
        self._bars: List[OHLCBar] = []
        self._bar_callbacks: List[Callable] = []
        self._last_bar_time: Optional[datetime] = None

    def get_latest_price(self) -> float:
        return self.broker.get_latest_price()

    def get_bars(self, count=None):
        if count:
            return self._bars[-count:]
        return self._bars

    def get_closes(self, count=None):
        bars = self.get_bars(count)
        return [bar.close for bar in bars]

    def _convert_ist_to_et_time(self, ist_time: dt_time) -> dt_time:
        """
        Convert IST time to ET time.

        IST is UTC+5:30, ET is UTC-5 (EST) or UTC-4 (EDT).
        Difference: IST - ET = 10:30 (EST) or 9:30 (EDT)

        For simplicity, we use 10:30 hours difference (EST).
        This means: ET time = IST time - 10:30 hours

        Example: 20:00 IST = 09:30 ET (market open)
        """
        try:
            import pytz
            # Use pytz for accurate conversion
            eastern = pytz.timezone('US/Eastern')
            ist = pytz.timezone('Asia/Kolkata')

            # Create a datetime with the IST time for today
            today = datetime.now(ist).date()
            ist_dt = ist.localize(datetime.combine(today, ist_time))

            # Convert to ET
            et_dt = ist_dt.astimezone(eastern)
            return et_dt.time()
        except ImportError:
            # Fallback: IST - 10:30 = ET (approximate for EST)
            ist_dt = datetime.combine(datetime.today(), ist_time)
            et_dt = ist_dt - timedelta(hours=10, minutes=30)
            return et_dt.time()

    def get_bars_in_range(self, start_time, end_time, date=None):
        """
        Get bars within a time range.

        IMPORTANT: The start_time and end_time are in IST (from config).
        IBKR bar timestamps may be in different timezones (CT, ET, etc.)
        depending on IBKR settings. We convert everything to ET for comparison.
        """
        if date is None:
            date = datetime.now(US_EASTERN)

        # Convert IST times to ET for comparison
        start_time_et = self._convert_ist_to_et_time(start_time)
        end_time_et = self._convert_ist_to_et_time(end_time)

        # Get today's date in ET timezone for proper date matching
        et_date = self._get_current_et_time().date()

        matching_bars = []
        for bar in self._bars:
            # Convert bar timestamp to ET for proper comparison
            bar_ts = bar.timestamp
            if bar_ts.tzinfo is not None:
                # Bar has timezone info - convert to ET
                bar_et = bar_ts.astimezone(US_EASTERN)
                bar_time_et = bar_et.time()
                bar_date_et = bar_et.date()
            else:
                # Naive timestamp from IBKR - use configurable offset to convert to ET
                # Default: 1 hour for CT (Central Time), 0 for ET
                offset_hours = self.config.ibkr_bar_timezone_offset_hours
                bar_ts_et = bar_ts + timedelta(hours=offset_hours)
                bar_time_et = bar_ts_et.time()
                bar_date_et = bar_ts_et.date()

            # Use exclusive end time (<) because bar timestamp marks the START of the bar period
            # e.g., a bar at 10:00 represents 10:00-10:05, which is AFTER a window ending at 10:00
            if bar_date_et == et_date and start_time_et <= bar_time_et < end_time_et:
                matching_bars.append(bar)

        self.logger.debug(f"get_bars_in_range: IST {start_time}-{end_time} -> ET {start_time_et}-{end_time_et}")
        self.logger.debug(f"Found {len(matching_bars)} bars in range (from {len(self._bars)} total)")
        return matching_bars

    def get_todays_bars(self):
        """Get bars for today (using ET date for comparison)."""
        et_date = self._get_current_et_time().date()
        todays_bars = []
        for bar in self._bars:
            bar_ts = bar.timestamp
            if bar_ts.tzinfo is not None:
                # Convert to ET for proper date comparison
                bar_date = bar_ts.astimezone(US_EASTERN).date()
            else:
                # Naive timestamp from IBKR - use configurable offset for ET
                offset_hours = self.config.ibkr_bar_timezone_offset_hours
                bar_date = (bar_ts + timedelta(hours=offset_hours)).date()
            if bar_date == et_date:
                todays_bars.append(bar)
        return todays_bars

    def _get_current_et_time(self) -> datetime:
        """
        Get current time in US Eastern timezone.
        IBKR bar timestamps are in ET, so we need to compare against ET time.

        Uses ZoneInfo for proper DST handling (no hardcoded offsets).
        """
        now_et = datetime.now(US_EASTERN)
        # Return naive datetime for comparison with bar timestamps
        return now_et.replace(tzinfo=None)

    def register_bar_callback(self, callback):
        self._bar_callbacks.append(callback)

    def unregister_bar_callback(self, callback):
        if callback in self._bar_callbacks:
            self._bar_callbacks.remove(callback)

    def load_bars(self, bars):
        """Load historical bars with validation. (Issue 7, 8)"""
        # Filter out invalid bars
        valid_bars = []
        for bar in bars:
            if bar and hasattr(bar, 'is_valid'):
                if bar.is_valid():
                    valid_bars.append(bar)
                else:
                    self.logger.debug(f"Skipping invalid bar: {bar}")
            else:
                # For bars without is_valid method, do basic check
                if bar and bar.high > 0 and bar.low > 0:
                    valid_bars.append(bar)

        self._bars = valid_bars

        if valid_bars:
            self._last_bar_time = valid_bars[-1].timestamp

        if len(valid_bars) < len(bars):
            self.logger.warning(f"Filtered {len(bars) - len(valid_bars)} invalid bars during load")

    def add_bar(self, bar: OHLCBar):
        """Add a new bar with validation. (Issue 7, 8, 9)"""
        # Validate the bar
        if hasattr(bar, 'is_valid') and not bar.is_valid():
            self.logger.warning(f"Rejecting invalid bar: {bar}")
            return

        if self._bars and self._bars[-1].timestamp == bar.timestamp:
            self._bars[-1] = bar
        else:
            self._bars.append(bar)
            self._last_bar_time = bar.timestamp

            for callback in self._bar_callbacks:
                try:
                    callback(bar)
                except Exception as e:
                    self.logger.error(f"Bar callback error: {e}")

    def start(self):
        return True

    def stop(self):
        pass
