"""
Trend Filter using Moving Averages

Determines trend regime based on:
- MA short vs MA long relationship
- Price vs MA relationship

Author: client Options Trading Engine
"""

from typing import Optional, List
from collections import deque

from ...broker import IBKRBroker, OHLCBar
from ...utils.logging import TradingLogger
from ...utils.config import EngineConfig
from ...enums import DirectionBias, TrendRegime, TrendMode
from ...data_classes import TrendState


class TrendFilter:
    """
    Trend Filter using Moving Averages.

    Determines trend regime based on:
    - MA short vs MA long relationship
    - Price vs MA relationship
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
        self.state = TrendState()

        # MA lengths
        self.ma_short_length = config.ma_short_length
        self.ma_long_length = config.ma_long_length

        # Price buffer for MA calculation
        self._price_buffer: deque = deque(maxlen=config.ma_long_length + 10)

    def reset(self):
        """Reset trend state for a new trading day"""
        self.state = TrendState()
        self._price_buffer.clear()
        self.logger.debug("Trend filter state reset")

    def calculate_sma(self, prices: List[float], length: int) -> Optional[float]:
        """Calculate Simple Moving Average"""
        if len(prices) < length:
            return None
        return sum(prices[-length:]) / length

    def calculate_ema(self, prices: List[float], length: int) -> Optional[float]:
        """Calculate Exponential Moving Average"""
        if len(prices) < length:
            return None

        multiplier = 2 / (length + 1)
        ema = prices[0]

        for price in prices[1:]:
            ema = (price - ema) * multiplier + ema

        return ema

    def update(self, current_bar: OHLCBar) -> TrendRegime:
        """
        Update trend analysis with new bar.

        Args:
            current_bar: The latest bar

        Returns:
            Current TrendRegime
        """
        if not self.config.use_trend_filter:
            self.state.trend_regime = TrendRegime.SIDEWAYS
            self.state.trend_ok = True
            return TrendRegime.SIDEWAYS

        # Add price to buffer
        self._price_buffer.append(current_bar.close)
        self.state.last_price = current_bar.close

        # Get closes from broker
        closes = self.broker.get_closes()

        if len(closes) < self.ma_long_length:
            self.logger.debug(f"Not enough bars for trend: {len(closes)}/{self.ma_long_length}")
            self.state.trend_regime = TrendRegime.SIDEWAYS
            return TrendRegime.SIDEWAYS

        # Calculate MAs
        self.state.ma_short = self.calculate_sma(closes, self.ma_short_length)
        self.state.ma_long = self.calculate_sma(closes, self.ma_long_length)

        if self.state.ma_short is None or self.state.ma_long is None:
            self.state.trend_regime = TrendRegime.SIDEWAYS
            return TrendRegime.SIDEWAYS

        # Determine trend regime
        current_price = current_bar.close

        if self.state.ma_short > self.state.ma_long:
            # Short MA above long MA indicates uptrend
            if current_price > self.state.ma_short:
                self.state.trend_regime = TrendRegime.UPTREND
            else:
                # Price below short MA but short > long: weakening uptrend
                self.state.trend_regime = TrendRegime.SIDEWAYS
        elif self.state.ma_short < self.state.ma_long:
            # Short MA below long MA indicates downtrend
            if current_price < self.state.ma_short:
                self.state.trend_regime = TrendRegime.DOWNTREND
            else:
                # Price above short MA but short < long: weakening downtrend
                self.state.trend_regime = TrendRegime.SIDEWAYS
        else:
            self.state.trend_regime = TrendRegime.SIDEWAYS

        self.logger.debug(
            f"Trend update: MA_short={self.state.ma_short:.2f}, "
            f"MA_long={self.state.ma_long:.2f}, "
            f"Price={current_price:.2f}, "
            f"Regime={self.state.trend_regime.value}"
        )

        return self.state.trend_regime

    def is_trend_aligned(self, direction_bias: DirectionBias) -> bool:
        """
        Check if trend aligns with direction bias.

        Args:
            direction_bias: DirectionBias from ORB or other signal

        Returns:
            True if trend supports the direction
        """
        if not self.config.use_trend_filter:
            return True

        if self.config.trend_mode == TrendMode.IGNORE_TREND:
            return True

        if direction_bias == DirectionBias.BULLISH:
            return self.state.trend_regime in (TrendRegime.UPTREND, TrendRegime.SIDEWAYS)

        elif direction_bias == DirectionBias.BEARISH:
            return self.state.trend_regime in (TrendRegime.DOWNTREND, TrendRegime.SIDEWAYS)

        elif direction_bias == DirectionBias.NEUTRAL:
            return True

        return False

    def get_trend_ok(self, direction_bias: DirectionBias) -> bool:
        """
        Determine if trading is allowed based on trend.

        Args:
            direction_bias: The direction we want to trade

        Returns:
            True if trend filter allows the trade
        """
        self.state.trend_ok = self.is_trend_aligned(direction_bias)
        return self.state.trend_ok

    def get_state(self) -> TrendState:
        """Get current trend state"""
        return self.state

    def get_trend_regime(self) -> Optional[TrendRegime]:
        """Get current trend regime"""
        return self.state.trend_regime

    def get_ma_values(self) -> tuple:
        """Get MA short and long values"""
        return self.state.ma_short, self.state.ma_long

    def get_status_summary(self) -> dict:
        """Get summary of trend status for logging"""
        return {
            "trend_enabled": self.config.use_trend_filter,
            "trend_mode": self.config.trend_mode.value if self.config.use_trend_filter else None,
            "ma_short": self.state.ma_short,
            "ma_long": self.state.ma_long,
            "ma_short_length": self.ma_short_length,
            "ma_long_length": self.ma_long_length,
            "trend_regime": self.state.trend_regime.value if self.state.trend_regime else None,
            "trend_ok": self.state.trend_ok,
            "last_price": self.state.last_price
        }
