"""
Spread Order Executor - Multi-Leg Option Order Execution

Handles:
- Spread order creation and placement
- Combo leg construction
- Native IBKR combo orders
- Sequential leg execution as fallback
- Execution monitoring and fill tracking
- Slippage management
- Order modification and cancellation

Author: client Options Trading Engine
"""

import threading
import time
from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
from enum import Enum

from ibapi.contract import Contract, ComboLeg
from ibapi.order import Order

from ..utils.logging import TradingLogger
from ..utils.config import EngineConfig
from ..enums import OrderStatus, OrderSide
from ..utils.exceptions import (
    SpreadExecutionError, OrderSubmitError, OrderRejectError,
    OrderTimeoutError, PartialFillError, OrderCancelError
)
from ..utils.timezone import US_EASTERN


# ============================================================================
# DATA CLASSES
# ============================================================================

class SpreadType(Enum):
    """Types of spread orders"""
    VERTICAL = "VERTICAL"           # Bull/Bear spreads
    IRON_CONDOR = "IRON_CONDOR"    # 4-leg iron condor
    IRON_FLY = "IRON_FLY"          # Iron butterfly
    STRADDLE = "STRADDLE"          # Long/Short straddle
    STRANGLE = "STRANGLE"          # Long/Short strangle
    RATIO = "RATIO"                 # Ratio spreads
    CUSTOM = "CUSTOM"               # Custom multi-leg


class ExecutionMode(Enum):
    """How to execute the spread"""
    NATIVE_COMBO = "NATIVE_COMBO"       # Single IBKR combo order (best)
    SEQUENTIAL = "SEQUENTIAL"            # Execute legs one by one
    SIMULTANEOUS = "SIMULTANEOUS"        # Submit all legs at once


@dataclass
class SpreadLeg:
    """Definition of a single leg in a spread"""
    contract: Contract
    action: str                  # BUY or SELL
    quantity: int = 1
    price: float = 0.0           # Limit price for leg
    ratio: int = 1               # Ratio for ratio spreads

    # Execution state
    order_id: Optional[int] = None
    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: int = 0
    avg_fill_price: float = 0.0

    def __post_init__(self):
        if self.action not in ("BUY", "SELL"):
            raise ValueError(f"Invalid action: {self.action}, must be BUY or SELL")


@dataclass
class SpreadOrderInfo:
    """Complete spread order with all legs"""
    spread_id: str
    spread_type: SpreadType
    legs: List[SpreadLeg] = field(default_factory=list)

    # Order parameters
    total_quantity: int = 1
    limit_price: Optional[float] = None     # Net debit/credit limit
    execution_mode: ExecutionMode = ExecutionMode.NATIVE_COMBO
    slippage_per_leg: float = 0.05

    # Status tracking
    status: OrderStatus = OrderStatus.PENDING
    combo_order_id: Optional[int] = None
    submit_time: Optional[datetime] = None
    fill_time: Optional[datetime] = None

    # Execution results
    total_credit: float = 0.0
    total_debit: float = 0.0
    net_premium: float = 0.0               # Positive = credit, Negative = debit
    slippage: float = 0.0

    # Error tracking
    error_message: Optional[str] = None

    def __post_init__(self):
        if self.submit_time is None:
            self.submit_time = datetime.now(US_EASTERN)

    @property
    def is_filled(self) -> bool:
        """Check if all legs are filled"""
        return all(leg.status == OrderStatus.FILLED for leg in self.legs)

    @property
    def filled_legs(self) -> int:
        """Count of filled legs"""
        return sum(1 for leg in self.legs if leg.status == OrderStatus.FILLED)

    @property
    def is_credit(self) -> bool:
        """Check if this is a credit spread"""
        return self.net_premium > 0

    def calculate_net_premium(self) -> float:
        """Calculate net premium from filled legs"""
        net = 0.0
        for leg in self.legs:
            if leg.status == OrderStatus.FILLED:
                if leg.action == "SELL":
                    net += leg.avg_fill_price * leg.filled_quantity
                else:
                    net -= leg.avg_fill_price * leg.filled_quantity
        self.net_premium = net
        return net

    def get_summary(self) -> Dict[str, Any]:
        """Get summary of spread order"""
        return {
            "spread_id": self.spread_id,
            "type": self.spread_type.value,
            "status": self.status.value,
            "legs": len(self.legs),
            "filled_legs": self.filled_legs,
            "net_premium": f"${self.net_premium:.2f}",
            "is_credit": self.is_credit,
            "submit_time": self.submit_time.strftime("%H:%M:%S") if self.submit_time else None,
            "fill_time": self.fill_time.strftime("%H:%M:%S") if self.fill_time else None
        }


