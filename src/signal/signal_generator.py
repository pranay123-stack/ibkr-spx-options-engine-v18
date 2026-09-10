"""
Signal Generator

Combines multiple indicators to generate trading signals.

Determines final direction based on configured direction_source:
- orb_only: Use ORB direction only
- trend_only: Use trend direction only
- orb_and_trend_agree: Only signal when ORB and trend agree
- within_range: Use neutral when price is within ORB range

Author: client Options Trading Engine
"""

from datetime import datetime
from typing import Optional, Dict, Any

from ..broker import IBKRBroker, OHLCBar
from ..utils.logging import TradingLogger
from ..utils.config import EngineConfig
from ..enums import DirectionBias, TrendRegime, VolRegime, DirectionSource
from ..data_classes import Signal
from ..utils.timezone import US_EASTERN

from ..technical_analysis.orb import OpeningRangeBreakout
from ..technical_analysis.trend import TrendFilter


class SignalGenerator:
    """
    Combines multiple indicators to generate trading signals.

    Determines final direction based on configured direction_source:
    - orb_only: Use ORB direction only
    - trend_only: Use trend direction only
    - orb_and_trend_agree: Only signal when ORB and trend agree
    - within_range: Use neutral when price is within ORB range
    """

    def __init__(
        self,
        config: EngineConfig,
        broker: IBKRBroker,
        orb: OpeningRangeBreakout,
        trend: TrendFilter,
        volatility,  # VolatilityAnalyzer - imported later to avoid circular
        logger: TradingLogger
    ):
        self.config = config
        self.broker = broker
        self.orb = orb
        self.trend = trend
        self.volatility = volatility
        self.logger = logger

        # Last signal
        self._last_signal: Optional[Signal] = None

        # Signal generated flag (only one signal per day)
        self._signal_generated = False

    def reset(self):
        """Reset signal generator for new trading day"""
        self._last_signal = None
        self._signal_generated = False
        self.orb.reset()
        self.trend.reset()
        self.volatility.reset()
        self.logger.debug("Signal generator reset")

    def update(self, current_bar: OHLCBar) -> Optional[Signal]:
        """
        Update all indicators and generate signal if conditions are met.

        Args:
            current_bar: The latest bar

        Returns:
            Signal if conditions met, None otherwise
        """
        if self._signal_generated:
            # Only one entry signal per day
            return self._last_signal

        underlying_price = current_bar.close

        # Update indicators
        orb_direction = self.orb.update(current_bar)
        trend_regime = self.trend.update(current_bar)
        vol_regime = self.volatility.update(underlying_price)

        # Check if we have a valid signal
        signal = self._evaluate_signal(
            current_bar,
            orb_direction,
            trend_regime,
            vol_regime
        )

        if signal and signal.signal_ok:
            self._last_signal = signal
            self._signal_generated = True
            # Note: Signal CSV logging moved to engine.py after strategy selection

        return signal

    def _evaluate_signal(
        self,
        current_bar: OHLCBar,
        orb_direction: Optional[DirectionBias],
        trend_regime: Optional[TrendRegime],
        vol_regime: Optional[VolRegime]
    ) -> Optional[Signal]:
        """
        Evaluate conditions and create signal.
        """
        # Get indicator states
        orb_state = self.orb.get_state()
        trend_state = self.trend.get_state()
        vol_state = self.volatility.get_state()

        # Build base signal
        signal = Signal(
            timestamp=current_bar.timestamp,
            direction_bias=None,
            vol_regime=vol_regime,
            trend_regime=trend_regime,
            signal_ok=False,
            reason="",
            underlying_price=current_bar.close,
            or_high=orb_state.or_high,
            or_low=orb_state.or_low,
            ma_short=trend_state.ma_short,
            ma_long=trend_state.ma_long,
            iv_atm=vol_state.atm_iv,
            ivp=vol_state.ivp
        )

        # Check ORB is complete
        if self.config.use_orb and not orb_state.orb_complete:
            signal.reason = "Waiting for ORB to complete"
            return signal

        # Check for breakout
        if self.config.use_orb and not orb_state.breakout_detected:
            signal.reason = "Waiting for ORB breakout"
            return signal

        # Determine final direction based on direction_source
        final_direction = self._determine_direction(
            orb_direction,
            trend_regime
        )

        if final_direction is None:
            signal.reason = "No valid direction signal"
            return signal

        signal.direction_bias = final_direction

        # Check trend alignment
        if self.config.use_trend_filter:
            if not self.trend.get_trend_ok(final_direction):
                signal.reason = f"Trend not aligned: {trend_regime} vs {final_direction}"
                return signal

        # Check volatility regime is set
        if self.config.use_ivp and vol_regime is None:
            signal.reason = "Volatility regime not determined"
            return signal

        # All conditions met
        signal.signal_ok = True
        signal.reason = "All conditions met"

        self.logger.debug(
            f"Signal generated: Direction={final_direction.value}, "
            f"Vol={vol_regime.value if vol_regime else 'N/A'}, "
            f"Trend={trend_regime.value if trend_regime else 'N/A'}"
        )

        return signal

    def _determine_direction(
        self,
        orb_direction: Optional[DirectionBias],
        trend_regime: Optional[TrendRegime]
    ) -> Optional[DirectionBias]:
        """
        Determine final direction based on direction_source config.
        """
        direction_source = self.config.direction_source

        if direction_source == DirectionSource.ORB_ONLY:
            return orb_direction

        elif direction_source == DirectionSource.TREND_ONLY:
            # Convert trend regime to direction
            if trend_regime == TrendRegime.UPTREND:
                return DirectionBias.BULLISH
            elif trend_regime == TrendRegime.DOWNTREND:
                return DirectionBias.BEARISH
            else:
                return DirectionBias.NEUTRAL

        elif direction_source == DirectionSource.ORB_AND_TREND_AGREE:
            # Must agree
            if orb_direction == DirectionBias.BULLISH and trend_regime == TrendRegime.UPTREND:
                return DirectionBias.BULLISH
            elif orb_direction == DirectionBias.BEARISH and trend_regime == TrendRegime.DOWNTREND:
                return DirectionBias.BEARISH
            elif orb_direction == DirectionBias.NEUTRAL:
                return DirectionBias.NEUTRAL
            else:
                # Disagreement - no signal or neutral based on config
                return DirectionBias.NEUTRAL

        elif direction_source == DirectionSource.WITHIN_RANGE:
            # Use neutral if within range, otherwise ORB direction
            return orb_direction

        return orb_direction

    def get_last_signal(self) -> Optional[Signal]:
        """Get the last generated signal"""
        return self._last_signal

    def is_signal_generated(self) -> bool:
        """Check if a signal has been generated today"""
        return self._signal_generated

    def force_signal_check(self, underlying_price: float) -> Optional[Signal]:
        """
        Force a signal check with current market conditions.
        Used for manual intervention or testing.
        """
        # Create a synthetic bar
        bar = OHLCBar(
            timestamp=datetime.now(US_EASTERN),
            open=underlying_price,
            high=underlying_price,
            low=underlying_price,
            close=underlying_price
        )
        return self.update(bar)

    def get_status_summary(self) -> Dict[str, Any]:
        """Get comprehensive status summary"""
        return {
            "signal_generated": self._signal_generated,
            "direction_source": self.config.direction_source.value,
            "orb": self.orb.get_status_summary(),
            "trend": self.trend.get_status_summary(),
            "volatility": self.volatility.get_status_summary(),
            "last_signal": {
                "direction": self._last_signal.direction_bias.value if self._last_signal and self._last_signal.direction_bias else None,
                "vol_regime": self._last_signal.vol_regime.value if self._last_signal and self._last_signal.vol_regime else None,
                "trend_regime": self._last_signal.trend_regime.value if self._last_signal and self._last_signal.trend_regime else None,
                "signal_ok": self._last_signal.signal_ok if self._last_signal else False,
                "reason": self._last_signal.reason if self._last_signal else None
            } if self._last_signal else None
        }
