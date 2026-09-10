"""
Strategy Builder Module
Builds concrete option structures from strategy templates
"""

from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime

from ..broker import IBKRBroker, OptionContract
from ..utils.logging import TradingLogger
from ..utils.config import EngineConfig, StrategyTemplate, StrategyLeg
from ..enums import OptionType, OrderSide, ExpiryRule, PriceField
from ..utils.timezone import US_EASTERN
from ..utils.exceptions import CriticalError


@dataclass
class BuiltLeg:
    """A concrete leg with resolved strike and contract"""
    leg_index: int
    option_type: OptionType
    side: OrderSide
    target_delta: float
    actual_delta: Optional[float]
    strike: float
    expiry: str
    quantity: int
    contract: OptionContract

    # Pricing
    entry_price: float = 0.0
    current_price: float = 0.0

    def get_notional_value(self, contract_size: int = 100) -> float:
        """Calculate notional value of this leg"""
        return self.entry_price * self.quantity * contract_size


@dataclass
class BuiltStrategy:
    """A complete built strategy with all legs resolved"""
    strategy_id: str
    legs: List[BuiltLeg] = field(default_factory=list)
    underlying_price: float = 0.0
    entry_spread: float = 0.0
    is_credit: bool = False
    build_time: datetime = None
    error: Optional[str] = None

    def __post_init__(self):
        if self.build_time is None:
            self.build_time = datetime.now(US_EASTERN)

    def calculate_spread_value(self, price_field: PriceField = PriceField.MID) -> float:
        """
        Calculate spread value: Sum(buy) - Sum(sell)
        Credit spreads will be negative, debit spreads positive.
        """
        buy_total = 0.0
        sell_total = 0.0

        for leg in self.legs:
            price = leg.contract.get_price(price_field) * leg.quantity
            if leg.side == OrderSide.BUY:
                buy_total += price
            else:
                sell_total += price

        return buy_total - sell_total

    def get_max_profit(self) -> Optional[float]:
        """Estimate maximum profit (simplified)"""
        if self.is_credit:
            return abs(self.entry_spread)
        else:
            # For debit spreads, max profit depends on structure
            return None

    def get_max_loss(self) -> Optional[float]:
        """Estimate maximum loss (simplified)"""
        # This is a simplification - actual max loss depends on structure
        if self.is_credit:
            # Max loss = width of spread - credit received
            strikes = sorted([leg.strike for leg in self.legs])
            if len(strikes) >= 2:
                width = max(strikes) - min(strikes)
                return width - abs(self.entry_spread)
        else:
            return abs(self.entry_spread)
        return None