# ============================================================================
# SPREAD ORDER EXECUTOR
# ============================================================================

class SpreadOrderExecutor:
    """
    Handles execution of multi-leg option spread orders.

    Supports multiple execution modes:
    1. Native Combo: Single IBKR combo order (preferred)
    2. Sequential: Execute legs one by one
    3. Simultaneous: Submit all legs at once

    Usage:
        executor = SpreadOrderExecutor(broker, logger, config)

        # Create spread legs
        legs = [
            SpreadLeg(short_put_contract, "SELL", 1, price=2.50),
            SpreadLeg(long_put_contract, "BUY", 1, price=1.00)
        ]

        # Execute spread
        spread_order = executor.execute_spread(
            legs=legs,
            spread_type=SpreadType.VERTICAL,
            quantity=5,
            limit_price=1.50  # Net credit
        )

        # Wait for fill
        executor.wait_for_fill(spread_order.spread_id)
    """

    def __init__(
        self,
        broker,  # IBKRWrapper - avoid circular import
        logger: TradingLogger,
        config: Optional[EngineConfig] = None
    ):
        self.broker = broker
        self.logger = logger
        self.config = config

        # Active spread orders
        self._spread_orders: Dict[str, SpreadOrderInfo] = {}
        self._order_lock = threading.Lock()

        # Execution settings
        self.default_slippage = config.spread_slippage if config else 0.05
        self.max_retry_attempts = 3
        self.fill_timeout = config.order_fill_timeout if config else 60.0

        # Execution mode preference
        self.prefer_native_combo = True

    # ============================================================================
    # SPREAD EXECUTION - MAIN ENTRY POINT
    # ============================================================================

    def execute_spread(
        self,
        legs: List[SpreadLeg],
        spread_type: SpreadType = SpreadType.CUSTOM,
        quantity: int = 1,
        limit_price: Optional[float] = None,
        execution_mode: Optional[ExecutionMode] = None,
        slippage: Optional[float] = None
    ) -> SpreadOrderInfo:
        """
        Execute a spread order with multiple legs.

        Args:
            legs: List of SpreadLeg definitions
            spread_type: Type of spread (for logging/tracking)
            quantity: Number of spreads to execute
            limit_price: Net limit price (credit if positive, debit if negative)
            execution_mode: Override default execution mode
            slippage: Slippage allowance per leg

        Returns:
            SpreadOrderInfo with execution status
        """
        # Generate spread ID
        spread_id = f"SPREAD_{datetime.now(US_EASTERN).strftime('%Y%m%d_%H%M%S_%f')}"

        # Determine execution mode
        if execution_mode is None:
            execution_mode = ExecutionMode.NATIVE_COMBO if self.prefer_native_combo else ExecutionMode.SEQUENTIAL

        # Create spread order info
        spread_order = SpreadOrderInfo(
            spread_id=spread_id,
            spread_type=spread_type,
            legs=legs,
            total_quantity=quantity,
            limit_price=limit_price,
            execution_mode=execution_mode,
            slippage_per_leg=slippage or self.default_slippage
        )

        # Store order
        with self._order_lock:
            self._spread_orders[spread_id] = spread_order

        # Log spread execution start
        self._log_spread_start(spread_order)

        try:
            # Execute based on mode
            if execution_mode == ExecutionMode.NATIVE_COMBO:
                self._execute_native_combo(spread_order)
            elif execution_mode == ExecutionMode.SEQUENTIAL:
                self._execute_sequential(spread_order)
            else:  # SIMULTANEOUS
                self._execute_simultaneous(spread_order)

        except Exception as e:
            spread_order.status = OrderStatus.ERROR
            spread_order.error_message = str(e)
            self.logger.error(f"Spread execution failed: {e}")
            raise SpreadExecutionError(
                spread_id=spread_id,
                filled_legs=spread_order.filled_legs,
                total_legs=len(legs),
                context={"error": str(e)}
            )

        return spread_order

    # ============================================================================
    # NATIVE COMBO ORDER EXECUTION
    # ============================================================================

    def _execute_native_combo(self, spread_order: SpreadOrderInfo):
        """Execute as a single IBKR combo order"""
        self.logger.info(f"[NATIVE COMBO] Executing {spread_order.spread_type.value} as combo order")

        # Build combo contract
        combo_contract = self._build_combo_contract(spread_order)

        # Build combo order
        combo_order = self._build_combo_order(spread_order)

        # Place order through broker
        order_id = self.broker.get_next_order_id()
        spread_order.combo_order_id = order_id
        spread_order.status = OrderStatus.SUBMITTED

        self.logger.info(f"Placing combo order {order_id} with {len(spread_order.legs)} legs")
        self.broker.placeOrder(order_id, combo_contract, combo_order)

        # Register for status updates
        self._register_combo_callback(spread_order)

    def _build_combo_contract(self, spread_order: SpreadOrderInfo) -> Contract:
        """Build IBKR combo contract from spread legs"""
        combo = Contract()
        combo.symbol = spread_order.legs[0].contract.symbol
        combo.secType = "BAG"  # Combo/bag order
        combo.currency = "USD"
        combo.exchange = "SMART"

        combo.comboLegs = []

        for i, leg in enumerate(spread_order.legs):
            combo_leg = ComboLeg()
            combo_leg.conId = leg.contract.conId
            combo_leg.ratio = leg.ratio * spread_order.total_quantity
            combo_leg.action = leg.action
            combo_leg.exchange = leg.contract.exchange or "SMART"

            combo.comboLegs.append(combo_leg)
            self.logger.debug(f"  Leg {i+1}: {leg.action} {combo_leg.ratio}x conId={combo_leg.conId}")

        return combo

    def _build_combo_order(self, spread_order: SpreadOrderInfo) -> Order:
        """Build IBKR order for combo"""
        order = Order()
        order.action = "BUY"  # Always BUY the combo structure
        order.totalQuantity = 1  # Quantity is in leg ratios
        order.tif = "DAY"
        order.transmit = True
        # Override TWS precautionary settings that reject orders based on price %
        order.overridePercentageConstraints = True

        if spread_order.limit_price is not None:
            order.orderType = "LMT"
            order.lmtPrice = round(spread_order.limit_price, 2)
            self.logger.info(f"Combo limit price: ${order.lmtPrice:.2f}")
        else:
            order.orderType = "MKT"
            self.logger.info("Combo order type: MARKET")

        return order

    def _register_combo_callback(self, spread_order: SpreadOrderInfo):
        """Register callback for combo order status updates"""
        # The broker's orderStatus callback will handle updates
        # We just need to link our spread_id to the order_id
        if hasattr(self.broker, '_spread_order_map'):
            self.broker._spread_order_map[spread_order.combo_order_id] = spread_order.spread_id

    # ============================================================================
    # SEQUENTIAL LEG EXECUTION
    # ============================================================================

    def _execute_sequential(self, spread_order: SpreadOrderInfo):
        """Execute legs one by one"""
        self.logger.info(f"[SEQUENTIAL] Executing {len(spread_order.legs)} legs sequentially")
        spread_order.status = OrderStatus.SUBMITTED

        for i, leg in enumerate(spread_order.legs):
            self.logger.info(f"  Executing leg {i+1}/{len(spread_order.legs)}: {leg.action} @ ${leg.price:.2f}")

            try:
                self._execute_single_leg(spread_order, leg, i)

                # Wait for fill before next leg
                if not self._wait_for_leg_fill(leg, timeout=self.fill_timeout):
                    raise PartialFillError(
                        order_id=leg.order_id,
                        filled_quantity=leg.filled_quantity,
                        total_quantity=leg.quantity,
                        context={"leg_index": i, "spread_id": spread_order.spread_id}
                    )

            except Exception as e:
                self.logger.error(f"Leg {i+1} execution failed: {e}")
                self._handle_partial_fill(spread_order, failed_leg_index=i)
                raise

        # All legs filled
        spread_order.status = OrderStatus.FILLED
        spread_order.fill_time = datetime.now(US_EASTERN)
        spread_order.calculate_net_premium()

        self._log_spread_complete(spread_order)

    def _execute_single_leg(self, spread_order: SpreadOrderInfo, leg: SpreadLeg, leg_index: int):
        """Execute a single leg order"""
        # Calculate price with slippage
        price = leg.price
        if leg.action == "BUY":
            price += spread_order.slippage_per_leg
        else:
            price -= spread_order.slippage_per_leg
        price = max(0.01, round(price, 2))

        # Create order
        order = Order()
        order.action = leg.action
        order.totalQuantity = leg.quantity * spread_order.total_quantity
        order.orderType = "LMT"
        order.lmtPrice = price
        order.tif = "DAY"
        order.transmit = True

        # Place order
        order_id = self.broker.get_next_order_id()
        leg.order_id = order_id
        leg.status = OrderStatus.SUBMITTED

        self.broker.placeOrder(order_id, leg.contract, order)

        self.logger.info(f"    Order {order_id}: {leg.action} {order.totalQuantity} @ ${price:.2f}")

    def _wait_for_leg_fill(self, leg: SpreadLeg, timeout: float) -> bool:
        """Wait for a single leg to fill"""
        start_time = time.time()

        while time.time() - start_time < timeout:
            # Check broker for order status
            if leg.order_id in self.broker.orders:
                order_info = self.broker.orders[leg.order_id]
                leg.status = order_info.status
                leg.filled_quantity = order_info.filled_quantity
                leg.avg_fill_price = order_info.avg_fill_price

                if leg.status == OrderStatus.FILLED:
                    return True
                elif leg.status in (OrderStatus.CANCELLED, OrderStatus.ERROR):
                    return False

            time.sleep(0.1)

        return False

    # ============================================================================
    # SIMULTANEOUS LEG EXECUTION
    # ============================================================================

    def _execute_simultaneous(self, spread_order: SpreadOrderInfo):
        """Execute all legs at the same time"""
        self.logger.info(f"[SIMULTANEOUS] Executing {len(spread_order.legs)} legs at once")
        spread_order.status = OrderStatus.SUBMITTED

        # Submit all legs
        for i, leg in enumerate(spread_order.legs):
            self._execute_single_leg(spread_order, leg, i)

        # Wait for all to fill
        all_filled = self._wait_for_all_legs(spread_order, timeout=self.fill_timeout)

        if all_filled:
            spread_order.status = OrderStatus.FILLED
            spread_order.fill_time = datetime.now(US_EASTERN)
            spread_order.calculate_net_premium()
            self._log_spread_complete(spread_order)
        else:
            # Handle partial fills
            self._handle_partial_fill(spread_order)

    def _wait_for_all_legs(self, spread_order: SpreadOrderInfo, timeout: float) -> bool:
        """Wait for all legs to fill"""
        start_time = time.time()

        while time.time() - start_time < timeout:
            all_filled = True

            for leg in spread_order.legs:
                if leg.order_id in self.broker.orders:
                    order_info = self.broker.orders[leg.order_id]
                    leg.status = order_info.status
                    leg.filled_quantity = order_info.filled_quantity
                    leg.avg_fill_price = order_info.avg_fill_price

                if leg.status != OrderStatus.FILLED:
                    all_filled = False

            if all_filled:
                return True

            time.sleep(0.1)

        return False

    # ============================================================================
    # PARTIAL FILL HANDLING
    # ============================================================================

    def _handle_partial_fill(self, spread_order: SpreadOrderInfo, failed_leg_index: int = -1):
        """Handle partial fill situation - may need to unwind"""
        filled_legs = [leg for leg in spread_order.legs if leg.status == OrderStatus.FILLED]
        unfilled_legs = [leg for leg in spread_order.legs if leg.status != OrderStatus.FILLED]

        self.logger.warning("=" * 50)
        self.logger.warning("[PARTIAL FILL WARNING]")
        self.logger.warning(f"   Filled legs: {len(filled_legs)}/{len(spread_order.legs)}")
        self.logger.warning(f"   Spread ID: {spread_order.spread_id}")

        for i, leg in enumerate(filled_legs):
            self.logger.warning(f"   Filled: {leg.action} @ ${leg.avg_fill_price:.2f}")

        for leg in unfilled_legs:
            self.logger.warning(f"   UNFILLED: {leg.action} (status: {leg.status.value})")

        self.logger.warning("=" * 50)

        spread_order.status = OrderStatus.PARTIAL

        # Cancel any pending orders
        for leg in unfilled_legs:
            if leg.order_id and leg.status == OrderStatus.SUBMITTED:
                try:
                    self.broker.cancelOrder(leg.order_id, "")
                    self.logger.info(f"Cancelled pending order {leg.order_id}")
                except Exception as e:
                    self.logger.error(f"Failed to cancel order {leg.order_id}: {e}")

    def unwind_spread(self, spread_id: str) -> bool:
        """Unwind a partially filled spread"""
        with self._order_lock:
            spread_order = self._spread_orders.get(spread_id)

        if not spread_order:
            self.logger.error(f"Spread {spread_id} not found")
            return False

        self.logger.info(f"[UNWINDING] Spread {spread_id}")

        # Close all filled positions
        for leg in spread_order.legs:
            if leg.status == OrderStatus.FILLED and leg.filled_quantity > 0:
                # Reverse the action
                close_action = "SELL" if leg.action == "BUY" else "BUY"

                order = Order()
                order.action = close_action
                order.totalQuantity = leg.filled_quantity
                order.orderType = "MKT"  # Market to ensure fill
                order.tif = "DAY"
                order.transmit = True

                order_id = self.broker.get_next_order_id()
                self.broker.placeOrder(order_id, leg.contract, order)

                self.logger.info(f"  Unwinding: {close_action} {leg.filled_quantity} (order {order_id})")

        return True

    # ============================================================================
    # ORDER MODIFICATION
    # ============================================================================

    def modify_spread_price(self, spread_id: str, new_limit_price: float) -> bool:
        """Modify the limit price of a pending spread order"""
        with self._order_lock:
            spread_order = self._spread_orders.get(spread_id)

        if not spread_order:
            self.logger.error(f"Spread {spread_id} not found")
            return False

        if spread_order.status != OrderStatus.SUBMITTED:
            self.logger.warning(f"Cannot modify spread {spread_id} - status: {spread_order.status.value}")
            return False

        spread_order.limit_price = new_limit_price

        if spread_order.execution_mode == ExecutionMode.NATIVE_COMBO and spread_order.combo_order_id:
            # Modify combo order
            combo_contract = self._build_combo_contract(spread_order)
            combo_order = self._build_combo_order(spread_order)

            self.broker.placeOrder(spread_order.combo_order_id, combo_contract, combo_order)
            self.logger.info(f"Modified combo order {spread_order.combo_order_id} price to ${new_limit_price:.2f}")

        return True

    def cancel_spread(self, spread_id: str) -> bool:
        """Cancel a pending spread order"""
        with self._order_lock:
            spread_order = self._spread_orders.get(spread_id)

        if not spread_order:
            self.logger.error(f"Spread {spread_id} not found")
            return False

        self.logger.info(f"[CANCELLING] Spread {spread_id}")

        # Cancel combo order if native combo
        if spread_order.combo_order_id:
            self.broker.cancelOrder(spread_order.combo_order_id, "")

        # Cancel individual leg orders
        for leg in spread_order.legs:
            if leg.order_id and leg.status == OrderStatus.SUBMITTED:
                self.broker.cancelOrder(leg.order_id, "")

        spread_order.status = OrderStatus.CANCELLED
        return True

    # ============================================================================
    # WAIT FOR FILL
    # ============================================================================

    def wait_for_fill(
        self,
        spread_id: str,
        timeout: float = None
    ) -> Optional[SpreadOrderInfo]:
        """
        Wait for spread order to fill completely.

        Args:
            spread_id: Spread order ID
            timeout: Max wait time in seconds

        Returns:
            SpreadOrderInfo if filled, None on timeout
        """
        timeout = timeout or self.fill_timeout
        start_time = time.time()

        while time.time() - start_time < timeout:
            with self._order_lock:
                spread_order = self._spread_orders.get(spread_id)

            if spread_order:
                if spread_order.status == OrderStatus.FILLED:
                    return spread_order
                elif spread_order.status in (OrderStatus.CANCELLED, OrderStatus.ERROR):
                    return spread_order

            time.sleep(0.1)

        self.logger.warning(f"Timeout waiting for spread {spread_id} to fill")
        return None

    # ============================================================================
    # STATUS & QUERIES
    # ============================================================================

    def get_spread_status(self, spread_id: str) -> Optional[SpreadOrderInfo]:
        """Get spread order status"""
        with self._order_lock:
            return self._spread_orders.get(spread_id)

    def get_all_spreads(self) -> Dict[str, SpreadOrderInfo]:
        """Get all spread orders"""
        with self._order_lock:
            return dict(self._spread_orders)

    def get_open_spreads(self) -> List[SpreadOrderInfo]:
        """Get all open (non-filled, non-cancelled) spreads"""
        with self._order_lock:
            return [
                s for s in self._spread_orders.values()
                if s.status not in (OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.ERROR)
            ]

    def update_spread_status(self, order_id: int, status: OrderStatus, fill_info: Dict[str, Any] = None):
        """Update spread status from broker callback"""
        with self._order_lock:
            # Find spread by combo order ID or leg order ID
            for spread in self._spread_orders.values():
                if spread.combo_order_id == order_id:
                    spread.status = status
                    if status == OrderStatus.FILLED:
                        spread.fill_time = datetime.now(US_EASTERN)
                        if fill_info:
                            spread.net_premium = fill_info.get("avg_price", 0)
                    return

                for leg in spread.legs:
                    if leg.order_id == order_id:
                        leg.status = status
                        if fill_info:
                            leg.filled_quantity = fill_info.get("filled", 0)
                            leg.avg_fill_price = fill_info.get("avg_price", 0)

                        # Check if all legs filled
                        if all(l.status == OrderStatus.FILLED for l in spread.legs):
                            spread.status = OrderStatus.FILLED
                            spread.fill_time = datetime.now(US_EASTERN)
                            spread.calculate_net_premium()
                        return

    # ============================================================================
    # LOGGING
    # ============================================================================

    def _log_spread_start(self, spread_order: SpreadOrderInfo):
        """Log spread execution start"""
        self.logger.info("=" * 60)
        self.logger.info(f"[SPREAD ORDER] {spread_order.spread_type.value}")
        self.logger.info(f"   Spread ID: {spread_order.spread_id}")
        self.logger.info(f"   Mode: {spread_order.execution_mode.value}")
        self.logger.info(f"   Quantity: {spread_order.total_quantity}")
        if spread_order.limit_price:
            self.logger.info(f"   Limit: ${spread_order.limit_price:.2f} {'CREDIT' if spread_order.limit_price > 0 else 'DEBIT'}")
        self.logger.info(f"   Legs ({len(spread_order.legs)}):")

        for i, leg in enumerate(spread_order.legs):
            symbol = leg.contract.symbol
            strike = leg.contract.strike
            right = leg.contract.right
            self.logger.info(f"      {i+1}. {leg.action} {leg.quantity}x {symbol} {strike}{right} @ ${leg.price:.2f}")

        self.logger.info("=" * 60)

    def _log_spread_complete(self, spread_order: SpreadOrderInfo):
        """Log spread execution completion"""
        self.logger.info("=" * 60)
        self.logger.info(f"[SPREAD FILLED] {spread_order.spread_type.value}")
        self.logger.info(f"   Spread ID: {spread_order.spread_id}")
        self.logger.info(f"   Status: {spread_order.status.value}")
        self.logger.info(f"   Net Premium: ${spread_order.net_premium:.2f} {'(CREDIT)' if spread_order.is_credit else '(DEBIT)'}")

        exec_time = None
        if spread_order.fill_time and spread_order.submit_time:
            exec_time = (spread_order.fill_time - spread_order.submit_time).total_seconds()
            self.logger.info(f"   Execution Time: {exec_time:.2f}s")

        self.logger.info(f"   Leg Fills:")
        for i, leg in enumerate(spread_order.legs):
            self.logger.info(f"      {i+1}. {leg.action} {leg.filled_quantity} @ ${leg.avg_fill_price:.2f}")

        self.logger.info("=" * 60)

    # ============================================================================
    # CONVENIENCE METHODS FOR COMMON SPREADS
    # ============================================================================

    def execute_vertical_spread(
        self,
        short_contract: Contract,
        long_contract: Contract,
        short_price: float,
        long_price: float,
        quantity: int = 1,
        limit_credit: Optional[float] = None
    ) -> SpreadOrderInfo:
        """
        Execute a vertical spread (bull put / bear call).

        Args:
            short_contract: Contract to sell
            long_contract: Contract to buy
            short_price: Price of short leg
            long_price: Price of long leg
            quantity: Number of spreads
            limit_credit: Net credit limit

        Returns:
            SpreadOrderInfo
        """
        legs = [
            SpreadLeg(short_contract, "SELL", 1, short_price),
            SpreadLeg(long_contract, "BUY", 1, long_price)
        ]

        return self.execute_spread(
            legs=legs,
            spread_type=SpreadType.VERTICAL,
            quantity=quantity,
            limit_price=limit_credit
        )

    def execute_iron_condor(
        self,
        put_short: Contract,
        put_long: Contract,
        call_short: Contract,
        call_long: Contract,
        prices: Dict[str, float],
        quantity: int = 1,
        limit_credit: Optional[float] = None
    ) -> SpreadOrderInfo:
        """
        Execute an iron condor.

        Args:
            put_short: Short put contract
            put_long: Long put contract
            call_short: Short call contract
            call_long: Long call contract
            prices: Dict with 'put_short', 'put_long', 'call_short', 'call_long' prices
            quantity: Number of condors
            limit_credit: Net credit limit

        Returns:
            SpreadOrderInfo
        """
        legs = [
            SpreadLeg(put_short, "SELL", 1, prices.get("put_short", 0)),
            SpreadLeg(put_long, "BUY", 1, prices.get("put_long", 0)),
            SpreadLeg(call_short, "SELL", 1, prices.get("call_short", 0)),
            SpreadLeg(call_long, "BUY", 1, prices.get("call_long", 0))
        ]

        return self.execute_spread(
            legs=legs,
            spread_type=SpreadType.IRON_CONDOR,
            quantity=quantity,
            limit_price=limit_credit
        )

    def execute_straddle(
        self,
        call_contract: Contract,
        put_contract: Contract,
        call_price: float,
        put_price: float,
        is_short: bool = True,
        quantity: int = 1,
        limit_price: Optional[float] = None
    ) -> SpreadOrderInfo:
        """
        Execute a straddle.

        Args:
            call_contract: ATM call contract
            put_contract: ATM put contract
            call_price: Call price
            put_price: Put price
            is_short: True for short straddle (credit), False for long (debit)
            quantity: Number of straddles
            limit_price: Net limit

        Returns:
            SpreadOrderInfo
        """
        action = "SELL" if is_short else "BUY"

        legs = [
            SpreadLeg(call_contract, action, 1, call_price),
            SpreadLeg(put_contract, action, 1, put_price)
        ]

        return self.execute_spread(
            legs=legs,
            spread_type=SpreadType.STRADDLE,
            quantity=quantity,
            limit_price=limit_price
        )

    def execute_strangle(
        self,
        call_contract: Contract,
        put_contract: Contract,
        call_price: float,
        put_price: float,
        is_short: bool = True,
        quantity: int = 1,
        limit_price: Optional[float] = None
    ) -> SpreadOrderInfo:
        """
        Execute a strangle.

        Args:
            call_contract: OTM call contract
            put_contract: OTM put contract
            call_price: Call price
            put_price: Put price
            is_short: True for short strangle (credit), False for long (debit)
            quantity: Number of strangles
            limit_price: Net limit

        Returns:
            SpreadOrderInfo
        """
        action = "SELL" if is_short else "BUY"

        legs = [
            SpreadLeg(call_contract, action, 1, call_price),
            SpreadLeg(put_contract, action, 1, put_price)
        ]

        return self.execute_spread(
            legs=legs,
            spread_type=SpreadType.STRANGLE,
            quantity=quantity,
            limit_price=limit_price
        )
