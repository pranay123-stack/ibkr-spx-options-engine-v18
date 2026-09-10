"""
IV History Manager - Historical IV Data Management

Handles:
- IV history storage and retrieval
- Daily IV tracking
- Intraday sample collection
- Historical data loading

Author: client Options Trading Engine
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from collections import deque

from ..utils.logging import TradingLogger
from ..data_classes import IVData
from ..utils.timezone import US_EASTERN


class IVHistoryManager:
    """
    Manages IV historical data for IVP calculation.

    Tracks:
    - Full IV history (timestamped samples)
    - Daily IV values (one per day for IVP)
    - Intraday samples (current day)
    """

    def __init__(
        self,
        logger: TradingLogger,
        ivp_window_days: int = 252
    ):
        self.logger = logger
        self.ivp_window_days = ivp_window_days

        # IV history for percentile calculation
        max_history = ivp_window_days * 10
        self._iv_history: deque = deque(maxlen=max_history)

        # Daily IV samples (one per day for IVP)
        self._daily_iv: Dict[str, float] = {}

        # Intraday IV samples
        self._intraday_samples: List[IVData] = []

    # ============================================================================
    # RECORD OPERATIONS
    # ============================================================================

    def record_iv(
        self,
        iv: float,
        underlying_price: float = 0,
        strike: float = 0,
        option_type: str = "ATM"
    ):
        """Record IV sample for history/IVP calculation."""
        now = datetime.now(US_EASTERN)

        # Create IV data point
        data = IVData(
            timestamp=now,
            iv=iv,
            underlying_price=underlying_price,
            strike=strike,
            option_type=option_type
        )

        # Add to history
        self._iv_history.append((now, data))

        # Add to intraday samples
        self._intraday_samples.append(data)

        # Update daily IV (use latest of the day)
        date_str = now.strftime("%Y%m%d")
        self._daily_iv[date_str] = iv

    def reset_intraday(self):
        """Reset intraday samples for a new trading day."""
        self._intraday_samples.clear()
        self.logger.debug("Intraday IV samples cleared")

    # ============================================================================
    # LOAD OPERATIONS
    # ============================================================================

    def load_historical_iv(self, iv_data: List[Tuple[datetime, float]]):
        """
        Load historical IV data for IVP calculation.

        Args:
            iv_data: List of (datetime, iv) tuples
        """
        for dt, iv in iv_data:
            data = IVData(timestamp=dt, iv=iv, underlying_price=0)
            self._iv_history.append((dt, data))

            date_str = dt.strftime("%Y%m%d")
            if date_str not in self._daily_iv:
                self._daily_iv[date_str] = iv

        self.logger.debug(f"Loaded {len(iv_data)} historical IV data points")

    def load_daily_iv_history(self, daily_data: Dict[str, float]):
        """
        Load daily IV history directly.

        Args:
            daily_data: Dict mapping date strings (YYYYMMDD) to IV values
        """
        self._daily_iv.update(daily_data)
        self.logger.debug(f"Loaded {len(daily_data)} daily IV values")

    # ============================================================================
    # GET OPERATIONS
    # ============================================================================

    def get_iv_history(self, days: Optional[int] = None) -> List[Tuple[datetime, float]]:
        """
        Get IV history.

        Args:
            days: Number of days to look back (None for all)

        Returns:
            List of (timestamp, iv) tuples
        """
        if days is None:
            return [(ts, data.iv) for ts, data in self._iv_history]

        cutoff = datetime.now(US_EASTERN) - timedelta(days=days)
        return [(ts, data.iv) for ts, data in self._iv_history if ts >= cutoff]

    def get_historical_ivs_in_window(self) -> List[Tuple[datetime, IVData]]:
        """Get historical IV data within the IVP window."""
        cutoff = datetime.now(US_EASTERN) - timedelta(days=self.ivp_window_days)
        return [(ts, data) for ts, data in self._iv_history if ts >= cutoff]

    def get_daily_ivs(self) -> Dict[str, float]:
        """Get all daily IV values."""
        return self._daily_iv.copy()

    def get_daily_ivs_for_ivp(self) -> List[float]:
        """
        Get daily IV values for IVP calculation.

        Uses ALL available historical data (not limited to ivp_window_days).
        Excludes today's value to prevent intraday samples from corrupting baseline.

        Returns:
            List of IV values (excluding today)
        """
        today_date = datetime.now(US_EASTERN).strftime("%Y%m%d")

        # Use ALL available historical data, only exclude today
        return [
            iv for date_str, iv in self._daily_iv.items()
            if date_str < today_date
        ]

    def get_intraday_samples(self) -> List[IVData]:
        """Get intraday IV samples."""
        return self._intraday_samples.copy()

    # ============================================================================
    # UTILITY
    # ============================================================================

    def clear_history(self):
        """Clear all IV history."""
        self._iv_history.clear()
        self._daily_iv.clear()
        self._intraday_samples.clear()
        self.logger.info("IV history cleared")

    def get_counts(self) -> Dict[str, int]:
        """Get counts of stored data."""
        return {
            "history_count": len(self._iv_history),
            "daily_count": len(self._daily_iv),
            "intraday_samples": len(self._intraday_samples)
        }
