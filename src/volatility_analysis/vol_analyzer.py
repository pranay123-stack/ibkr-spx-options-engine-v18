"""
Volatility Analyzer - IV and IVP Based Regime Analysis

Uses IVCalculator for core calculations and IBKRBroker for data fetching.

Features:
- ATM IV calculation from options data
- IV Percentile (IVP) calculation
- Volatility regime classification (VOL_1/VOL_2/VOL_3)

Author: client Options Trading Engine
"""

from datetime import datetime, time as dt_time, timedelta
from typing import Optional, List, Tuple
import csv
import os

from ..broker import IBKRBroker
from ..utils.logging import TradingLogger
from ..utils.config import EngineConfig
from ..enums import VolRegime, ExpiryRule
from ..data_classes import IVState
from ..iv_analysis import IVCalculator
from ..utils.timezone import US_EASTERN, IST
from ..utils.exceptions import CriticalError


class VolatilityAnalyzer:
    """
    Volatility Analyzer for IV and IVP based regime determination.

    Uses IVCalculator for core calculations and IBKRBroker for data fetching.

    Features:
    - ATM IV calculation from options data
    - IV Percentile (IVP) calculation
    - Volatility regime classification (VOL_1/VOL_2/VOL_3)
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

        # Initialize IV Calculator with config settings
        self.iv_calculator = IVCalculator(
            logger=logger,
            config=config,
            ivp_window_days=config.ivp_window_days,
            ivp_1_threshold=config.ivp_1_threshold,
            ivp_2_threshold=config.ivp_2_threshold
        )

        # Reference times for IV sampling
        self.iv_reference_times = config.iv_reference_times

        # Track last sample time to avoid duplicate sampling
        self._last_sample_time: Optional[datetime] = None

        # Track if historical IV has been loaded
        self._historical_iv_loaded = False

    def fetch_and_load_historical_iv(self) -> bool:
        """
        Fetch historical IV data from IBKR and load for IVP calculation.

        This should be called once at startup to populate IV history
        for accurate IVP percentile calculation.

        Returns:
            True if historical IV was loaded successfully
        """
        if self._historical_iv_loaded:
            return True

        try:
            self.logger.debug("Fetching historical IV data from IBKR for IVP calculation...")

            # Fetch 1 year of daily IV data
            iv_data_raw = self.broker.get_historical_iv(
                symbol=self.config.underlying_symbol,
                duration="1 Y",
                bar_size="1 day"
            )

            if iv_data_raw and len(iv_data_raw) >= 10:
                # Convert from dict format to tuple format (timestamp, close_iv)
                # The broker returns dicts with keys: timestamp, open, high, low, close
                iv_data = [(bar['timestamp'], bar['close']) for bar in iv_data_raw]

                self.iv_calculator.load_historical_iv(iv_data)
                self._historical_iv_loaded = True
                self.logger.debug(f"Loaded {len(iv_data)} days of historical IV for IVP calculation")

                # Save historical IV to CSV for verification
                self._save_historical_iv_to_csv(iv_data)

                return True
            else:
                self.logger.warning("Insufficient historical IV data - IVP will use estimation")
                return False

        except Exception as e:
            self.logger.warning(f"Could not load historical IV: {e} - IVP will use estimation")
            return False

    def _et_to_ist(self, et_dt: datetime) -> datetime:
        """Convert ET (Eastern Time) datetime to IST (India Standard Time).

        Uses proper ZoneInfo conversion to handle EST/EDT transitions correctly.
        """
        # Ensure the datetime has ET timezone
        if et_dt.tzinfo is None:
            et_dt = et_dt.replace(tzinfo=US_EASTERN)
        else:
            et_dt = et_dt.astimezone(US_EASTERN)

        # Convert to IST
        ist_dt = et_dt.astimezone(IST)
        return ist_dt.replace(tzinfo=None)  # Return naive for CSV compatibility

    def _ist_to_et(self, ist_dt: datetime) -> datetime:
        """Convert IST (India Standard Time) datetime to ET (Eastern Time).

        Uses proper ZoneInfo conversion to handle EST/EDT transitions correctly.
        """
        # Ensure the datetime has IST timezone
        if ist_dt.tzinfo is None:
            ist_dt = ist_dt.replace(tzinfo=IST)
        else:
            ist_dt = ist_dt.astimezone(IST)

        # Convert to ET
        et_dt = ist_dt.astimezone(US_EASTERN)
        return et_dt.replace(tzinfo=None)  # Return naive for comparison

    def _save_historical_iv_to_csv(self, iv_data: List[Tuple[datetime, float]]):
        """Save historical IV data to CSV for verification with both ET and IST timestamps."""
        try:
            csv_path = "historical_iv.csv"
            with open(csv_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["timestamp_et", "timestamp_ist", "iv", "iv_pct"])

                for dt_et, iv in iv_data:
                    # Convert ET to IST
                    dt_ist = self._et_to_ist(dt_et)
                    writer.writerow([
                        dt_et.strftime("%Y-%m-%d %H:%M:%S"),
                        dt_ist.strftime("%Y-%m-%d %H:%M:%S"),
                        f"{iv:.2f}",
                        f"{iv * 100:.2f}%"
                    ])
            self.logger.debug(f"Saved historical IV data to {csv_path} ({len(iv_data)} rows)")
        except Exception as e:
            self.logger.warning(f"Could not save historical IV to CSV: {e}")

    def save_current_iv_to_csv(self, current_iv: float, ivp: float, vol_regime: str):
        """Save current IV and IVP to CSV for verification (append mode).

        Same format as historical_iv.csv so you can compare:
        - Today's IV/IVP -> should match tomorrow's historical_iv.csv
        Includes both ET and IST timestamps.
        """
        try:
            csv_path = "current_iv.csv"

            # Check if file exists to determine if we need header
            file_exists = os.path.exists(csv_path)

            # Get current time in both timezones
            now_et = datetime.now(US_EASTERN)
            now_ist = datetime.now(IST)

            with open(csv_path, "a", newline="") as f:
                writer = csv.writer(f)
                # Write header only if new file
                if not file_exists:
                    writer.writerow(["timestamp_et", "timestamp_ist", "iv", "iv_pct", "ivp"])
                writer.writerow([
                    now_et.strftime("%Y-%m-%d %H:%M:%S"),
                    now_ist.strftime("%Y-%m-%d %H:%M:%S"),
                    f"{current_iv / 100:.2f}",  # Store as decimal (same as historical)
                    f"{current_iv:.2f}%",
                    f"{ivp:.2f}%"
                ])
            self.logger.debug(f"Appended current IV ({current_iv:.2f}%) IVP ({ivp:.2f}%) to {csv_path}")
        except Exception as e:
            self.logger.warning(f"Could not save current IV to CSV: {e}")

    def reset(self):
        """Reset volatility state for a new trading day"""
        self.iv_calculator.reset()
        self._last_sample_time = None
        self.logger.debug("Volatility analyzer state reset")

    def update(self, underlying_price: float) -> Optional[VolRegime]:
        """
        Update volatility analysis.

        Args:
            underlying_price: Current underlying price

        Returns:
            Current VolRegime
        """
        if not self.config.use_ivp:
            # IVP disabled, return default regime
            self.iv_calculator.state.vol_regime = VolRegime.VOL_2
            self.logger.debug("IVP disabled, defaulting to VOL_2 (mid)")
            return VolRegime.VOL_2

        # Check if it's time for an IV sample
        should_sample = self._should_sample_iv()

        if should_sample:
            self._sample_iv(underlying_price)

        # Return current regime (IVCalculator maintains state)
        return self.iv_calculator.get_vol_regime()

    def _should_sample_iv(self) -> bool:
        """Check if we should sample IV now"""
        current_time = datetime.now(US_EASTERN).time()

        # Sample if we don't have any IV yet
        if self.iv_calculator.get_atm_iv() is None:
            return True

        # Check if within 1 minute of any reference time
        for ref_time in self.iv_reference_times:
            ref_dt = datetime.combine(datetime.today(), ref_time)
            current_dt = datetime.combine(datetime.today(), current_time)
            diff = abs((current_dt - ref_dt).total_seconds())

            if diff < 60:  # Within 1 minute
                # Avoid duplicate sampling in same minute
                if self._last_sample_time:
                    time_since_last = (datetime.now(US_EASTERN) - self._last_sample_time).total_seconds()
                    if time_since_last < 60:
                        return False
                return True

        return False

    def _sample_iv(self, underlying_price: float):
        """Sample ATM IV from broker"""
        self.logger.debug(f"Fetching ATM IV for {self.config.underlying_symbol} at ${underlying_price:.2f}...")

        try:
            # Use broker to get ATM IV
            iv = self.broker.get_atm_iv(
                self.config.underlying_symbol,
                underlying_price,
                ExpiryRule.SAME_DAY
            )

            if iv is not None and iv > 0:
                # Update IV Calculator with new data
                self.iv_calculator.update(
                    atm_iv=iv,
                    underlying_price=underlying_price
                )
                self._last_sample_time = datetime.now(US_EASTERN)
                self.logger.debug(f"IV sampled successfully: {iv * 100:.2f}%")

                # Save current IV/IVP to CSV for verification
                ivp = self.iv_calculator.get_ivp()
                vol_regime = self.iv_calculator.get_vol_regime()
                if ivp is not None:
                    self.save_current_iv_to_csv(
                        current_iv=iv * 100,
                        ivp=ivp if ivp else 0,  # IVP is already 0-100
                        vol_regime=vol_regime.value if vol_regime else "N/A"
                    )

            else:
                # NO FALLBACK - IV is critical for strategy selection
                self._log_iv_fetch_failure("IV fetch returned None or 0")

        except Exception as e:
            # NO FALLBACK - IV is critical for strategy selection
            self._log_iv_fetch_failure(f"Error sampling IV: {e}")

    def _log_iv_fetch_failure(self, reason: str):
        """Log IV fetch failure and raise CriticalError for shutdown."""
        self.logger.error("=" * 60)
        self.logger.error("[IV FETCH FAILED] Cannot get Implied Volatility from IBKR")
        self.logger.error("=" * 60)
        self.logger.error(f"  Reason: {reason}")
        self.logger.error("  Possible causes:")
        self.logger.error("    - Option chain not available or not subscribed")
        self.logger.error("    - Market closed and no option quotes available")
        self.logger.error("    - IBKR connection issues")
        self.logger.error("  Impact: Vol regime cannot be determined - strategy selection compromised")
        self.logger.error("  Action: ENGINE SHUTDOWN - cannot trade without IV data")
        self.logger.error("=" * 60)
        raise CriticalError(
            f"IV fetch failed: {reason}",
            context={"reason": reason}
        )

    # ============================================================================
    # GETTERS - Delegate to IVCalculator
    # ============================================================================

    def get_atm_iv(self, underlying_price: float, refresh: bool = False) -> Optional[float]:
        """Get ATM Implied Volatility."""
        if refresh or self.iv_calculator.get_atm_iv() is None:
            self._sample_iv(underlying_price)

        return self.iv_calculator.get_atm_iv()

    def get_ivp(self) -> Optional[float]:
        """Get current IV Percentile"""
        return self.iv_calculator.get_ivp()

    def get_vol_regime(self) -> Optional[VolRegime]:
        """Get current volatility regime"""
        return self.iv_calculator.get_vol_regime()

    def get_state(self) -> IVState:
        """Get current volatility state"""
        return self.iv_calculator.get_state()

    def load_historical_iv(self, iv_data: List[Tuple[datetime, float]]):
        """Load historical IV data for IVP calculation."""
        self.iv_calculator.load_historical_iv(iv_data)

    def get_iv_statistics(self) -> dict:
        """Get IV statistics from history"""
        stats = self.iv_calculator.get_statistics()
        return {
            "count": stats.count,
            "min": stats.min_iv,
            "max": stats.max_iv,
            "mean": stats.mean_iv,
            "current": stats.current_iv
        }

    def get_status_summary(self) -> dict:
        """Get summary of volatility status for logging"""
        return self.iv_calculator.get_status_summary()
