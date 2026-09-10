"""
Spread Executor - Order Execution for Option Spreads

Handles:
- Entry execution (opening positions)
- Exit execution (closing positions)
- Order management and fill tracking

Author: client Options Trading Engine
"""

import time
from typing import Optional, List, Dict, Any

from ...broker import IBKRBroker
from ...utils.logging import TradingLogger
from ...utils.config import EngineConfig
from ...enums import OrderSide, OrderStatus, ExitReason
from ...data_classes import BuiltStrategy, ExecutionResult


class SpreadExecutor:
    """
    Executes option spread strategies.

    Handles:
    - Entry execution (opening positions)
    - Exit execution (closing positions)
    - Order management and fill tracking
    """

    def __init__(
        self,
        broker: IBKRBroker,
        config: EngineConfig,
        logger: TradingLogger
    ):
        self.broker = broker
        self.config = config
        self.logger = logger

    def execute_entry(
        self,
        built_strategy: BuiltStrategy,
        contracts: int,
        use_limit: bool = True,
        max_slippage: float = 0.10
    ) -> ExecutionResult:
        """Execute entry for a built strategy."""
        start_time = time.time()

        self.logger.info(f"Executing entry for {built_strategy.strategy_id}")
        self.logger.info(f"Contracts: {contracts}")

        legs = self._build_leg_orders(built_strategy, contracts, is_opening=True)

        if not legs:
            return ExecutionResult(
                success=False,
                combo_order=None,
                fill_price=0,
                error_message="Failed to build leg orders"
            )

        if use_limit:
            limit_price = self._calculate_entry_limit_price(
                built_strategy, max_slippage
            )
        else:
            limit_price = None

        result = self._execute_spread(
            legs=legs,
            contracts=contracts,
            limit_price=limit_price,
            is_entry=True
        )

        result.execution_time = time.time() - start_time

        if result.success:
            self.logger.info(f"Entry executed. Fill: {result.fill_price:.4f}")
        else:
            self.logger.error(f"Entry failed: {result.error_message}")

        return result

    def execute_exit(
        self,
        position,  # Position object
        exit_reason: ExitReason,
        use_market: bool = False
    ) -> ExecutionResult:
        """Execute exit for an open position."""
        start_time = time.time()

        self.logger.info(f"Executing exit for {position.position_id}")
        self.logger.info(f"Exit reason: {exit_reason.value}")

        legs = self._build_leg_orders(
            position.built_strategy,
            position.contracts,
            is_opening=False
        )

        if not legs:
            return ExecutionResult(
                success=False,
                combo_order=None,
                fill_price=0,
                error_message="Failed to build closing leg orders"
            )

        # ALWAYS use LIMIT orders for exits - MARKET combo orders don't fill reliably on IBKR
        # This applies to ALL exit reasons including TIME exits
        use_market = False
        limit_price = self._calculate_exit_limit_price(position.built_strategy)
        self.logger.info(f"Using LIMIT order for exit (calculated limit: {limit_price:.2f})")

        result = self._execute_spread(
            legs=legs,
            contracts=position.contracts,
            limit_price=limit_price,
            is_entry=False,
            use_market=use_market,
            is_credit=position.is_credit  # Pass for proper exit fill_price sign
        )

        result.execution_time = time.time() - start_time

        if result.success:
            self.logger.info(f"Exit executed. Fill: {result.fill_price:.4f}")
        else:
            self.logger.error(f"Exit failed: {result.error_message}")

        return result

    def _build_leg_orders(
        self,
        built_strategy: BuiltStrategy,
        contracts: int,
        is_opening: bool
    ) -> List[Dict[str, Any]]:
        """Build leg order definitions from strategy."""
        legs = []

        for built_leg in built_strategy.legs:
            if is_opening:
                action = "BUY" if built_leg.side == OrderSide.BUY else "SELL"
            else:
                action = "SELL" if built_leg.side == OrderSide.BUY else "BUY"

            price = built_leg.current_price if built_leg.current_price > 0 else built_leg.entry_price

            legs.append({
                "contract": built_leg.contract.contract,
                "action": action,
                "quantity": built_leg.quantity,
                "price": price,
                "leg_index": built_leg.leg_index,
                "strike": built_leg.strike,
                "option_type": built_leg.option_type.value
            })

        return legs

    def _calculate_entry_limit_price(
        self,
        built_strategy: BuiltStrategy,
        max_slippage: float
    ) -> float:
        """Calculate limit price for entry.

        Uses fresh market mid-price (from contract) and adds slippage once.
        Does NOT use leg.entry_price which already has slippage.
        """
        # Get true mid-price from market (no slippage)
        buy_total = 0.0
        sell_total = 0.0
        for leg in built_strategy.legs:
            price = leg.contract.get_price(self.config.option_entry_price_field)
            if leg.side.value == 'BUY':
                buy_total += price * leg.quantity
            else:
                sell_total += price * leg.quantity
        mid_price = buy_total - sell_total

        if built_strategy.is_credit:
            limit_price = mid_price + max_slippage  # Accept less credit (e.g., -5.00 + 0.50 = -4.50)
        else:
            limit_price = mid_price + max_slippage  # Willing to pay more for debit (e.g., 3.00 + 0.50 = 3.50)

        return round(limit_price, 2)

    def _calculate_exit_limit_price(self, built_strategy: BuiltStrategy) -> float:
        """Calculate limit price for exit"""
        current_spread = built_strategy.calculate_spread_value(
            self.config.option_entry_price_field
        )

        slippage = self.config.slippage_per_leg * len(built_strategy.legs)

        if built_strategy.is_credit:
            limit_price = current_spread - slippage  # Pay slightly more to close
        else:
            limit_price = current_spread - slippage  # Accept slightly less to close

        return round(limit_price, 2)

    def _execute_spread(
        self,
        legs: List[Dict[str, Any]],
        contracts: int,
        limit_price: Optional[float],
        is_entry: bool,
        use_market: bool = False,
        is_credit: bool = True  # Original position type (for exit fill_price sign)
    ) -> ExecutionResult:
        """Execute spread order."""
        # Validate contract quantity
        if contracts <= 0:
            error_msg = f"Invalid contract quantity: {contracts}. Must be > 0"
            self.logger.error(error_msg)
            return ExecutionResult(
                success=False,
                error_message=error_msg
            )

        # Use price chasing for both entry and exit orders
        combo_order = self.broker.place_spread_order(
            legs=legs,
            quantity=contracts,
            use_market=use_market,
            slippage=self.config.slippage_per_leg,
            enable_chasing=self.config.enable_price_chasing,
            chase_interval=self.config.chase_interval_seconds,
            max_chase_time=self.config.max_chase_time_seconds,
            max_price_deviation_pct=self.config.max_price_deviation_pct
        )

        filled_combo = self.broker.wait_for_spread_fill(
            combo_order.combo_id,
            timeout=60.0
        )

        if filled_combo and filled_combo.status == OrderStatus.FILLED:
            fill_price = self._calculate_combo_fill_price(filled_combo, is_entry, is_credit)

            return ExecutionResult(
                success=True,
                combo_order=filled_combo,
                fill_price=fill_price
            )
        else:
            # Calculate actual filled quantity (not just leg count)
            filled_legs = combo_order.get_total_filled()
            total_legs = len(combo_order.leg_orders)

            # Get actual contract quantities filled
            # Use min() because a spread is only complete when ALL legs fill
            # e.g., if leg1=5, leg2=3 filled, only 3 complete spreads exist
            total_qty = contracts
            leg_fills = [leg_order.filled_quantity for leg_order in combo_order.leg_orders
                        if leg_order.filled_quantity and leg_order.filled_quantity > 0]
            filled_qty = min(leg_fills) if leg_fills else 0

            # Show both leg status and quantity for clarity
            if filled_qty > 0:
                error_msg = f"Order partially filled: {filled_qty}/{total_qty} contracts ({filled_legs}/{total_legs} legs)"
            else:
                error_msg = f"Order not filled: 0/{total_qty} contracts ({filled_legs}/{total_legs} legs)"

            # Check for "riskless combination" error or other combo rejection on EXIT orders
            # This error occurs when IBKR rejects combo orders for closing positions
            last_error = self.broker.last_error if hasattr(self.broker, 'last_error') else ""
            is_riskless_error = "riskless" in last_error.lower() if last_error else False
            is_exit_failure = not is_entry and filled_legs == 0

            # Try leg-by-leg fallback for exit orders that fail
            if is_exit_failure:
                self.logger.warning("Combo exit order failed - trying leg-by-leg close as fallback")

                # Use the broker's leg-by-leg close method
                legged_result = self.broker.close_spread_legged(
                    legs=legs,
                    quantity=contracts,
                    timeout=60.0
                )

                if legged_result and legged_result.status == OrderStatus.FILLED:
                    fill_price = self._calculate_combo_fill_price(legged_result, is_entry, is_credit)
                    self.logger.info(f"Leg-by-leg exit successful! Fill price: {fill_price:.4f}")

                    return ExecutionResult(
                        success=True,
                        combo_order=legged_result,
                        fill_price=fill_price
                    )
                else:
                    self.logger.error("Leg-by-leg fallback also failed")
                    error_msg += " (leg-by-leg fallback also failed)"

            if 0 < filled_legs < total_legs:
                error_msg += ". PARTIAL FILL - manual intervention needed"
                self.logger.warning(error_msg)

            return ExecutionResult(
                success=False,
                combo_order=combo_order,
                fill_price=0,
                error_message=error_msg
            )

    def _calculate_combo_fill_price(self, combo_order, is_entry: bool = True, is_credit: bool = True) -> float:
        """Calculate net fill price from combo order fills.

        For IBKR combo orders, the avgFillPrice on the trade object
        represents the NET spread price, not individual leg prices.
        We use this directly when available.

        Args:
            combo_order: The filled combo order
            is_entry: True for entry orders, False for exit orders
            is_credit: True if original position is credit spread

        Returns:
            Fill price with proper sign for P&L calculation:
            - Entry credit spread: negative (we receive premium)
            - Entry debit spread: positive (we pay premium)
            - Exit credit spread: negative (we pay to close)
            - Exit debit spread: positive (we receive to close)
        """
        # Check if we have the combo avg fill price from any leg
        # (broker_insync sets the same avgFillPrice on all legs from trade.orderStatus.avgFillPrice)
        if combo_order.leg_orders:
            first_leg = combo_order.leg_orders[0]
            if first_leg.avg_fill_price != 0:
                combo_price = abs(first_leg.avg_fill_price)

                if is_entry:
                    # Entry order: use is_credit parameter to determine sign
                    # This works correctly for all strategies including multi-leg (iron condor, iron fly)
                    # where counting buy/sell legs would give incorrect result (2 buy, 2 sell = equal)
                    if is_credit:
                        # Credit spread entry - we receive premium (negative spread value)
                        return -combo_price
                    else:
                        # Debit spread entry - we pay premium (positive spread value)
                        return combo_price
                else:
                    # Exit order: sign matches original position type for P&L calc
                    # Credit spread exit: we pay to close (negative)
                    # Debit spread exit: we receive to close (positive)
                    if is_credit:
                        return -combo_price  # Paying to close credit spread
                    else:
                        return combo_price   # Receiving to close debit spread

        # Fallback: calculate from individual leg fills (may not work for IBKR combos)
        net_price = 0.0

        for order_info in combo_order.leg_orders:
            fill_price = order_info.avg_fill_price
            quantity = order_info.filled_quantity

            if order_info.action in ("BUY", "COMBO"):
                net_price += fill_price * quantity
            else:
                net_price -= fill_price * quantity

        # For exit orders, adjust sign based on position type
        if not is_entry:
            if is_credit:
                return -abs(net_price)  # Credit spread exit: we pay to close
            else:
                return abs(net_price)   # Debit spread exit: we receive to close

        return net_price
