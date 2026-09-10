"""
Spread Value Tracker

Tracks spread value in real-time.

Author: client Options Trading Engine
"""

from ...broker import IBKRBroker
from ...utils.logging import TradingLogger
from ...utils.config import EngineConfig
from ...utils.exceptions import CriticalError
from ..entry_management import BuiltStrategy


class SpreadValueTracker:
    """Tracks spread value in real-time."""

    def __init__(
        self,
        broker: IBKRBroker,
        config: EngineConfig,
        logger: TradingLogger
    ):
        self.broker = broker
        self.config = config
        self.logger = logger
        self._last_spread: float = 0.0

    def get_current_spread(self, built_strategy: BuiltStrategy) -> float:
        """Get current spread value from broker."""
        contracts = [leg.contract for leg in built_strategy.legs]
        quotes_success = self.broker.get_option_quotes(contracts)

        # Only log quote status occasionally to reduce log spam
        if not quotes_success:
            self.logger.debug(f"Option quotes unavailable (paper trading) - using entry prices")

        # Calculate spread from current prices (not entry prices)
        # Buy legs - Sell legs
        buy_total = 0.0
        sell_total = 0.0
        price_field = self.config.option_entry_price_field

        for leg in built_strategy.legs:
            # Get current price from contract (updated by get_option_quotes)
            price = leg.contract.get_price(price_field)
            # Check for None or invalid price BEFORE trying to format/use it
            if price is None or price <= 0:
                self.logger.debug(f"  Leg {leg.strike} {leg.option_type.value}: quote_price={price}, entry={leg.entry_price:.4f} (INVALID)")
            else:
                self.logger.debug(f"  Leg {leg.strike} {leg.option_type.value}: quote_price={price:.4f}, entry={leg.entry_price:.4f}")
            if price is None or price <= 0:
                # NO FALLBACK - require real market data for spread calculation
                self.logger.error("=" * 60)
                self.logger.error("[SPREAD CALC FAILED] Cannot get option price for P&L calculation")
                self.logger.error("=" * 60)
                self.logger.error(f"  Leg: {leg.strike} {leg.option_type.value}")
                self.logger.error(f"  Entry price: ${leg.entry_price:.2f}")
                self.logger.error("  Possible causes:")
                self.logger.error("    - Option quote not available")
                self.logger.error("    - Market data subscription issue")
                self.logger.error("  Action: ENGINE SHUTDOWN - cannot calculate accurate P&L")
                self.logger.error("=" * 60)
                raise CriticalError(
                    f"Cannot get price for {leg.strike} {leg.option_type.value} - spread calculation failed",
                    context={"strike": leg.strike, "option_type": leg.option_type.value}
                )

            # Update leg's current_price for tracking
            leg.current_price = price

            total_price = price * leg.quantity
            if leg.side.value == "BUY":
                buy_total += total_price
            else:
                sell_total += total_price

        spread_value = buy_total - sell_total

        self._last_spread = spread_value
        return spread_value

    def get_last_spread(self) -> float:
        """Get last calculated spread value."""
        return self._last_spread
