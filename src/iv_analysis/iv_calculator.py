"""
IV Calculator - Implied Volatility and IVP Calculation

Handles:
- ATM IV calculation from option prices
- IV Percentile (IVP) calculation
- Volatility regime determination
- IV statistics

Author: client Options Trading Engine
"""

from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple
import math

from ..utils.logging import TradingLogger
from ..utils.config import EngineConfig
from ..enums import VolRegime, OptionType
from ..data_classes import IVData, IVState, IVStatistics
from ..utils.timezone import US_EASTERN, IST
from .iv_history import IVHistoryManager


class IVCalculator:
    """
    IV and IVP Calculator

    Handles:
    - ATM IV calculation from option prices
    - IV Percentile (IVP) calculation
    - Volatility regime determination
    - IV statistics

    IV Percentile (IVP):
        IVP = (Number of days IV was lower than current) / Total days * 100

    Volatility Regimes:
        VOL_1 (LOW):  IVP < ivp_1_threshold
        VOL_2 (MID):  ivp_1_threshold <= IVP < ivp_2_threshold
        VOL_3 (HIGH): IVP >= ivp_2_threshold
    """

    def __init__(
        self,
        logger: TradingLogger,
        config: Optional[EngineConfig] = None,
        ivp_window_days: int = 252,
        ivp_1_threshold: float = 40.0,
        ivp_2_threshold: float = 60.0
    ):
        self.logger = logger
        self.config = config

        # IVP parameters
        self.ivp_window_days = config.ivp_window_days if config else ivp_window_days
        self.ivp_1_threshold = config.ivp_1_threshold if config else ivp_1_threshold
        self.ivp_2_threshold = config.ivp_2_threshold if config else ivp_2_threshold

        # State
        self.state = IVState()

        # History manager
        self._history = IVHistoryManager(logger, self.ivp_window_days)

    def reset(self):
        """Reset IV state for a new trading day"""
        self.state = IVState()
        self._history.reset_intraday()
        self.logger.debug("IV calculator state reset")

    # ============================================================================
    # ATM IV CALCULATION
    # ============================================================================

    def calculate_atm_iv(
        self,
        call_iv: Optional[float],
        put_iv: Optional[float],
        method: str = "average"
    ) -> Optional[float]:
        """
        Calculate ATM IV from call and put IVs.

        Args:
            call_iv: Call option implied volatility
            put_iv: Put option implied volatility
            method: Calculation method ('average', 'call', 'put', 'min', 'max')

        Returns:
            ATM IV as decimal (e.g., 0.20 for 20%)
        """
        ivs = []
        if call_iv is not None and call_iv > 0:
            ivs.append(call_iv)
        if put_iv is not None and put_iv > 0:
            ivs.append(put_iv)

        if not ivs:
            return None

        if method == "average":
            return sum(ivs) / len(ivs)
        elif method == "call":
            return call_iv if call_iv and call_iv > 0 else put_iv
        elif method == "put":
            return put_iv if put_iv and put_iv > 0 else call_iv
        elif method == "min":
            return min(ivs)
        elif method == "max":
            return max(ivs)
        else:
            return sum(ivs) / len(ivs)

    def calculate_iv_from_price(
        self,
        option_price: float,
        underlying_price: float,
        strike: float,
        days_to_expiry: float,
        option_type: OptionType,
        risk_free_rate: float = 0.05
    ) -> Optional[float]:
        """
        Calculate implied volatility from option price using Newton-Raphson.

        Args:
            option_price: Market price of the option
            underlying_price: Current underlying price
            strike: Strike price
            days_to_expiry: Days until expiration
            option_type: CALL or PUT
            risk_free_rate: Risk-free interest rate

        Returns:
            Implied volatility as decimal
        """
        if option_price <= 0 or underlying_price <= 0 or strike <= 0:
            return None

        if days_to_expiry <= 0:
            return None

        T = days_to_expiry / 365.0
        S = underlying_price
        K = strike
        r = risk_free_rate
        is_call = option_type == OptionType.CALL

        # Initial guess
        sigma = 0.20

        # Newton-Raphson iteration
        for _ in range(100):
            try:
                d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
                d2 = d1 - sigma * math.sqrt(T)

                # Standard normal CDF
                def norm_cdf(x):
                    return 0.5 * (1 + math.erf(x / math.sqrt(2)))

                # Standard normal PDF
                def norm_pdf(x):
                    return math.exp(-0.5 * x ** 2) / math.sqrt(2 * math.pi)

                if is_call:
                    price = S * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d2)
                else:
                    price = K * math.exp(-r * T) * norm_cdf(-d2) - S * norm_cdf(-d1)

                vega = S * norm_pdf(d1) * math.sqrt(T)

                if vega < 1e-10:
                    break

                diff = option_price - price
                sigma = sigma + diff / vega

                if abs(diff) < 1e-6:
                    break

                if sigma <= 0.001:
                    sigma = 0.001
                if sigma > 5.0:
                    sigma = 5.0

            except (ValueError, ZeroDivisionError, OverflowError):
                break

        if 0.001 < sigma < 5.0:
            return sigma

        return None

    # ============================================================================
    # IV PERCENTILE CALCULATION
    # ============================================================================

    def calculate_ivp(
        self,
        current_iv: float,
        use_daily: bool = True
    ) -> float:
        """
        Calculate IV Percentile.

        IVP = (Number of observations with IV lower than current) / Total observations * 100

        Args:
            current_iv: Current IV value
            use_daily: Use daily IV values (one per day) vs all samples

        Returns:
            IV Percentile (0-100)
        """
        if use_daily:
            historical_ivs = self._history.get_daily_ivs_for_ivp()
        else:
            historical_ivs = [data.iv for _, data in self._history.get_historical_ivs_in_window()]

        if len(historical_ivs) < 5:
            return self._estimate_ivp_from_level(current_iv)

        # Calculate percentile
        count_below = sum(1 for iv in historical_ivs if iv < current_iv)
        ivp = (count_below / len(historical_ivs)) * 100

        return ivp

    def _estimate_ivp_from_level(self, iv: float) -> float:
        """
        Estimate IVP based on typical IV ranges when insufficient history.

        Based on typical SPX IV distribution:
        - IV < 12%: Very low, IVP ~10-20
        - IV 12-15%: Low, IVP ~20-35
        - IV 15-18%: Below average, IVP ~35-45
        - IV 18-22%: Average, IVP ~45-55
        - IV 22-28%: Above average, IVP ~55-70
        - IV 28-35%: High, IVP ~70-85
        - IV > 35%: Very high, IVP ~85-95
        """
        if iv < 0.12:
            return 15.0
        elif iv < 0.15:
            return 30.0
        elif iv < 0.18:
            return 40.0
        elif iv < 0.22:
            return 50.0
        elif iv < 0.28:
            return 65.0
        elif iv < 0.35:
            return 80.0
        else:
            return 90.0

    # ============================================================================
    # VOLATILITY REGIME
    # ============================================================================

    def determine_vol_regime(self, ivp: float) -> VolRegime:
        """
        Determine volatility regime based on IVP thresholds.

        VOL_1 (LOW):  IVP < ivp_1_threshold
        VOL_2 (MID):  ivp_1_threshold <= IVP < ivp_2_threshold
        VOL_3 (HIGH): IVP >= ivp_2_threshold

        Args:
            ivp: IV Percentile (0-100)

        Returns:
            VolRegime enum value
        """
        if ivp < self.ivp_1_threshold:
            return VolRegime.VOL_1
        elif ivp < self.ivp_2_threshold:
            return VolRegime.VOL_2
        else:
            return VolRegime.VOL_3

    def get_regime_description(self, regime: VolRegime) -> str:
        """Get human-readable description of volatility regime"""
        descriptions = {
            VolRegime.VOL_1: "LOW Volatility - Favor credit spreads, wider wings",
            VolRegime.VOL_2: "MEDIUM Volatility - Balanced strategies",
            VolRegime.VOL_3: "HIGH Volatility - Favor debit spreads, tighter risk"
        }
        return descriptions.get(regime, "Unknown regime")

    # ============================================================================
    # UPDATE & RECORD
    # ============================================================================

    def update(
        self,
        atm_iv: Optional[float] = None,
        call_iv: Optional[float] = None,
        put_iv: Optional[float] = None,
        underlying_price: Optional[float] = None
    ) -> IVState:
        """
        Update IV state with new data.

        Args:
            atm_iv: ATM implied volatility (or calculated from call/put)
            call_iv: Call option IV
            put_iv: Put option IV
            underlying_price: Current underlying price

        Returns:
            Updated IVState
        """
        # Calculate ATM IV if not provided
        if atm_iv is None and (call_iv is not None or put_iv is not None):
            atm_iv = self.calculate_atm_iv(call_iv, put_iv)

        if atm_iv is not None and atm_iv > 0:
            self.state.atm_iv = atm_iv
            self.state.call_iv = call_iv
            self.state.put_iv = put_iv
            self.state.underlying_price = underlying_price
            self.state.last_update = datetime.now(US_EASTERN)

            # Calculate IVP
            self.state.ivp = self.calculate_ivp(atm_iv)

            # Determine regime
            self.state.vol_regime = self.determine_vol_regime(self.state.ivp)

            # Record sample
            self._history.record_iv(atm_iv, underlying_price or 0)

            # Log update
            self._log_iv_update()

        return self.state

    def _log_iv_update(self):
        """Log IV update details with calculation explanation"""
        now_et = datetime.now(US_EASTERN)
        now_ist = now_et.astimezone(IST)
        et_str = now_et.strftime('%H:%M:%S')
        ist_str = now_ist.strftime('%H:%M:%S')

        # IV/IVP explanation is now shown at startup via log_all_explanations()

        self.logger.info("-" * 50)
        self.logger.info(f"[IV UPDATE] {et_str} ET / {ist_str} IST")
        self.logger.info(f"   ATM IV: {self.state.atm_iv * 100:.2f}%")
        if self.state.call_iv:
            self.logger.info(f"   Call IV: {self.state.call_iv * 100:.2f}%")
        if self.state.put_iv:
            self.logger.info(f"   Put IV: {self.state.put_iv * 100:.2f}%")
        self.logger.info(f"   IVP: {self.state.ivp:.1f}%")
        self.logger.info(f"   Vol Regime: {self.state.vol_regime.value if self.state.vol_regime else 'N/A'}")
        self.logger.info(f"   Thresholds: VOL_1 < {self.ivp_1_threshold}% < VOL_2 < {self.ivp_2_threshold}% < VOL_3")
        self.logger.info("-" * 50)

    # ============================================================================
    # IV ESTIMATION (FALLBACK)
    # ============================================================================

    def calculate_historical_volatility(
        self,
        prices: List[float],
        period: int = 20,
        annualize: bool = True,
        trading_days: int = 252
    ) -> Optional[float]:
        """
        Calculate Historical Volatility (HV) from price data.

        Uses log returns and standard deviation method.

        Args:
            prices: List of closing prices (oldest to newest)
            period: Number of periods for calculation (default 20)
            annualize: Whether to annualize the result
            trading_days: Trading days per year for annualization

        Returns:
            Historical volatility as decimal (e.g., 0.18 for 18%)
        """
        if len(prices) < period + 1:
            return None

        # Use most recent 'period' prices
        recent_prices = prices[-(period + 1):]

        # Calculate log returns
        log_returns = []
        for i in range(1, len(recent_prices)):
            if recent_prices[i-1] > 0 and recent_prices[i] > 0:
                log_return = math.log(recent_prices[i] / recent_prices[i-1])
                log_returns.append(log_return)

        if len(log_returns) < 2:
            return None

        # Calculate standard deviation of returns
        mean_return = sum(log_returns) / len(log_returns)
        variance = sum((r - mean_return) ** 2 for r in log_returns) / (len(log_returns) - 1)
        std_dev = math.sqrt(variance)

        # Annualize if requested
        if annualize:
            annualization_factor = math.sqrt(trading_days)
            return std_dev * annualization_factor

        return std_dev

    def estimate_iv_fallback(
        self,
        underlying_symbol: str = "SPX",
        underlying_price: float = 0,
        historical_prices: Optional[List[float]] = None
    ) -> float:
        """
        Estimate IV when broker data is unavailable (paper trading).

        Priority:
        1. Calculate from historical prices if available
        2. Fall back to typical IV by symbol

        Args:
            underlying_symbol: Symbol for estimation rules
            underlying_price: Current underlying price
            historical_prices: Optional list of historical closing prices

        Returns:
            Estimated IV as decimal
        """
        estimated_iv = None
        estimation_method = "default"

        # Try to calculate from historical prices first
        if historical_prices and len(historical_prices) >= 21:
            hv = self.calculate_historical_volatility(historical_prices, period=20)
            if hv and 0.05 < hv < 1.0:  # Sanity check: 5% to 100%
                estimated_iv = hv
                estimation_method = "historical_volatility"

        # Fall back to typical IV by symbol
        if estimated_iv is None:
            default_ivs = {
                "SPX": 0.18,
                "SPY": 0.18,
                "QQQ": 0.22,
                "IWM": 0.24,
                "VIX": 0.80,
            }
            estimated_iv = default_ivs.get(underlying_symbol.upper(), 0.20)
            estimation_method = "symbol_default"

        self.logger.warning("-" * 50)
        self.logger.warning("[IV ESTIMATION FALLBACK]")
        self.logger.warning("   Broker IV not available (typical in paper trading)")
        self.logger.warning(f"   Symbol: {underlying_symbol}")
        self.logger.warning(f"   Method: {estimation_method}")
        self.logger.warning(f"   Estimated IV: {estimated_iv * 100:.1f}%")
        if estimation_method == "historical_volatility":
            self.logger.warning(f"   Based on: 20-period historical volatility")
        self.logger.warning("   Note: Live trading will use actual broker IV")
        self.logger.warning("-" * 50)

        return estimated_iv

    # ============================================================================
    # HISTORY DELEGATION
    # ============================================================================

    def load_historical_iv(self, iv_data: List[Tuple[datetime, float]]):
        """Load historical IV data for IVP calculation."""
        self._history.load_historical_iv(iv_data)

    def load_daily_iv_history(self, daily_data: Dict[str, float]):
        """Load daily IV history directly."""
        self._history.load_daily_iv_history(daily_data)

    def get_iv_history(self, days: Optional[int] = None) -> List[Tuple[datetime, float]]:
        """Get IV history."""
        return self._history.get_iv_history(days)

    def clear_history(self):
        """Clear all IV history"""
        self._history.clear_history()

    # ============================================================================
    # STATISTICS
    # ============================================================================

    def get_statistics(self, days: Optional[int] = None) -> IVStatistics:
        """Get IV statistics over a period."""
        days = days or self.ivp_window_days
        history = self._history.get_iv_history(days)

        if not history:
            return IVStatistics(current_iv=self.state.atm_iv)

        ivs = [iv for _, iv in history]

        # Calculate statistics
        count = len(ivs)
        min_iv = min(ivs)
        max_iv = max(ivs)
        mean_iv = sum(ivs) / count

        # Median
        sorted_ivs = sorted(ivs)
        mid = count // 2
        median_iv = sorted_ivs[mid] if count % 2 else (sorted_ivs[mid-1] + sorted_ivs[mid]) / 2

        # Standard deviation
        variance = sum((iv - mean_iv) ** 2 for iv in ivs) / count
        std_iv = variance ** 0.5

        # Current percentile
        if self.state.atm_iv:
            percentile = (sum(1 for iv in ivs if iv < self.state.atm_iv) / count) * 100
        else:
            percentile = 50.0

        return IVStatistics(
            count=count,
            min_iv=min_iv,
            max_iv=max_iv,
            mean_iv=mean_iv,
            median_iv=median_iv,
            std_iv=std_iv,
            current_iv=self.state.atm_iv,
            percentile=percentile
        )

    def get_intraday_stats(self) -> Dict[str, Any]:
        """Get intraday IV statistics"""
        samples = self._history.get_intraday_samples()
        if not samples:
            return {}

        ivs = [s.iv for s in samples]

        return {
            "samples": len(ivs),
            "open_iv": ivs[0] * 100,
            "current_iv": ivs[-1] * 100,
            "high_iv": max(ivs) * 100,
            "low_iv": min(ivs) * 100,
            "avg_iv": (sum(ivs) / len(ivs)) * 100,
            "change": (ivs[-1] - ivs[0]) * 100,
            "change_pct": ((ivs[-1] / ivs[0]) - 1) * 100 if ivs[0] > 0 else 0
        }

    # ============================================================================
    # GETTERS
    # ============================================================================

    def get_atm_iv(self) -> Optional[float]:
        """Get current ATM IV"""
        return self.state.atm_iv

    def get_ivp(self) -> Optional[float]:
        """Get current IV Percentile"""
        return self.state.ivp

    def get_vol_regime(self) -> Optional[VolRegime]:
        """Get current volatility regime"""
        return self.state.vol_regime

    def get_state(self) -> IVState:
        """Get current IV state"""
        return self.state

    def get_status_summary(self) -> Dict[str, Any]:
        """Get summary of IV status for logging"""
        stats = self.get_statistics()
        counts = self._history.get_counts()

        return {
            "atm_iv": self.state.atm_iv,
            "atm_iv_pct": f"{self.state.atm_iv * 100:.2f}%" if self.state.atm_iv else None,
            "ivp": self.state.ivp,
            "vol_regime": self.state.vol_regime.value if self.state.vol_regime else None,
            "ivp_1_threshold": self.ivp_1_threshold,
            "ivp_2_threshold": self.ivp_2_threshold,
            "history_count": counts["history_count"],
            "daily_count": counts["daily_count"],
            "intraday_samples": counts["intraday_samples"],
            "stats": {
                "min": f"{stats.min_iv * 100:.2f}%",
                "max": f"{stats.max_iv * 100:.2f}%",
                "mean": f"{stats.mean_iv * 100:.2f}%",
                "std": f"{stats.std_iv * 100:.2f}%"
            } if stats.count > 0 else None,
            "last_update": self.state.last_update.strftime("%H:%M:%S") if self.state.last_update else None
        }

    def print_status(self):
        """Print IV status to log"""
        summary = self.get_status_summary()
        self.logger.info("=" * 50)
        self.logger.info("[IV CALCULATOR STATUS]")
        for key, value in summary.items():
            if isinstance(value, dict):
                self.logger.info(f"   {key}:")
                for k, v in value.items():
                    self.logger.info(f"      {k}: {v}")
            else:
                self.logger.info(f"   {key}: {value}")
        self.logger.info("=" * 50)
