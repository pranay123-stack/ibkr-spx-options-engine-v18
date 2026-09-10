"""
Strategy Builder - Building Option Strategies from Templates

Handles:
- Building concrete strategies from templates
- Delta-based leg selection

Author: client Options Trading Engine
"""

from typing import List, Dict

from ...broker import IBKRBroker
from ...utils.logging import TradingLogger
from ...utils.config import EngineConfig, StrategyTemplate
from ...enums import OptionType, OrderSide
from ...utils.exceptions import (
    StrategyBuildError, LegBuildError, NoOptionFoundError
)
from ...data_classes import BuiltLeg, BuiltStrategy


class StrategyBuilder:
    """
    Builds concrete option structures from strategy templates.
    Uses delta-based leg selection to find appropriate strikes.
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

    def build_strategy(
        self,
        template: StrategyTemplate,
        underlying_price: float
    ) -> BuiltStrategy:
        """Build a concrete strategy from a template."""
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
            # Track reference strike for spread width constraint
            reference_strikes: Dict[OptionType, float] = {}

            for leg_def in template.legs:
                try:
                    exclude_list = used_strikes.get(leg_def.option_type, [])
                    ref_strike = reference_strikes.get(leg_def.option_type)

                    self.logger.debug(f"Building leg {leg_def.leg_index} ({leg_def.option_type.value}), excluding strikes: {exclude_list}, ref_strike: {ref_strike}")

                    built_leg = self._build_leg(
                        leg_def,
                        underlying_price,
                        exclude_strikes=exclude_list,
                        reference_strike=ref_strike,
                        max_spread_width=self.config.max_spread_width_points
                    )

                    # Track this strike so subsequent legs of same type use different strike
                    used_strikes[leg_def.option_type].append(built_leg.strike)
                    # First leg becomes reference for spread width
                    if ref_strike is None:
                        reference_strikes[leg_def.option_type] = built_leg.strike

                    self.logger.debug(f"Leg {leg_def.leg_index} selected strike {built_leg.strike}, updated exclusion list: {used_strikes[leg_def.option_type]}")

                    built.legs.append(built_leg)
                except (NoOptionFoundError, LegBuildError) as e:
                    raise StrategyBuildError(
                        strategy_id=template.strategy_id,
                        reason=f"Failed to build leg {leg_def.leg_index}: {e.message}",
                        context={"leg_index": leg_def.leg_index, "original_error": e.to_dict()}
                    )

            built.entry_spread = built.calculate_spread_value(
                self.config.option_entry_price_field
            )

            self.logger.debug(
                f"Strategy built: {template.strategy_id}, "
                f"{len(built.legs)} legs, Entry spread: {built.entry_spread:.4f}"
            )

            for leg in built.legs:
                self.logger.debug(
                    f"  Leg {leg.leg_index}: {leg.option_type.value} "
                    f"{leg.strike} {leg.side.value} "
                    f"(Delta: {leg.target_delta:.2f} -> {round(leg.actual_delta, 2) if leg.actual_delta is not None else 'N/A'})"
                )

        except StrategyBuildError:
            raise
        except Exception as e:
            built.error = str(e)
            self.logger.error(f"Error building strategy: {e}")
            raise StrategyBuildError(
                strategy_id=template.strategy_id,
                reason=str(e),
                context={"underlying_price": underlying_price}
            )

        return built

    def _build_leg(
        self,
        leg_def,
        underlying_price: float,
        exclude_strikes: List[float] = None,
        reference_strike: float = None,
        max_spread_width: float = None
    ) -> BuiltLeg:
        """Build a single leg by finding the appropriate option contract.

        Args:
            leg_def: Leg definition from template
            underlying_price: Current underlying price
            exclude_strikes: List of strikes to exclude (already used by other legs)
            reference_strike: First leg's strike (for spread width constraint)
            max_spread_width: Maximum spread width in points (e.g., 25.0)
        """
        if exclude_strikes is None:
            exclude_strikes = []

        # find_option_by_delta now raises NoOptionFoundError if not found
        option = self.broker.find_option_by_delta(
            symbol=self.config.underlying_symbol,
            underlying_price=underlying_price,
            option_type=leg_def.option_type,
            target_delta=leg_def.target_delta_abs,
            expiry_rule=leg_def.expiry_rule,
            exclude_strikes=exclude_strikes,
            reference_strike=reference_strike,
            max_spread_width=max_spread_width
        )

        # Request fresh market data for this option (Issue: cached chain may not have prices)
        self.broker.get_option_quotes([option], timeout=5)

        entry_price = option.get_price(self.config.option_entry_price_field)

        # NO FALLBACK - require real market data for option prices
        if entry_price is None or entry_price <= 0:
            self.logger.error("=" * 60)
            self.logger.error("[OPTION PRICE FAILED] Cannot get option price from IBKR")
            self.logger.error("=" * 60)
            self.logger.error(f"  Strike: {option.strike} {leg_def.option_type.value}")
            self.logger.error("  Possible causes:")
            self.logger.error("    - Option not subscribed for market data")
            self.logger.error("    - Market closed and no quotes available")
            self.logger.error("    - IBKR connection issues")
            self.logger.error("  Action: Leg build will fail - cannot trade without real prices")
            self.logger.error("=" * 60)

        if entry_price is None or entry_price <= 0:
            raise LegBuildError(
                leg_index=leg_def.leg_index,
                option_type=leg_def.option_type.value,
                reason="Invalid entry price",
                context={"strike": option.strike, "price": entry_price}
            )

        slippage = self.config.slippage_per_leg
        if leg_def.side == OrderSide.BUY:
            entry_price += slippage
        else:
            entry_price -= slippage

        entry_price = max(0.01, entry_price)

        return BuiltLeg(
            leg_index=leg_def.leg_index,
            option_type=leg_def.option_type,
            side=leg_def.side,
            target_delta=leg_def.target_delta_abs,
            actual_delta=option.delta if option.delta else leg_def.target_delta_abs,
            strike=option.strike,
            expiry=option.expiry,
            quantity=leg_def.quantity_factor,
            contract=option,
            entry_price=entry_price,
            current_price=entry_price
        )

    def _estimate_option_price(
        self,
        option,
        underlying_price: float,
        option_type,
        target_delta: float
    ) -> float:
        """
        Estimate option price when market data unavailable.
        Uses a simplified approximation based on intrinsic value and estimated time value.
        """
        strike = option.strike

        # Calculate intrinsic value
        if option_type == OptionType.CALL:
            intrinsic = max(0, underlying_price - strike)
        else:
            intrinsic = max(0, strike - underlying_price)

        # Estimate time value based on ATM approximation and delta
        # ATM options have highest time value, OTM options have less
        # For 0-DTE, assume ~1% of underlying for ATM, scaled by delta
        atm_time_value = underlying_price * 0.01  # ~1% of underlying for 0-DTE ATM

        # Delta-based time value scaling: ATM (delta ~0.5) has full TV, OTM has less
        delta_factor = abs(target_delta) * 2  # Scale: 0.5 delta = 1.0, 0.1 delta = 0.2
        delta_factor = min(1.0, delta_factor)  # Cap at 1.0

        time_value = atm_time_value * delta_factor

        estimated_price = intrinsic + time_value

        # Ensure minimum price
        return max(0.05, round(estimated_price, 2))

    def refresh_strategy_prices(self, built_strategy: BuiltStrategy) -> bool:
        """Refresh prices for all legs in a built strategy."""
        contracts = [leg.contract for leg in built_strategy.legs]
        success = self.broker.get_option_quotes(contracts)

        for leg in built_strategy.legs:
            leg.current_price = leg.contract.get_price(
                self.config.option_entry_price_field
            )

        return success

    def calculate_position_size(self, built_strategy: BuiltStrategy) -> int:
        """Calculate number of contracts based on max capital."""
        max_loss = built_strategy.get_max_loss()

        if max_loss is None or max_loss <= 0:
            max_loss = abs(built_strategy.entry_spread)

        if max_loss <= 0:
            return 1

        margin_per_contract = max_loss * self.config.contract_size
        max_contracts = int(self.config.max_cap_to_be_used / margin_per_contract)

        return max(1, max_contracts)