class StrategyBuilder:
    """
    Builds concrete option structures from strategy templates.

    Uses delta-based leg selection to find appropriate strikes.
    """

    def __init__(
        self,
        config: EngineConfig,
        ibkr: IBKRBroker,
        logger: TradingLogger
    ):
        self.config = config
        self.ibkr = ibkr
        self.logger = logger

    def build_strategy(
        self,
        template: StrategyTemplate,
        underlying_price: float
    ) -> BuiltStrategy:
        """
        Build a concrete strategy from a template.

        Args:
            template: Strategy template with leg definitions
            underlying_price: Current underlying price

        Returns:
            BuiltStrategy with resolved legs or error
        """
        self.logger.debug(f"Building strategy: {template.strategy_id}")

        built = BuiltStrategy(
            strategy_id=template.strategy_id,
            underlying_price=underlying_price,
            is_credit=template.is_credit_strategy()
        )

        try:
            # Track used strikes to ensure different legs get different strikes
            used_strikes: Dict[OptionType, List[float]] = {
                OptionType.CALL: [],
                OptionType.PUT: []
            }

            # Build each leg
            for leg_def in template.legs:
                exclude_list = used_strikes.get(leg_def.option_type, [])
                self.logger.debug(f"Building leg {leg_def.leg_index} ({leg_def.option_type.value}), excluding strikes: {exclude_list}")

                built_leg = self._build_leg(
                    leg_def,
                    underlying_price,
                    exclude_strikes=exclude_list
                )

                if built_leg is None:
                    built.error = f"Failed to build leg {leg_def.leg_index}"
                    self.logger.error(built.error)
                    return built

                # Track this strike so subsequent legs of same type use different strike
                used_strikes[leg_def.option_type].append(built_leg.strike)
                self.logger.debug(f"Leg {leg_def.leg_index} selected strike {built_leg.strike}, updated exclusion list: {used_strikes[leg_def.option_type]}")
                built.legs.append(built_leg)

            # Request fresh market quotes for all legs to get real prices
            self.logger.debug(f"Requesting quotes for {len(built.legs)} legs")
            self.logger.debug(f"Requesting real-time market quotes for {len(built.legs)} legs...")
            quotes_success = self.refresh_prices(built)
            self.logger.debug(f"Quotes success: {quotes_success}")
            if quotes_success:
                self.logger.debug("Successfully retrieved market quotes for all legs")
            else:
                self.logger.warning("⚠ Some market quotes unavailable - using estimated prices")

            # Calculate entry spread
            built.entry_spread = built.calculate_spread_value(
                self.config.option_entry_price_field
            )

            self.logger.debug(
                f"Strategy built: {template.strategy_id}, "
                f"{len(built.legs)} legs, "
                f"Entry spread: {built.entry_spread:.4f}"
            )

            # Log leg details
            for leg in built.legs:
                self.logger.debug(
                    f"  Leg {leg.leg_index}: {leg.option_type.value} "
                    f"{leg.strike} {leg.side.value} "
                    f"(Delta target: {leg.target_delta:.2f}, "
                    f"actual: {leg.actual_delta:.2f if leg.actual_delta else 'N/A'})"
                )

        except Exception as e:
            built.error = str(e)
            self.logger.error(f"Error building strategy: {e}")

        return built

    def _build_leg(
        self,
        leg_def: StrategyLeg,
        underlying_price: float,
        exclude_strikes: List[float] = None
    ) -> Optional[BuiltLeg]:
        """
        Build a single leg by finding the appropriate option contract.

        Args:
            leg_def: Leg definition from template
            underlying_price: Current underlying price
            exclude_strikes: List of strikes to exclude (already used by other legs)
        """
        if exclude_strikes is None:
            exclude_strikes = []

        # Find option by delta
        try:
            option = self.ibkr.find_option_by_delta(
                symbol=self.config.underlying_symbol,
                underlying_price=underlying_price,
                option_type=leg_def.option_type,
                target_delta=leg_def.target_delta_abs,
                expiry_rule=leg_def.expiry_rule,
                exclude_strikes=exclude_strikes
            )
        except Exception as e:
            self.logger.warning(
                f"Failed to find option for leg {leg_def.leg_index}: "
                f"{leg_def.option_type.value} delta={leg_def.target_delta_abs}. "
                f"Error: {e}"
            )
            return None

        if option is None:
            self.logger.warning(
                f"Could not find option for leg {leg_def.leg_index}: "
                f"{leg_def.option_type.value} delta={leg_def.target_delta_abs}"
            )
            return None

        # Check if option has valid delta (quotes may have failed)
        # In paper trading, delta might not be available - use target delta as estimate
        actual_delta = option.delta
        if actual_delta is None:
            self.logger.warning(
                f"Option for leg {leg_def.leg_index} has no delta data - "
                f"using target delta ({leg_def.target_delta_abs}) as estimate. Strike: {option.strike}"
            )
            actual_delta = leg_def.target_delta_abs
            if leg_def.option_type == OptionType.PUT:
                actual_delta = -actual_delta  # Puts have negative delta

        # Get price for this option
        entry_price = option.get_price(self.config.option_entry_price_field)

        # Apply slippage for sell legs (we get less) and buy legs (we pay more)
        slippage = self.config.slippage_per_leg
        if leg_def.side == OrderSide.BUY:
            entry_price += slippage
        else:
            entry_price -= slippage

        entry_price = max(0.01, entry_price)  # Minimum price

        built_leg = BuiltLeg(
            leg_index=leg_def.leg_index,
            option_type=leg_def.option_type,
            side=leg_def.side,
            target_delta=leg_def.target_delta_abs,
            actual_delta=actual_delta,
            strike=option.strike,
            expiry=option.expiry,
            quantity=leg_def.quantity_factor,
            contract=option,
            entry_price=entry_price,
            current_price=entry_price
        )

        return built_leg

    def refresh_prices(self, built_strategy: BuiltStrategy) -> bool:
        """
        Refresh prices for all legs in a built strategy.

        Returns True if all prices updated successfully.
        """
        contracts = [leg.contract for leg in built_strategy.legs]

        success = self.ibkr.get_option_quotes(contracts)

        for leg in built_strategy.legs:
            # Update current price from fresh market data
            leg.current_price = leg.contract.get_price(
                self.config.option_entry_price_field
            )
            # Also update entry price with fresh market data (with slippage already applied)
            fresh_price = leg.current_price
            slippage = self.config.slippage_per_leg
            if leg.side == OrderSide.BUY:
                leg.entry_price = fresh_price + slippage
            else:
                leg.entry_price = fresh_price - slippage
            leg.entry_price = max(0.01, leg.entry_price)  # Minimum price

        return success

    def calculate_position_size(
        self,
        built_strategy: BuiltStrategy
    ) -> int:
        """
        Calculate number of contracts based on max capital.

        Returns number of contracts to trade.
        """
        # Estimate margin requirement per contract
        # For spreads, margin is typically the max loss
        max_loss = built_strategy.get_max_loss()

        if max_loss is None or max_loss <= 0:
            # NO FALLBACK - require valid max loss for position sizing
            self.logger.error("=" * 60)
            self.logger.error("[POSITION SIZING FAILED] Cannot calculate max loss for strategy")
            self.logger.error("=" * 60)
            self.logger.error(f"  Max loss returned: {max_loss}")
            self.logger.error(f"  Entry spread: {built_strategy.entry_spread}")
            self.logger.error("  Possible causes:")
            self.logger.error("    - Strategy legs not properly built")
            self.logger.error("    - Invalid option prices")
            self.logger.error("  Action: ENGINE SHUTDOWN - cannot size position safely")
            self.logger.error("=" * 60)
            raise CriticalError(
                "Cannot calculate max loss for position sizing - strategy build failed",
                context={"max_loss": max_loss, "entry_spread": built_strategy.entry_spread}
            )

        # Calculate contracts based on max capital
        margin_per_contract = max_loss * self.config.contract_size
        max_contracts = int(self.config.max_cap_to_be_used / margin_per_contract)

        # Minimum 1 contract
        return max(1, max_contracts)

    def get_leg_contracts(self, built_strategy: BuiltStrategy) -> List[Dict[str, Any]]:
        """
        Get contract details for all legs (for order execution).
        """
        leg_details = []

        for leg in built_strategy.legs:
            leg_details.append({
                "leg_index": leg.leg_index,
                "contract": leg.contract.contract,
                "action": "BUY" if leg.side == OrderSide.BUY else "SELL",
                "quantity": leg.quantity,
                "option_type": leg.option_type.value,
                "strike": leg.strike,
                "expiry": leg.expiry
            })

        return leg_details
