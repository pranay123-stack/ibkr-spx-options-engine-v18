"""
IBKR Broker Wrapper using ib_insync Library

This module provides an alternative IBKR broker interface using the ib_insync library,
which offers a more pythonic and easier-to-use API compared to the native ibapi.

Key differences from ibapi:
- No need for threading - ib_insync handles the event loop internally
- Synchronous-style programming with async capabilities under the hood
- Uses self.ib.sleep() for message processing instead of time.sleep()

Author: client Options Trading Engine
"""

from datetime import datetime, timedelta, date, time as dt_time, timezone
from typing import Dict, Any, Optional, Callable, List
from collections import deque

from ib_insync import IB, Contract, Order, ComboLeg, Index, Option

from ..utils.logging import TradingLogger
from ..utils.config import EngineConfig
from ..utils.timezone import US_EASTERN, IST

# Import data classes from models
from .models import (
    ConnectionConfig,
    MarketDataType,
    OHLCBar,
    OptionContract,
    OptionChain,
    OrderInfo,
    ComboOrder,
    AccountInfo,
    PositionInfo,
)
from ..enums import OrderStatus, OptionType, ExpiryRule


class IBKRInsyncWrapper:
    """
    IBKR Broker Wrapper using ib_insync library.

    This class provides a clean interface to IBKR TWS/Gateway using the ib_insync
    library, which simplifies the interaction compared to the native ibapi.

    Key features:
    - Simplified connection management (no threading required)
    - Synchronous-style API with async under the hood
    - Uses ib_insync's IB class for all interactions

    Usage:
        wrapper = IBKRInsyncWrapper(logger, config)
        if wrapper.connect():
            status = wrapper.get_status()
            wrapper.print_status()
            wrapper.disconnect()
    """

    def __init__(self, logger: TradingLogger, config: Optional[EngineConfig] = None):
        """
        Initialize the IBKR ib_insync wrapper.

        Args:
            logger: TradingLogger instance for logging
            config: Optional EngineConfig for connection settings
        """
        self.logger = logger
        self.config = config

        # Initialize ib_insync IB instance
        self.ib = IB()

        # Connection state
        self._connected = False
        self._connection_config = ConnectionConfig()

        # Order tracking
        self.next_order_id: Optional[int] = None
        self.account_id: Optional[str] = None

        # Data storage
        self.orders: Dict[int, OrderInfo] = {}
        self.combo_orders: Dict[str, ComboOrder] = {}
        self.account_info = AccountInfo()
        self.positions: Dict[str, PositionInfo] = {}

        # Error tracking
        self.last_error: Optional[str] = None
        self.errors: list = []

        # Market data storage
        self._tick_subscriptions: Dict[int, Any] = {}
        self.bars: deque = deque(maxlen=1000)

        # Option chain cache
        self._chains_cache: Dict[str, OptionChain] = {}

        # Register error handler
        self.ib.errorEvent += self._on_error

    def connect(
        self,
        host: str = None,
        port: int = None,
        client_id: int = None,
        timeout: float = None
    ) -> bool:
        """
        Connect to IBKR TWS/Gateway.

        Args:
            host: IBKR host address (default: 127.0.0.1)
            port: IBKR port (7496=Live, 7497=Paper)
            client_id: Client ID for this connection
            timeout: Connection timeout in seconds

        Returns:
            True if connection successful, False otherwise
        """
        if self._connected:
            self.logger.warning("Already connected to IBKR")
            return True

        # Use config values if not specified
        if self.config:
            host = host or self.config.ibkr_host
            port = port or self.config.ibkr_port
            client_id = self.config.ibkr_client_id if client_id is None else client_id

        # Fall back to defaults
        host = host or self._connection_config.host
        port = port or self._connection_config.port
        client_id = self._connection_config.client_id if client_id is None else client_id
        timeout = timeout or self._connection_config.timeout

        self.logger.info("[IBKR CONNECTION - ib_insync]")
        self.logger.info(f"   Host: {host}")
        self.logger.info(f"   Port: {port} ({'LIVE' if port == 7496 else 'PAPER' if port == 7497 else 'CUSTOM'})")
        self.logger.info(f"   Client ID: {client_id}")

        # Auto-retry on initial connect with exponential backoff
        import time as time_module  # Local import for sleep
        max_retries = self._connection_config.reconnect_attempts
        base_delay = self._connection_config.reconnect_delay

        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    delay = min(base_delay * (2 ** (attempt - 1)), 60)  # Max 60s delay
                    self.logger.info(f"   Retry attempt {attempt + 1}/{max_retries} in {delay:.1f}s...")
                    time_module.sleep(delay)

                # Connect using ib_insync - handles threading internally
                self.ib.connect(
                    host=host,
                    port=port,
                    clientId=client_id,
                    timeout=timeout
                )

                # Connection successful
                self._connected = True

                # Bug #8 fix: Clear stale bars on reconnect to avoid mixing old/new data
                if self.bars:
                    self.logger.debug(f"Clearing {len(self.bars)} stale bars after reconnect")
                    self.bars.clear()

                # Bug #5 fix: Clear stale market data subscriptions on reconnect
                # Old subscriptions are invalid after disconnect - IBKR assigns new req_ids
                if self._tick_subscriptions:
                    self.logger.debug(f"Clearing {len(self._tick_subscriptions)} stale market data subscriptions after reconnect")
                    self._tick_subscriptions.clear()

                # Get account info
                accounts = self.ib.managedAccounts()
                if accounts:
                    self.account_id = accounts[0]
                    self.account_info.account_id = self.account_id

                # Get next valid order ID
                self.next_order_id = self.ib.client.getReqId()

                self.logger.info("[CONNECTION SUCCESSFUL]")
                self.logger.info(f"   Account: {self.account_id}")

                return True

            except Exception as e:
                self.logger.error(f"Connection attempt {attempt + 1}/{max_retries} failed: {e}")
                self.last_error = str(e)
                self.errors.append({
                    "timestamp": datetime.now(US_EASTERN),
                    "message": str(e),
                    "context": {"host": host, "port": port, "client_id": client_id, "attempt": attempt + 1}
                })

                # If last attempt, give up
                if attempt == max_retries - 1:
                    self.logger.error(f"All {max_retries} connection attempts failed")
                    self._connected = False
                    return False

        self._connected = False
        return False

    def disconnect(self) -> None:
        """
        Disconnect from IBKR TWS/Gateway.

        Cleanly disconnects from IBKR and resets connection state.
        """
        if self._connected:
            self.logger.info("Disconnecting from IBKR...")

            try:
                self.ib.disconnect()
            except Exception as e:
                self.logger.warning(f"Error during disconnect: {e}")

            self._connected = False
            self.logger.info("Disconnected from IBKR")
        else:
            self.logger.debug("Already disconnected from IBKR")

    def is_connected(self) -> bool:
        """
        Check if connected to IBKR.

        Returns:
            True if connected, False otherwise
        """
        return self._connected and self.ib.isConnected()

    def get_status(self) -> Dict[str, Any]:
        """
        Get current broker status as a dictionary.

        Returns:
            Dictionary containing:
            - connected: Connection status
            - account_id: Connected account ID
            - next_order_id: Next available order ID
            - server_version: IBKR server version (if connected)
            - pending_orders: Count of pending orders
            - filled_orders: Count of filled orders
            - positions: Count of open positions
            - ibkr_messages: Count of IBKR status messages (includes info, not just errors)
            - last_message: Most recent IBKR message
        """
        status = {
            "connected": self.is_connected(),
            "account_id": self.account_id,
            "next_order_id": self.next_order_id,
            "server_version": self.ib.client.serverVersion() if self.is_connected() else None,
            "pending_orders": len([o for o in self.orders.values() if o.status == OrderStatus.PENDING]),
            "filled_orders": len([o for o in self.orders.values() if o.status == OrderStatus.FILLED]),
            "positions": len(self.positions),
            "ibkr_messages": len(self.errors),
            "last_message": self.last_error
        }
        return status

    def print_status(self) -> None:
        """
        Log the current broker status in a formatted manner.

        Outputs a formatted status report to the logger including
        connection state, account info, order counts, and error info.
        """
        status = self.get_status()
        self.logger.debug("=" * 50)
        self.logger.debug("[IBKR WRAPPER STATUS - ib_insync]")
        for key, value in status.items():
            self.logger.debug(f"   {key}: {value}")
        self.logger.debug("=" * 50)

    def get_next_order_id(self) -> int:
        """
        Get the next available order ID.

        Retrieves and increments the order ID counter.

        Returns:
            Next available order ID
        """
        if self.next_order_id is None:
            # Request from IB if not initialized
            if self.is_connected():
                self.next_order_id = self.ib.client.getReqId()
            else:
                raise RuntimeError("Not connected to IBKR - cannot get order ID")

        order_id = self.next_order_id
        self.next_order_id += 1
        return order_id

    def _on_error(self, reqId: int, errorCode: int, errorString: str, contract: Contract = None) -> None:
        """
        Handle error events from ib_insync.

        Args:
            reqId: Request ID associated with the error
            errorCode: IBKR error code
            errorString: Error description
            contract: Contract associated with error (if any)
        """
        error_info = {
            "req_id": reqId,
            "code": errorCode,
            "message": errorString,
            "timestamp": datetime.now(US_EASTERN),
            "contract": str(contract) if contract else None
        }
        self.errors.append(error_info)
        self.last_error = errorString

        # Log based on severity
        if errorCode in (2104, 2106, 2158):  # Market data farm messages
            self.logger.debug(f"IBKR Info [{errorCode}]: {errorString}")
        elif errorCode >= 2000:  # Warnings
            self.logger.warning(f"IBKR Warning [{errorCode}]: {errorString}")
        elif errorCode == 201:  # "Riskless combination" - known IBKR limitation for SPX bracket orders
            # Downgrade to WARNING since this is expected and handled gracefully
            self.logger.warning(f"IBKR Known Limitation [{errorCode}]: {errorString}")
        else:  # Errors
            self.logger.error(f"IBKR Error [{errorCode}]: {errorString}")

    def sleep(self, seconds: float) -> None:
        """
        Sleep while processing IB messages.

        Use this instead of time.sleep() to allow ib_insync to process
        incoming messages during the wait.

        Args:
            seconds: Number of seconds to sleep
        """
        self.ib.sleep(seconds)

    # =========================================================================
    # SPX Price Helper Methods
    # =========================================================================

    def get_spx_tick_size(self, price: float) -> float:
        """
        Get the correct tick size for SPX options based on price level.

        SPX Options Tick Size Rules:
        - Below $3.00: $0.05
        - $3.00 and above: $0.10
        """
        return 0.05 if price < 3.00 else 0.10

    def round_to_spx_tick(self, price: float) -> float:
        """
        Round price to valid SPX option tick size.

        This function ensures prices conform to CBOE SPX option tick requirements.
        """
        from decimal import Decimal, ROUND_HALF_UP

        tick_size = self.get_spx_tick_size(price)

        # Use Decimal for precise arithmetic
        price_decimal = Decimal(str(price))
        tick_decimal = Decimal(str(tick_size))

        # Round to nearest valid tick
        ticks = (price_decimal / tick_decimal).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
        rounded_price = float(ticks * tick_decimal)

        # Ensure 2 decimal places
        return round(rounded_price, 2)

    def get_spread_market_price(self, legs: List[Dict[str, Any]], timeout: int = 2) -> Optional[float]:
        """
        Get current market price for a spread by requesting quotes for all legs.

        Args:
            legs: List of leg dictionaries with 'contract' and 'action' keys
            timeout: Time to wait for market data

        Returns:
            Net spread price (mid of bid/ask), or None if unavailable
        """
        try:
            net_price = 0.0
            tickers = []

            # Request market data for all legs
            for leg in legs:
                ticker = self.ib.reqMktData(leg['contract'], '', False, False)
                tickers.append((leg, ticker))

            # Wait for data
            self.ib.sleep(timeout)

            # Calculate net spread price
            all_valid = True
            for leg, ticker in tickers:
                if ticker.bid and ticker.bid > 0 and ticker.ask and ticker.ask > 0:
                    mid_price = (ticker.bid + ticker.ask) / 2
                    leg_quantity = leg.get('quantity', 1)

                    if leg['action'] == 'BUY':
                        net_price -= mid_price * leg_quantity
                    else:  # SELL
                        net_price += mid_price * leg_quantity
                else:
                    all_valid = False
                    self.logger.debug(f"No valid market data for leg: {leg['contract'].strike} {leg['contract'].right}")

            # Cancel market data subscriptions
            for leg, ticker in tickers:
                try:
                    self.ib.cancelMktData(leg['contract'])
                except Exception:
                    pass

            if all_valid:
                return net_price
            else:
                return None

        except Exception as e:
            self.logger.error(f"Error getting spread market price: {e}")
            return None

    # =========================================================================
    # Market Data Methods
    # =========================================================================

    def create_underlying_contract(self) -> Optional[Contract]:
        """
        Create and qualify SPX index contract.

        Returns:
            Qualified SPX Index contract, or None if qualification fails
        """
        try:
            self.logger.debug("Creating SPX underlying contract...")
            contract = Index('SPX', 'CBOE')

            # Qualify the contract with IBKR
            qualified = self.ib.qualifyContracts(contract)

            if qualified:
                self.logger.debug(f"SPX contract qualified: {qualified[0]}")
                return qualified[0]
            else:
                self.logger.error("Failed to qualify SPX contract")
                return None

        except Exception as e:
            self.logger.error(f"Error creating underlying contract: {e}")
            return None

    def get_latest_price(self, contract: Contract = None) -> Optional[float]:
        """
        Get the current/latest price for a contract.

        Args:
            contract: Contract to get price for. If None, uses SPX.

        Returns:
            Latest price (last or close), or None if unavailable
        """
        try:
            if contract is None:
                contract = self.create_underlying_contract()
                if contract is None:
                    return None

            self.logger.debug(f"Requesting market data for {contract.symbol}...")

            # Request market data snapshot
            self.ib.reqMktData(contract, '', False, False)

            # Allow time for data to arrive
            self.ib.sleep(1)

            # Get the ticker
            ticker = self.ib.ticker(contract)

            if ticker is None:
                self.logger.warning(f"No ticker data available for {contract.symbol}")
                return None

            # Try last price first, then close as fallback
            price = None
            if ticker.last and ticker.last > 0:
                price = ticker.last
                self.logger.debug(f"Using last price: {price}")
            elif ticker.close and ticker.close > 0:
                price = ticker.close
                self.logger.debug(f"Using close price: {price}")
            else:
                self.logger.warning(f"No valid price found for {contract.symbol}")

            return price

        except Exception as e:
            self.logger.error(f"Error getting latest price: {e}")
            return None

    def get_price(self, symbol: str = None) -> Optional[float]:
        """
        Alias for get_latest_price for backward compatibility.

        Args:
            symbol: Symbol to get price for (currently ignored, uses SPX)

        Returns:
            Latest price (last or close), or None if unavailable
        """
        return self.get_latest_price()

    def check_market_halt(self, contract: Contract = None) -> Dict[str, Any]:
        """
        Check if market appears to be halted based on stale/missing data.

        Detects potential halt conditions:
        - No bid/ask spread (both 0)
        - Bid/ask unchanged for extended period
        - Last trade timestamp too old

        Args:
            contract: Contract to check. If None, uses SPX.

        Returns:
            Dict with 'is_halted', 'reason', 'last_update_seconds'
        """
        result = {
            'is_halted': False,
            'reason': None,
            'last_update_seconds': None,
            'bid': None,
            'ask': None,
            'last': None
        }

        try:
            if contract is None:
                contract = self.create_underlying_contract()
                if contract is None:
                    result['is_halted'] = True
                    result['reason'] = "Cannot create contract"
                    return result

            # Get current ticker data
            ticker = self.ib.ticker(contract)

            if ticker is None:
                result['is_halted'] = True
                result['reason'] = "No ticker data available"
                return result

            result['bid'] = ticker.bid if ticker.bid else 0
            result['ask'] = ticker.ask if ticker.ask else 0
            result['last'] = ticker.last if ticker.last else 0

            # Check 1: No bid/ask (both 0 or negative)
            if (not ticker.bid or ticker.bid <= 0) and (not ticker.ask or ticker.ask <= 0):
                result['is_halted'] = True
                result['reason'] = "No bid/ask data - market may be halted"
                return result

            # Check 2: Bid >= Ask (crossed market - unusual, may indicate issue)
            if ticker.bid and ticker.ask and ticker.bid >= ticker.ask:
                self.logger.warning(f"Crossed market detected: bid={ticker.bid}, ask={ticker.ask}")
                # Not necessarily halted, but log warning

            # Check 3: Last trade time too old (if available)
            if hasattr(ticker, 'time') and ticker.time:
                from datetime import datetime
                now = datetime.now(US_EASTERN)
                last_update = ticker.time
                if hasattr(last_update, 'astimezone'):
                    last_update = last_update.astimezone(US_EASTERN)
                    age_seconds = (now - last_update).total_seconds()
                    result['last_update_seconds'] = age_seconds

                    # If no update in 5+ minutes during market hours, may be halted
                    if age_seconds > 300:  # 5 minutes
                        result['is_halted'] = True
                        result['reason'] = f"No market update in {int(age_seconds)}s - market may be halted"
                        return result

            return result

        except Exception as e:
            self.logger.error(f"Error checking market halt: {e}")
            result['is_halted'] = True
            result['reason'] = f"Error checking: {e}"
            return result

    def subscribe_market_data(
        self,
        contract: Contract,
        callback: Optional[Callable] = None
    ) -> Optional[int]:
        """
        Subscribe to real-time market data for a contract.

        Args:
            contract: Contract to subscribe to
            callback: Optional callback function for tick updates

        Returns:
            Request ID for the subscription, or None if failed
        """
        try:
            self.logger.debug(f"Subscribing to market data for {contract.symbol}...")

            # Request streaming market data
            ticker = self.ib.reqMktData(contract, '', False, False)

            if ticker is None:
                self.logger.error(f"Failed to subscribe to market data for {contract.symbol}")
                return None

            # Store the subscription
            req_id = id(ticker)  # Use ticker object id as unique identifier
            self._tick_subscriptions[req_id] = {
                'contract': contract,
                'ticker': ticker,
                'callback': callback
            }

            # If callback provided, set up event handler
            if callback:
                ticker.updateEvent += lambda t: callback(t)

            self.logger.debug(f"Subscribed to market data, req_id: {req_id}")
            return req_id

        except Exception as e:
            self.logger.error(f"Error subscribing to market data: {e}")
            return None

    def unsubscribe_market_data(self, req_id: int) -> bool:
        """
        Cancel/unsubscribe from market data.

        Args:
            req_id: Request ID from subscribe_market_data

        Returns:
            True if successfully unsubscribed, False otherwise
        """
        try:
            if req_id not in self._tick_subscriptions:
                self.logger.warning(f"No subscription found for req_id: {req_id}")
                return False

            subscription = self._tick_subscriptions[req_id]
            contract = subscription['contract']

            self.logger.info(f"Unsubscribing from market data for {contract.symbol}...")

            # Bug #5 fix: Cancel the market data request and verify success
            # Only remove from tracking if cancel succeeds
            try:
                self.ib.cancelMktData(contract)
                # If no exception, assume cancel succeeded
                del self._tick_subscriptions[req_id]
                self.logger.info(f"Unsubscribed from market data, req_id: {req_id}")
                return True
            except Exception as cancel_err:
                # Cancel failed - keep in tracking so we can retry
                self.logger.error(f"Failed to cancel market data subscription {req_id}: {cancel_err}")
                return False

        except Exception as e:
            self.logger.error(f"Error unsubscribing from market data: {e}")
            return False

    def request_historical_data(
        self,
        contract: Contract,
        duration: str,
        bar_size: str,
        what_to_show: str = 'TRADES',
        use_rth: bool = False,
        keep_up_to_date: bool = False,
        callback: Optional[Callable] = None,
        timeout: int = 30
    ) -> List[OHLCBar]:
        """
        Request historical bar data for a contract.

        Args:
            contract: Contract to get historical data for
            duration: Time duration (e.g., '1 D', '1 W', '1 M')
            bar_size: Bar size (e.g., '1 min', '5 mins', '1 hour', '1 day')
            what_to_show: Data type ('TRADES', 'MIDPOINT', 'BID', 'ASK')
            use_rth: Use regular trading hours only
            keep_up_to_date: Keep updating with new bars
            callback: Optional callback for bar updates
            timeout: Timeout in seconds

        Returns:
            List of OHLCBar objects
        """
        try:
            self.logger.debug(
                f"Requesting historical data for {contract.symbol}: "
                f"duration={duration}, bar_size={bar_size}, what_to_show={what_to_show}"
            )

            # Request historical data
            bars = self.ib.reqHistoricalData(
                contract,
                endDateTime='',
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow=what_to_show,
                useRTH=use_rth,
                formatDate=1,
                keepUpToDate=keep_up_to_date,
                timeout=timeout
            )

            if not bars:
                self.logger.warning(f"No historical data returned for {contract.symbol}")
                return []

            # Convert to OHLCBar objects
            # IMPORTANT: IBKR returns timestamps in US Eastern Time (EST/EDT)
            ohlc_bars = []
            for bar in bars:
                try:
                    # Parse timestamp and ensure it's in US Eastern
                    if isinstance(bar.date, datetime):
                        if bar.date.tzinfo is None:
                            # Naive datetime from IBKR is in US Eastern
                            ts = bar.date.replace(tzinfo=US_EASTERN)
                        else:
                            ts = bar.date
                    elif ' ' in str(bar.date):
                        # Intraday bars: "YYYYMMDD HH:MM:SS" in US Eastern
                        ts = datetime.strptime(str(bar.date), "%Y%m%d %H:%M:%S").replace(tzinfo=US_EASTERN)
                    else:
                        # Daily bars: "YYYYMMDD" in US Eastern
                        ts = datetime.strptime(str(bar.date), "%Y%m%d").replace(tzinfo=US_EASTERN)

                    ohlc_bar = OHLCBar(
                        timestamp=ts,
                        open=bar.open,
                        high=bar.high,
                        low=bar.low,
                        close=bar.close,
                        volume=int(bar.volume) if hasattr(bar, 'volume') and bar.volume else 0
                    )
                    ohlc_bars.append(ohlc_bar)
                except Exception as e:
                    self.logger.warning(f"Error converting bar: {e}")
                    continue

            self.logger.debug(f"Received {len(ohlc_bars)} historical bars for {contract.symbol}")

            # Don't call callback for initial historical batch
            # The callback is for real-time bar updates only (when keep_up_to_date=True)
            # Real-time updates will come through a different mechanism

            return ohlc_bars

        except Exception as e:
            import traceback
            self.logger.error(f"Error requesting historical data: {e}")
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            return []

    def get_bars(
        self,
        symbol: Optional[str] = None,
        duration: str = '1 D',
        bar_size: str = '5 mins',
        count: int = 100
    ) -> List[OHLCBar]:
        """
        Get historical bars for SPX (or specified symbol).

        This is a convenience method that creates the contract and
        fetches historical data in one call.

        Args:
            symbol: Symbol to get bars for (default: SPX)
            duration: Time duration (e.g., '1 D', '1 W')
            bar_size: Bar size (e.g., '1 min', '5 mins')
            count: Maximum number of bars to return

        Returns:
            List of OHLCBar objects (stored in self.bars deque)
        """
        try:
            # Create SPX contract (or use specified symbol in future)
            contract = self.create_underlying_contract()
            if contract is None:
                self.logger.error("Failed to create contract for get_bars")
                return []

            self.logger.info(f"Getting bars for {contract.symbol}: duration={duration}, bar_size={bar_size}")

            # Request historical data
            bars = self.request_historical_data(
                contract=contract,
                duration=duration,
                bar_size=bar_size,
                what_to_show='TRADES',
                use_rth=False,
                keep_up_to_date=False
            )

            # Limit to requested count
            if len(bars) > count:
                bars = bars[-count:]

            # Store in deque
            self.bars.clear()
            for bar in bars:
                self.bars.append(bar)

            self.logger.info(f"Stored {len(self.bars)} bars in self.bars deque")

            return list(self.bars)

        except Exception as e:
            self.logger.error(f"Error in get_bars: {e}")
            return []

    # =========================================================================
    # Account and Position Methods
    # =========================================================================

    def request_account_summary(self, timeout: int = 10) -> AccountInfo:
        """
        Request account summary from IBKR.

        Retrieves account values including net liquidation, buying power,
        cash balance, and other key metrics.

        Args:
            timeout: Timeout in seconds for the request

        Returns:
            AccountInfo object with updated values
        """
        try:
            self.logger.info("Requesting account summary...")

            # Get account summary using ib_insync
            account_values = self.ib.accountSummary()

            if not account_values:
                self.logger.warning("No account summary data received")
                return self.account_info

            # Update account_info with values
            for av in account_values:
                tag = av.tag
                value = av.value

                try:
                    if tag == 'NetLiquidation':
                        self.account_info.net_liquidation = float(value)
                    elif tag == 'BuyingPower':
                        self.account_info.buying_power = float(value)
                    elif tag == 'TotalCashValue':
                        self.account_info.cash_balance = float(value)
                    elif tag == 'UnrealizedPnL':
                        self.account_info.unrealized_pnl = float(value)
                    elif tag == 'RealizedPnL':
                        self.account_info.realized_pnl = float(value)
                    elif tag == 'MaintMarginReq':
                        self.account_info.margin_used = float(value)
                    elif tag == 'AvailableFunds':
                        self.account_info.available_funds = float(value)
                    elif tag == 'ExcessLiquidity':
                        self.account_info.excess_liquidity = float(value)
                except (ValueError, TypeError) as e:
                    self.logger.debug(f"Could not convert {tag}={value}: {e}")
                    continue

            self.logger.info(
                f"Account summary updated: "
                f"NetLiq={self.account_info.net_liquidation}, "
                f"BuyingPower={self.account_info.buying_power}"
            )

            return self.account_info

        except Exception as e:
            self.logger.error(f"Error requesting account summary: {e}")
            return self.account_info

    def request_positions(self) -> Dict[str, PositionInfo]:
        """
        Request current positions from IBKR.

        Retrieves all open positions and converts them to PositionInfo objects.

        Returns:
            Dictionary of position key to PositionInfo objects
        """
        try:
            self.logger.debug("Requesting positions...")

            # Get positions using ib_insync
            positions = self.ib.positions()

            if not positions:
                self.logger.debug("No open positions found")
                self.positions.clear()
                return {}

            # Clear existing positions and rebuild
            self.positions.clear()

            for pos in positions:
                contract = pos.contract
                position_size = pos.position
                avg_cost = pos.avgCost

                # Create a unique key for the position
                if contract.secType == 'OPT':
                    # For options: symbol_expiry_strike_right
                    key = f"{contract.symbol}_{contract.lastTradeDateOrContractMonth}_{contract.strike}_{contract.right}"
                else:
                    # For other securities: symbol_secType
                    key = f"{contract.symbol}_{contract.secType}"

                # Create PositionInfo object with all required fields
                position_info = PositionInfo(
                    account=self.account_id or '',
                    symbol=contract.symbol,
                    sec_type=contract.secType,
                    strike=contract.strike if contract.secType == 'OPT' else 0.0,
                    right=contract.right if contract.secType == 'OPT' else '',
                    expiry=contract.lastTradeDateOrContractMonth if contract.secType == 'OPT' else '',
                    position=position_size,
                    avg_cost=avg_cost,
                    market_value=position_size * avg_cost if avg_cost else 0.0,
                    unrealized_pnl=0.0,  # Will be updated with market data
                    contract=contract
                )

                self.positions[key] = position_info

            # Only log at DEBUG level to reduce noise in monitoring loop
            self.logger.debug(f"Retrieved {len(self.positions)} positions")

            return self.positions

        except Exception as e:
            self.logger.error(f"Error requesting positions: {e}")
            return {}

    def get_account_balance(self) -> Optional[float]:
        """
        Get the net liquidation value from account info.

        Returns:
            Net liquidation value, or None if not available
        """
        if self.account_info.net_liquidation is None:
            self.logger.debug("Net liquidation not available, requesting account summary...")
            self.request_account_summary()

        return self.account_info.net_liquidation

    def get_buying_power(self) -> Optional[float]:
        """
        Get the buying power from account info.

        Returns:
            Buying power value, or None if not available
        """
        if self.account_info.buying_power is None:
            self.logger.debug("Buying power not available, requesting account summary...")
            self.request_account_summary()

        return self.account_info.buying_power

    def get_positions(self) -> List[Dict[str, Any]]:
        """
        Get the current positions as a list of dictionaries.

        Returns:
            List of position dictionaries with keys: symbol, sec_type, position, etc.
        """
        # First refresh positions from IBKR
        self.request_positions()

        # Convert to list of dicts for engine compatibility
        positions_list = []
        for key, pos_info in self.positions.items():
            pos_dict = {
                'symbol': pos_info.symbol,
                'sec_type': pos_info.sec_type,
                'position': pos_info.position,
                'avg_cost': pos_info.avg_cost,
                'market_value': pos_info.market_value,
                'unrealized_pnl': pos_info.unrealized_pnl,
            }
            # Add option-specific fields if applicable
            if pos_info.expiry:
                pos_dict['expiry'] = pos_info.expiry
            if pos_info.strike:
                pos_dict['strike'] = pos_info.strike
            if pos_info.right:
                pos_dict['right'] = pos_info.right

            positions_list.append(pos_dict)

        return positions_list

    def get_historical_iv(
        self,
        symbol: str,
        duration: str = '1 Y',
        bar_size: str = '1 day'
    ) -> List[Dict[str, Any]]:
        """
        Request historical implied volatility data for a symbol.

        Args:
            symbol: Symbol to get IV data for (e.g., 'SPX')
            duration: Time duration (e.g., '1 Y', '6 M', '1 M')
            bar_size: Bar size (e.g., '1 day', '1 week')

        Returns:
            List of dictionaries containing IV data points with keys:
            - timestamp: datetime of the bar
            - open: Opening IV value
            - high: High IV value
            - low: Low IV value
            - close: Closing IV value
        """
        try:
            self.logger.debug(
                f"Requesting historical IV for {symbol}: "
                f"duration={duration}, bar_size={bar_size}"
            )

            # Create index contract for the symbol
            contract = Index(symbol, 'CBOE')

            # Qualify the contract
            qualified = self.ib.qualifyContracts(contract)
            if not qualified:
                self.logger.error(f"Failed to qualify contract for {symbol}")
                return []

            contract = qualified[0]

            # Request historical data with OPTION_IMPLIED_VOLATILITY
            bars = self.ib.reqHistoricalData(
                contract,
                endDateTime='',
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow='OPTION_IMPLIED_VOLATILITY',
                useRTH=True,
                formatDate=1,
                keepUpToDate=False,
                timeout=30
            )

            if not bars:
                self.logger.warning(f"No historical IV data returned for {symbol}")
                return []

            # Convert to list of dictionaries
            # IBKR returns timestamps in US Eastern Time
            iv_data = []
            for bar in bars:
                try:
                    # Parse the date and ensure US Eastern timezone
                    if isinstance(bar.date, datetime):
                        if bar.date.tzinfo is None:
                            timestamp = bar.date.replace(tzinfo=US_EASTERN)
                        else:
                            timestamp = bar.date
                    elif ' ' in str(bar.date):
                        date_str = str(bar.date)
                        try:
                            timestamp = datetime.strptime(date_str, "%Y%m%d %H:%M:%S").replace(tzinfo=US_EASTERN)
                        except ValueError:
                            timestamp = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=US_EASTERN)
                    else:
                        date_str = str(bar.date)
                        try:
                            timestamp = datetime.strptime(date_str, "%Y%m%d").replace(tzinfo=US_EASTERN)
                        except ValueError:
                            timestamp = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=US_EASTERN)

                    iv_point = {
                        'timestamp': timestamp,
                        'open': bar.open,
                        'high': bar.high,
                        'low': bar.low,
                        'close': bar.close
                    }
                    iv_data.append(iv_point)
                except Exception as e:
                    self.logger.warning(f"Error converting IV bar: {e}")
                    continue

            self.logger.debug(f"Retrieved {len(iv_data)} historical IV data points for {symbol}")

            return iv_data

        except Exception as e:
            self.logger.error(f"Error requesting historical IV: {e}")
            return []

    # =========================================================================
    # Option Chain Methods
    # =========================================================================

    def create_option_contract(
        self,
        symbol: str,
        strike: float,
        expiry: str,
        option_type: OptionType,
        exchange: str = 'SMART',
        multiplier: str = '100',
        trading_class: str = None
    ) -> Optional[Option]:
        """
        Create and qualify an option contract using ib_insync's Option class.

        Args:
            symbol: Underlying symbol (e.g., 'SPX')
            strike: Strike price
            expiry: Expiry date in YYYYMMDD format
            option_type: OptionType.CALL or OptionType.PUT
            exchange: Exchange (default: 'SMART')
            multiplier: Contract multiplier (default: '100')
            trading_class: Trading class (default: None, uses config.underlying_trading_class)

        Returns:
            Qualified Option contract, or None if qualification fails
        """
        try:
            # Use config trading class if not specified
            if trading_class is None:
                trading_class = self.config.underlying_trading_class

            # Determine right (C or P)
            right = 'C' if option_type == OptionType.CALL else 'P'

            self.logger.info(
                f"Creating option contract: {symbol} {strike} {expiry} {right} "
                f"(exchange={exchange}, tradingClass={trading_class})"
            )

            # Create Option contract using ib_insync
            # Match test script settings: SMART exchange, currency='USD', tradingClass from config
            contract = Option(
                symbol=symbol,
                lastTradeDateOrContractMonth=expiry,
                strike=strike,
                right=right,
                exchange=exchange,
                currency='USD',  # Explicitly set USD currency for better quote availability
                multiplier=multiplier,
                tradingClass=trading_class
            )

            # Qualify the contract with IBKR
            qualified = self.ib.qualifyContracts(contract)

            if qualified:
                self.logger.info(f"Option contract qualified: {qualified[0]}")
                return qualified[0]
            else:
                self.logger.error(f"Failed to qualify option contract: {symbol} {strike} {expiry} {right}")
                return None

        except Exception as e:
            self.logger.error(f"Error creating option contract: {e}")
            return None

    def get_option_chain(
        self,
        symbol: str,
        expiry: str,
        underlying_price: Optional[float] = None,
        refresh: bool = False,
        timeout: int = 60
    ) -> Optional[OptionChain]:
        """
        Get option chain for a specific expiry date.

        Requests contract details for the given expiry using a wildcard strike
        to get all available strikes. Builds an OptionChain object with calls
        and puts dictionaries keyed by strike price.

        Args:
            symbol: Underlying symbol (e.g., 'SPX')
            expiry: Expiry date in YYYYMMDD format
            underlying_price: Current underlying price (optional, fetched if not provided)
            refresh: Force refresh from IBKR even if cached
            timeout: Request timeout in seconds

        Returns:
            OptionChain object or None if request fails
        """
        cache_key = f"{symbol}_{expiry}"

        # Return cached if available and not refreshing
        if cache_key in self._chains_cache and not refresh:
            chain = self._chains_cache[cache_key]
            if underlying_price is not None:
                chain.underlying_price = underlying_price
            self.logger.debug(f"Returning cached option chain for {cache_key}")
            return chain

        try:
            # Get underlying price if not provided
            if underlying_price is None:
                underlying_price = self.get_latest_price()
                if underlying_price is None:
                    self.logger.error("Could not get underlying price for option chain")
                    underlying_price = 0.0

            self.logger.debug(f"Requesting option chain for {symbol} expiry {expiry}")

            # Create Option contract with wildcard strike (strike=0 or omitted)
            # to get all available strikes
            contract = Option(
                symbol=symbol,
                lastTradeDateOrContractMonth=expiry,
                strike=0,  # Wildcard to get all strikes
                right='',  # Empty to get both calls and puts
                exchange='SMART',
                currency='USD',  # Explicitly set USD currency
                tradingClass=self.config.underlying_trading_class if symbol == self.config.underlying_symbol else ''
            )

            # Request contract details
            contract_details_list = self.ib.reqContractDetails(contract)

            # Allow time for response
            self.ib.sleep(min(timeout, 5))

            if not contract_details_list:
                self.logger.warning(f"No contracts found for {symbol} expiry {expiry}")
                return None

            self.logger.debug(f"Received {len(contract_details_list)} contract details")

            # Build option chain
            chain = OptionChain(
                symbol=symbol,
                expiry=expiry,
                underlying_price=underlying_price
            )

            strikes = set()

            for details in contract_details_list:
                opt_contract = details.contract
                strike = opt_contract.strike
                strikes.add(strike)

                option = OptionContract(
                    symbol=symbol,
                    strike=strike,
                    expiry=expiry,
                    option_type=OptionType.CALL if opt_contract.right == 'C' else OptionType.PUT,
                    contract=opt_contract
                )

                if opt_contract.right == 'C':
                    chain.calls[strike] = option
                else:
                    chain.puts[strike] = option

            chain.strikes = sorted(strikes)
            self.logger.debug(
                f"Built option chain: {len(chain.strikes)} strikes, "
                f"{len(chain.calls)} calls, {len(chain.puts)} puts"
            )

            # Cache the result
            self._chains_cache[cache_key] = chain

            return chain

        except Exception as e:
            self.logger.error(f"Error getting option chain: {e}")
            return None

    def get_option_chain_by_rule(
        self,
        symbol: str,
        expiry_rule: ExpiryRule,
        underlying_price: Optional[float] = None,
        refresh: bool = False
    ) -> Optional[OptionChain]:
        """
        Get option chain using an expiry rule to calculate the expiry date.

        Handles overnight sessions for IST timezone with 6:30 AM cutoff.
        Between midnight and 6:30 AM IST, the trading day is still the
        previous calendar day.

        Args:
            symbol: Underlying symbol (e.g., 'SPX')
            expiry_rule: ExpiryRule enum (SAME_DAY, PLUS_ONE_DAY, NEAREST, NEAREST_WEEKLY)
            underlying_price: Current underlying price (optional)
            refresh: Force refresh from IBKR

        Returns:
            OptionChain object or None if request fails
        """
        try:
            expiry = self._resolve_expiry(expiry_rule)
            if not expiry:
                self.logger.error(f"Could not resolve expiry for rule: {expiry_rule}")
                return None

            self.logger.debug(f"Resolved expiry rule {expiry_rule.value} to date {expiry}")
            return self.get_option_chain(symbol, expiry, underlying_price, refresh)

        except Exception as e:
            self.logger.error(f"Error getting option chain by rule: {e}")
            return None

    def _resolve_expiry(self, expiry_rule: ExpiryRule) -> Optional[str]:
        """
        Resolve expiry rule to actual date string (YYYYMMDD).

        Handles overnight sessions for IST timezone:
        - US market closes at 4 PM EST = 2:30 AM IST next day
        - Extended hours end at 8 PM EST = 6:30 AM IST next day
        - If current IST time is between midnight and 6:30 AM,
          the trading day is still the previous calendar day.

        Args:
            expiry_rule: ExpiryRule enum

        Returns:
            Expiry date string in YYYYMMDD format
        """
        now_et = datetime.now(US_EASTERN)
        today = now_et.date()

        # Use US Eastern time to determine trading day
        # US market hours: 9:30 AM - 4:00 PM ET
        # Before market open (before 9:30 AM ET) -> use previous trading day for expiry lookup
        # During/after market (9:30 AM - midnight ET) -> use current day
        market_open = dt_time(9, 30)

        if now_et.time() < market_open:
            # Before market open - use previous trading day
            trading_day = today - timedelta(days=1)
            # Skip weekends going backwards
            while trading_day.weekday() >= 5:  # Saturday=5, Sunday=6
                trading_day -= timedelta(days=1)
            self.logger.info(f"Pre-market session - using trading day: {trading_day}")
        else:
            # During or after market hours - use current day
            trading_day = today
            # Skip weekends forward
            while trading_day.weekday() >= 5:
                trading_day += timedelta(days=1)

        # SAME_DAY: Use the current trading day's options (0DTE)
        if expiry_rule == ExpiryRule.SAME_DAY:
            return trading_day.strftime("%Y%m%d")

        if expiry_rule == ExpiryRule.PLUS_ONE_DAY:
            # In overnight IST session: trading_day = US today (Jan 22)
            # +1 day = US tomorrow (Jan 23) which is valid for 0DTE
            next_day = trading_day + timedelta(days=1)
            # Skip weekends
            while next_day.weekday() >= 5:
                next_day += timedelta(days=1)
            return next_day.strftime("%Y%m%d")

        elif expiry_rule in (ExpiryRule.NEAREST, ExpiryRule.NEAREST_WEEKLY):
            return trading_day.strftime("%Y%m%d")

        # Default to trading day
        return trading_day.strftime("%Y%m%d")

    def find_option_by_delta(
        self,
        symbol: str,
        underlying_price: float,
        option_type: OptionType,
        target_delta: float,
        expiry_rule: ExpiryRule,
        exclude_strikes: List[float] = None,
        reference_strike: float = None,
        max_spread_width: float = None
    ) -> Optional[OptionContract]:
        """
        Find an option contract closest to the target delta.

        Gets the option chain and searches for the option with delta
        closest to the target. Uses moneyness estimation as fallback
        when Greeks are unavailable.

        Args:
            symbol: Underlying symbol (e.g., 'SPX')
            underlying_price: Current underlying price
            option_type: OptionType.CALL or OptionType.PUT
            target_delta: Target delta (e.g., 0.30 for 30 delta)
            expiry_rule: ExpiryRule for expiry date calculation
            exclude_strikes: List of strikes to exclude (already used by other legs)
            reference_strike: First leg's strike (for spread width constraint)
            max_spread_width: Maximum spread width in points (e.g., 25.0)

        Returns:
            OptionContract closest to target delta, or None if not found
        """
        if exclude_strikes is None:
            exclude_strikes = []

        try:
            # Get option chain
            chain = self.get_option_chain_by_rule(symbol, expiry_rule, underlying_price)

            if chain is None:
                self.logger.error(f"Could not get option chain for {symbol}")
                return None

            # Get contracts for the specified option type
            contracts = chain.calls if option_type == OptionType.CALL else chain.puts

            if not contracts:
                self.logger.error(f"No {option_type.value} contracts found in chain")
                return None

            # Build list of valid contracts (not excluded, within spread width if applicable)
            valid_contracts = []
            for strike, contract in contracts.items():
                if contract.delta is None:
                    continue
                if strike in exclude_strikes:
                    continue
                # Apply spread width constraint if we have a reference strike
                if reference_strike is not None and max_spread_width is not None:
                    if abs(strike - reference_strike) > max_spread_width:
                        continue
                valid_contracts.append((strike, contract))

            if valid_contracts:
                # Find contract closest to target delta
                target_abs = abs(target_delta)
                best_strike, best_contract = min(
                    valid_contracts,
                    key=lambda x: abs(abs(x[1].delta) - target_abs)
                )
                spread_info = ""
                if reference_strike is not None:
                    spread_info = f" [spread_width={abs(best_strike - reference_strike):.0f}]"
                self.logger.debug(
                    f"Found option by delta: {symbol} {best_strike} {option_type.value} "
                    f"(delta={best_contract.delta:.3f}, target={target_delta:.3f})"
                    + (f" [excluded: {exclude_strikes}]" if exclude_strikes else "")
                    + spread_info
                )
                return best_contract

            # Fallback: Use moneyness estimation when Greeks unavailable
            self.logger.debug("Greeks unavailable, using moneyness estimation")
            return self._estimate_by_moneyness(contracts, option_type, target_delta, underlying_price, exclude_strikes, reference_strike, max_spread_width)

        except Exception as e:
            self.logger.error(f"Error finding option by delta: {e}")
            return None

    def _estimate_by_moneyness(
        self,
        contracts: Dict[float, OptionContract],
        option_type: OptionType,
        target_delta: float,
        underlying_price: float,
        exclude_strikes: List[float] = None,
        reference_strike: float = None,
        max_spread_width: float = None
    ) -> Optional[OptionContract]:
        """
        Estimate strike based on delta approximation when Greeks unavailable.

        Uses a simple moneyness-based estimation:
        - For calls: higher delta = closer to or ITM
        - For puts: higher delta (abs) = closer to or ITM

        Args:
            contracts: Dictionary of strike -> OptionContract
            option_type: OptionType.CALL or OptionType.PUT
            target_delta: Target delta
            underlying_price: Current underlying price
            exclude_strikes: List of strikes to exclude (already used by other legs)
            reference_strike: First leg's strike (for spread width constraint)
            max_spread_width: Maximum spread width in points (e.g., 25.0)

        Returns:
            OptionContract closest to estimated strike, or None
        """
        if exclude_strikes is None:
            exclude_strikes = []

        if not contracts:
            return None

        # Filter out excluded strikes and apply spread width constraint
        available_strikes = []
        for s in contracts.keys():
            if s in exclude_strikes:
                continue
            # Apply spread width constraint if we have a reference strike
            if reference_strike is not None and max_spread_width is not None:
                if abs(s - reference_strike) > max_spread_width:
                    continue
            available_strikes.append(s)

        if not available_strikes:
            self.logger.warning("No available strikes after exclusion/spread width filter")
            return None

        # Estimate target strike based on delta
        # Delta 0.50 = ATM, lower delta = more OTM
        if option_type == OptionType.CALL:
            # Calls: lower delta = higher strike (more OTM)
            otm_factor = 1 + (0.50 - abs(target_delta)) * 0.1
            target_strike = underlying_price * otm_factor
        else:
            # Puts: lower delta = lower strike (more OTM)
            otm_factor = 1 - (0.50 - abs(target_delta)) * 0.1
            target_strike = underlying_price * otm_factor

        # Find strike closest to target from available strikes
        best_strike = min(available_strikes, key=lambda x: abs(x - target_strike))

        spread_info = ""
        if reference_strike is not None:
            spread_info = f" [spread_width={abs(best_strike - reference_strike):.0f}]"

        self.logger.debug(
            f"Estimated strike by moneyness: target_strike={target_strike:.2f}, "
            f"selected={best_strike} for delta={target_delta}"
            + (f" (excluded: {exclude_strikes})" if exclude_strikes else "")
            + spread_info
        )

        return contracts[best_strike]

    def get_option_quotes(
        self,
        options: List[OptionContract],
        timeout: int = 5
    ) -> bool:
        """
        Request market data for multiple option contracts.

        Updates bid/ask/last/mid and Greeks on each OptionContract in place.

        Args:
            options: List of OptionContract objects to get quotes for
            timeout: Timeout in seconds for market data requests

        Returns:
            True if all quotes received successfully, False otherwise
        """
        if not options:
            return True

        try:
            self.logger.debug(f"Requesting quotes for {len(options)} options")

            # Request market data for each option
            tickers = []
            for option in options:
                # Request market data
                ticker = self.ib.reqMktData(option.contract, '', False, False)
                tickers.append((option, ticker))

            # Wait for data to arrive
            self.ib.sleep(timeout)

            all_received = True

            # Process received data and update option contracts
            for option, ticker in tickers:
                if ticker is None:
                    self.logger.warning(f"No ticker for {option.symbol} {option.strike}")
                    all_received = False
                    continue

                # Update bid/ask/last
                option.bid = ticker.bid if ticker.bid and ticker.bid > 0 else 0.0
                option.ask = ticker.ask if ticker.ask and ticker.ask > 0 else 0.0
                option.last = ticker.last if ticker.last and ticker.last > 0 else 0.0

                # Calculate mid price
                if option.bid > 0 and option.ask > 0:
                    option.mid = (option.bid + option.ask) / 2
                else:
                    option.mid = option.last if option.last > 0 else 0.0

                # Update Greeks if available
                if ticker.modelGreeks:
                    option.implied_vol = ticker.modelGreeks.impliedVol
                    option.delta = ticker.modelGreeks.delta
                    option.gamma = ticker.modelGreeks.gamma
                    option.vega = ticker.modelGreeks.vega
                    option.theta = ticker.modelGreeks.theta
                    option.underlying_price = ticker.modelGreeks.undPrice

                self.logger.debug(
                    f"Quote: {option.symbol} {option.strike} {option.option_type.value} "
                    f"bid={option.bid:.2f} ask={option.ask:.2f} mid={option.mid:.2f} "
                    f"delta={option.delta}"
                )

            # Cancel market data subscriptions
            for option, ticker in tickers:
                try:
                    self.ib.cancelMktData(option.contract)
                except Exception:
                    pass  # Ignore cancel errors

            self.logger.debug(f"Completed quotes for {len(options)} options")
            return all_received

        except Exception as e:
            self.logger.error(f"Error getting option quotes: {e}")
            return False

    # =========================================================================
    # Order Placement Methods
    # =========================================================================

    def create_limit_order(
        self,
        action: str,
        quantity: int,
        limit_price: float,
        tif: str = 'DAY'
    ) -> Order:
        """
        Create a limit order.

        Args:
            action: 'BUY' or 'SELL'
            quantity: Number of contracts
            limit_price: Limit price for the order
            tif: Time in force ('DAY', 'GTC', 'IOC', 'FOK')

        Returns:
            Configured Order object
        """
        order = Order()
        order.action = action
        order.totalQuantity = quantity
        order.orderType = 'LMT'
        order.lmtPrice = limit_price
        order.tif = tif
        order.transmit = True
        return order

    def create_market_order(
        self,
        action: str,
        quantity: int,
        tif: str = 'DAY'
    ) -> Order:
        """
        Create a market order.

        Args:
            action: 'BUY' or 'SELL'
            quantity: Number of contracts
            tif: Time in force ('DAY', 'GTC', 'IOC', 'FOK')

        Returns:
            Configured Order object
        """
        order = Order()
        order.action = action
        order.totalQuantity = quantity
        order.orderType = 'MKT'
        order.tif = tif
        order.transmit = True
        return order

    def place_order(self, contract: Contract, order: Order) -> OrderInfo:
        """
        Place an order with IBKR.

        Args:
            contract: Contract to trade
            order: Order to place

        Returns:
            OrderInfo object with order details and tracking
        """
        try:
            self.logger.info(
                f"Placing order: {order.action} {order.totalQuantity} "
                f"{contract.symbol} @ {order.orderType}"
            )

            # Place the order
            trade = self.ib.placeOrder(contract, order)

            # Allow time for order acknowledgment
            self.ib.sleep(0.5)

            # Create OrderInfo object
            order_info = OrderInfo(
                order_id=trade.order.orderId,
                contract=contract,
                order=order,
                action=order.action,
                quantity=int(order.totalQuantity),
                order_type=order.orderType,
                status=OrderStatus.PENDING
            )

            # Store the order
            self.orders[trade.order.orderId] = order_info

            self.logger.info(
                f"Order placed successfully, order_id: {trade.order.orderId}, "
                f"status: {trade.orderStatus.status}"
            )

            return order_info

        except Exception as e:
            self.logger.error(f"Error placing order: {e}")
            raise

    def place_spread_order(
        self,
        legs: List[Dict[str, Any]],
        quantity: int = 1,
        use_market: bool = False,
        slippage: float = 0.05,
        enable_chasing: bool = True,
        chase_interval: float = 3.0,
        max_chase_time: float = 60.0,
        max_price_deviation_pct: float = 3.0
    ) -> ComboOrder:
        """
        Place a spread/combo order with multiple legs and optional price chasing.

        Args:
            legs: List of leg dictionaries, each containing:
                - 'contract': Qualified Option contract with conId
                - 'action': 'BUY' or 'SELL'
                - 'quantity': Ratio for this leg (default 1)
                - 'price': Optional price for this leg (used for net price calc)
            quantity: Total quantity of spreads to trade
            use_market: If True, use market order; if False, use limit order with chasing
            slippage: Slippage to add/subtract from calculated limit price
            enable_chasing: If True, chase the limit price if not filled (default: True)
            chase_interval: Seconds between price updates when chasing (default: 3.0)
            max_chase_time: Maximum time to chase before giving up (default: 60.0)
            max_price_deviation_pct: Max % change per chase to avoid IB rejection (default: 3.0)

        Returns:
            ComboOrder object with order tracking
        """
        try:
            self.logger.info(f"Placing spread order with {len(legs)} legs, quantity={quantity}")

            # Create the combo contract
            combo = Contract()
            combo.symbol = 'SPX'
            combo.secType = 'BAG'
            combo.currency = 'USD'
            combo.exchange = 'SMART'

            # Build combo legs
            combo_legs = []
            net_price = 0.0

            for leg in legs:
                cl = ComboLeg()
                cl.conId = leg['contract'].conId
                cl.ratio = leg.get('quantity', 1)
                cl.action = leg['action']
                cl.exchange = 'SMART'
                combo_legs.append(cl)

                # Calculate net price if provided
                if 'price' in leg and leg['price'] is not None:
                    leg_price = leg['price']
                    if leg['action'] == 'BUY':
                        net_price -= leg_price * leg.get('quantity', 1)
                    else:  # SELL
                        net_price += leg_price * leg.get('quantity', 1)

                self.logger.debug(
                    f"  Leg: conId={cl.conId}, action={cl.action}, ratio={cl.ratio}"
                )

            combo.comboLegs = combo_legs

            # Create the order
            order = Order()
            order.action = 'BUY'  # For spreads, we always BUY the combo
            order.totalQuantity = quantity
            # Issue 5, 6: Use DAY by default to prevent stale orders filling later
            # GTC orders can remain live and fill unexpectedly after engine stops
            order_tif = 'DAY'  # Default to DAY for safety
            if self.config:
                order_tif = getattr(self.config, 'order_tif', 'DAY')
            order.tif = order_tif
            order.transmit = True
            # Override TWS precautionary settings that reject orders based on price %
            order.overridePercentageConstraints = True
            # DO NOT use NonGuaranteed for 4-leg combos!

            if use_market:
                order.orderType = 'MKT'
                self.logger.info("Using MARKET order for spread")
            else:
                order.orderType = 'LMT'
                # For IBKR combos: positive lmtPrice = pay (debit), negative = receive (credit)
                # net_price is calculated as: SELL legs (+) - BUY legs (-)
                # For credit spreads: net_price > 0, we need negative limit price to receive credit
                # For debit spreads: net_price < 0, we need positive limit price to pay debit

                # Get actual market mid-price for initial limit (like old project approach)
                market_mid_price = self.get_spread_market_price(legs, timeout=2)

                if market_mid_price is not None:
                    # Use market mid-price directly (no slippage - chase to market)
                    limit_price = -market_mid_price  # Negate for IBKR combo convention
                    self.logger.info(f"Using market mid-price for initial limit: market={market_mid_price:.2f}, limit={limit_price:.2f}")
                else:
                    # Fallback to theoretical net_price if market data unavailable
                    limit_price = -net_price
                    # Apply slippage only when using theoretical price
                    if limit_price < 0:  # Credit spread
                        limit_price = limit_price + slippage
                    else:  # Debit spread
                        limit_price = limit_price - slippage
                    self.logger.info(f"Using theoretical price (market data unavailable): net={net_price}, limit={limit_price:.2f}")

                order.lmtPrice = self.round_to_spx_tick(limit_price)
                self.logger.info(f"Using LIMIT order for spread, price={order.lmtPrice}")

            # Place the order
            trade = self.ib.placeOrder(combo, order)

            # Allow time for order acknowledgment
            self.ib.sleep(0.5)

            self.logger.info(
                f"Spread order placed: order_id={trade.order.orderId}, "
                f"initial_status={trade.orderStatus.status}, "
                f"order_type={order.orderType}, limit_price={order.lmtPrice if order.orderType == 'LMT' else 'N/A'}"
            )

            # If using limit order and chasing is enabled, chase the price
            if order.orderType == 'LMT' and enable_chasing:
                self.logger.info(f"Price chasing enabled - will update limit price every {chase_interval}s for up to {max_chase_time}s")

                import time as time_module
                start_time = time_module.time()
                last_chase_time = start_time
                last_modified_price = order.lmtPrice

                while time_module.time() - start_time < max_chase_time:
                    self.ib.sleep(0.5)

                    # Check current order status
                    current_status = trade.orderStatus.status

                    if current_status == 'Filled':
                        fill_price = trade.orderStatus.avgFillPrice
                        self.logger.info(f"✅ Spread order {trade.order.orderId} FILLED @ ${fill_price:.2f}")
                        break
                    elif current_status in ['Cancelled', 'ApiCancelled', 'Inactive']:
                        self.logger.warning(f"Spread order {trade.order.orderId} cancelled: {current_status}")
                        break

                    # Only chase if order is still active
                    if current_status in ['Submitted', 'PreSubmitted']:
                        current_time = time_module.time()
                        if (current_time - last_chase_time) >= chase_interval:
                            last_chase_time = current_time

                            # Get current market price for the spread
                            current_market_price = self.get_spread_market_price(legs, timeout=2)

                            if current_market_price is not None:
                                # Determine if this is a credit or debit spread based on current limit sign
                                is_credit_spread = order.lmtPrice < 0

                                # Use market mid-price directly (no slippage buffer)
                                # This matches the old project's proven approach
                                new_limit_price = self.round_to_spx_tick(-current_market_price)

                                # Check if price change is within IB's percentage constraint
                                # Use absolute values for percentage calculation to handle negative prices
                                price_change_pct = abs((abs(new_limit_price) - abs(last_modified_price)) / abs(last_modified_price)) * 100 if last_modified_price != 0 else 0

                                if price_change_pct > max_price_deviation_pct:
                                    self.logger.debug(f"Price change {price_change_pct:.2f}% exceeds {max_price_deviation_pct}% constraint")
                                    # Use a price within constraint, chasing toward target
                                    safe_pct = max_price_deviation_pct - 0.5  # 0.5% buffer

                                    # Determine direction: chase toward target (new_limit_price before constraint)
                                    target_limit = new_limit_price  # The unconstrained target

                                    if is_credit_spread:
                                        # Credit spread: both limit and target are negative
                                        # Chase toward target's absolute value
                                        if abs(target_limit) > abs(last_modified_price):
                                            # Target wants more credit (more negative) - increase abs value
                                            new_limit_price = self.round_to_spx_tick(last_modified_price * (1 + safe_pct/100))
                                        else:
                                            # Target wants less credit (less negative) - decrease abs value
                                            new_limit_price = self.round_to_spx_tick(last_modified_price * (1 - safe_pct/100))
                                    else:
                                        # Debit spread: limit is positive
                                        if target_limit > last_modified_price:
                                            # Target is higher - chase up
                                            new_limit_price = self.round_to_spx_tick(last_modified_price * (1 + safe_pct/100))
                                        else:
                                            # Target is lower - chase down
                                            new_limit_price = self.round_to_spx_tick(last_modified_price * (1 - safe_pct/100))
                                    self.logger.debug(f"Adjusted limit price to ${new_limit_price:.2f} to stay within constraint")

                                # Only modify if price is different
                                if abs(new_limit_price - order.lmtPrice) > 0.01:
                                    self.logger.info(f"Chasing spread - updating limit from ${order.lmtPrice:.2f} to ${new_limit_price:.2f} (market: ${current_market_price:.2f})")

                                    # Modify the order
                                    order.lmtPrice = new_limit_price
                                    self.ib.placeOrder(combo, order)

                                    # Wait a moment to see if modification was accepted
                                    self.ib.sleep(1)

                                    # Check if order was filled (Error 434 means order already filled/cancelled)
                                    if trade.orderStatus.status == 'Filled':
                                        fill_price = trade.orderStatus.avgFillPrice
                                        self.logger.info(f"✅ Spread order {trade.order.orderId} FILLED @ ${fill_price:.2f}")
                                        break
                                    elif trade.orderStatus.status in ['Cancelled', 'Inactive', 'ApiCancelled']:
                                        self.logger.warning(f"Order modification rejected - order status: {trade.orderStatus.status}")
                                        break
                                    elif trade.order.totalQuantity == 0:
                                        # Error 434 scenario - order was filled
                                        self.logger.info(f"✅ Spread order {trade.order.orderId} appears FILLED (quantity=0)")
                                        break
                                    else:
                                        last_modified_price = new_limit_price  # Update last successful price
                            else:
                                self.logger.debug("Could not get valid spread market price for chasing")

                # Check final status after chasing
                final_status = trade.orderStatus.status
                if final_status != 'Filled':
                    self.logger.warning(f"Spread order {trade.order.orderId} not filled after {max_chase_time}s - Final status: {final_status}")
                    self.logger.warning("Order will remain active - aggressive fallback disabled by user")
                    # Order remains working at the last chased price
                    # It may still fill if market conditions improve

            # Generate combo_id
            combo_id = f"SPREAD_{trade.order.orderId}_{datetime.now(US_EASTERN).strftime('%Y%m%d_%H%M%S')}"

            # Create leg OrderInfo objects
            leg_orders = []
            for i, leg in enumerate(legs):
                leg_order_info = OrderInfo(
                    order_id=trade.order.orderId,
                    contract=leg['contract'],
                    order=order,
                    action=leg['action'],
                    quantity=leg.get('quantity', 1) * quantity,
                    order_type=order.orderType,
                    status=OrderStatus.PENDING
                )
                leg_orders.append(leg_order_info)

            # Create ComboOrder
            combo_order = ComboOrder(
                combo_id=combo_id,
                leg_orders=leg_orders,
                status=OrderStatus.PENDING
            )

            # Store the combo order and trade reference
            self.combo_orders[combo_id] = combo_order
            self.combo_orders[combo_id]._trade = trade  # Store trade reference for status checking

            self.logger.info(
                f"Spread order placed: combo_id={combo_id}, "
                f"order_id={trade.order.orderId}, status={trade.orderStatus.status}"
            )

            return combo_order

        except Exception as e:
            self.logger.error(f"Error placing spread order: {e}")
            raise

    def place_spread_with_bracket(
        self,
        legs: List[Dict[str, Any]],
        quantity: int = 1,
        credit_target_pct: float = 0.70,
        credit_sl_pct: float = 0.30,
        debit_target_pct: float = 1.50,
        debit_sl_pct: float = 0.50,
        slippage: float = 0.05,
        sl_limit_offset: float = 0.50,  # $ offset from SL trigger to limit price
        enable_chasing: bool = True,
        chase_interval: float = 3.0,
        max_chase_time: float = 60.0,
        on_entry_filled: callable = None,  # Callback for immediate state save after entry fills
        # Legacy parameters for backward compatibility
        target_pct: float = None,
        sl_pct: float = None,
    ) -> Dict[str, Any]:
        """
        Place spread order with bracket (SL + Target) using REVERSED COMBO EXIT approach.

        IBKR blocks SELL on SPX combo orders ("riskless combination" error).
        Solution: Use BUY on a REVERSED combo for exit orders.

        The broker automatically detects credit vs debit based on fill price and
        uses the appropriate target/SL percentages.

        Args:
            legs: List of leg dictionaries with 'contract', 'action', 'quantity', 'price'
            quantity: Number of spreads to trade
            credit_target_pct: For credit spreads - buy back at this % (0.50 = 50% profit)
            credit_sl_pct: For credit spreads - buy back at 1 + this % (1.50 = 150% loss)
            debit_target_pct: For debit spreads - sell at this % (1.50 = 50% profit)
            debit_sl_pct: For debit spreads - sell at this % (0.50 = 50% loss)
            slippage: Slippage for SL limit price
            enable_chasing: Enable price chasing for entry
            chase_interval: Seconds between chase updates
            max_chase_time: Max time to chase entry

        Returns:
            Dict with 'success', 'fill_price', 'entry_order_id', 'target_order_id',
            'sl_order_id', 'oca_group', 'error_message'
        """
        import time as time_module
        from datetime import datetime

        result = {
            'success': False,
            'fill_price': None,
            'entry_order_id': None,
            'target_order_id': None,
            'sl_order_id': None,
            'oca_group': None,
            'error_message': None
        }

        try:
            # ===== PHASE 1: ENTRY ORDER =====
            self.logger.info("")
            self.logger.info("=" * 60)
            self.logger.info("PHASE 1: ENTRY ORDER")
            self.logger.info("=" * 60)
            self.logger.info(f"  Legs: {len(legs)}, Quantity: {quantity}")

            # ===== STEP 1: BUILD ENTRY COMBO CONTRACT =====
            entry_combo = Contract()
            entry_combo.symbol = 'SPX'
            entry_combo.secType = 'BAG'
            entry_combo.currency = 'USD'
            entry_combo.exchange = 'SMART'

            entry_legs = []
            for leg in legs:
                cl = ComboLeg()
                cl.conId = leg['contract'].conId
                cl.ratio = leg.get('quantity', 1)
                cl.action = leg['action']
                cl.exchange = 'SMART'
                entry_legs.append(cl)
                self.logger.info(f"  Entry leg: conId={cl.conId}, action={cl.action}, strike={leg['contract'].strike}")

            entry_combo.comboLegs = entry_legs

            # ===== STEP 2: GET MARKET PRICE =====
            market_price = self.get_spread_market_price(legs, timeout=2)
            if market_price is None:
                result['error_message'] = "Could not get market price for spread"
                self.logger.error(result['error_message'])
                return result

            # Entry limit: For IBKR combo orders:
            #   negative price = receive credit (credit spread)
            #   positive price = pay debit (debit spread)
            # get_spread_market_price() may return inconsistent signs, so we determine
            # credit/debit from the leg structure: if SELL leg has higher strike, it's credit
            sell_strikes = [leg['contract'].strike for leg in legs if leg['action'] == 'SELL']
            buy_strikes = [leg['contract'].strike for leg in legs if leg['action'] == 'BUY']

            # Determine if credit spread based on leg structure
            # Credit Call Spread: SELL lower strike, BUY higher strike (bearish)
            # Credit Put Spread: SELL higher strike, BUY lower strike (bullish)
            # For both: we receive more premium than we pay = credit
            is_credit = False
            if sell_strikes and buy_strikes:
                # Check if this is a credit spread pattern
                # For calls: SELL strike < BUY strike = credit (we sell higher premium)
                # For puts: SELL strike > BUY strike = credit (we sell higher premium)
                right = legs[0]['contract'].right
                if right == 'C':
                    is_credit = max(sell_strikes) < max(buy_strikes)  # Call credit: sell lower, buy higher
                else:
                    is_credit = max(sell_strikes) > max(buy_strikes)  # Put credit: sell higher, buy lower

            # Set limit price with correct sign
            if is_credit:
                entry_limit = self.round_to_spx_tick(-abs(market_price))  # Negative for credit
            else:
                entry_limit = self.round_to_spx_tick(abs(market_price))   # Positive for debit

            self.logger.info(f"Entry: market={market_price:.2f}, limit={entry_limit:.2f} ({'CREDIT' if is_credit else 'DEBIT'})")

            # ===== STEP 3: PLACE ENTRY ORDER (STANDALONE) =====
            entry_order = Order()
            entry_order.action = 'BUY'
            entry_order.totalQuantity = quantity
            entry_order.orderType = 'LMT'
            entry_order.lmtPrice = entry_limit
            entry_order.tif = 'GTC'
            entry_order.transmit = True  # Transmit immediately
            entry_order.overridePercentageConstraints = True

            self.logger.info("Placing entry order...")
            entry_trade = self.ib.placeOrder(entry_combo, entry_order)
            self.ib.sleep(1)
            result['entry_order_id'] = entry_trade.order.orderId
            self.logger.info(f"Entry order placed: ID={entry_trade.order.orderId}")

            # ===== STEP 4: WAIT FOR ENTRY FILL (WITH OPTIONAL CHASING) =====
            self.logger.info("Waiting for entry fill...")
            start_time = time_module.time()
            last_chase_time = start_time
            current_limit = entry_limit

            while time_module.time() - start_time < max_chase_time:
                self.ib.sleep(1)
                status = entry_trade.orderStatus.status

                if status == 'Filled':
                    result['fill_price'] = entry_trade.orderStatus.avgFillPrice
                    result['entry_filled'] = True  # Mark entry as filled for state saving
                    self.logger.info(f"ENTRY FILLED @ {result['fill_price']:.2f}")

                    # Validate fill price is reasonable (within slippage tolerance)
                    expected_price = abs(entry_limit)  # Entry limit is negative for credits
                    actual_price = abs(result['fill_price'])
                    slippage_pct = abs(actual_price - expected_price) / expected_price * 100 if expected_price > 0 else 0
                    if slippage_pct > 5.0:  # More than 5% slippage
                        self.logger.warning(f"  ⚠️ HIGH SLIPPAGE: {slippage_pct:.1f}% (expected ${expected_price:.2f}, filled ${actual_price:.2f})")
                    else:
                        self.logger.info(f"  ✓ Fill price OK (slippage: {slippage_pct:.1f}%)")

                    # Note: Position verification done in engine.py after bracket orders placed

                    # CRITICAL: Call callback immediately to save state before exit orders
                    # This ensures state is saved even if crash happens during exit order placement
                    if on_entry_filled:
                        try:
                            on_entry_filled(result['fill_price'])
                        except Exception as cb_err:
                            self.logger.warning(f"Entry filled callback error: {cb_err}")
                    break

                elif status in ['Cancelled', 'ApiCancelled', 'Inactive']:
                    result['error_message'] = f"Entry order {status}"
                    for log in entry_trade.log:
                        if log.message:
                            self.logger.error(f"  {log.message}")
                    return result

                # Price chasing
                if enable_chasing and time_module.time() - last_chase_time >= chase_interval:
                    # Check status again before modifying - order might have filled
                    current_status = entry_trade.orderStatus.status
                    if current_status == 'Filled':
                        # Order filled during chase interval - break out of loop
                        result['fill_price'] = entry_trade.orderStatus.avgFillPrice
                        result['entry_filled'] = True
                        self.logger.info(f"ENTRY FILLED (during chase check) @ {result['fill_price']:.2f}")
                        if on_entry_filled:
                            try:
                                on_entry_filled(result['fill_price'])
                            except Exception as cb_err:
                                self.logger.warning(f"Entry filled callback error: {cb_err}")
                        break

                    # Get updated market price
                    new_market = self.get_spread_market_price(legs, timeout=1)
                    if new_market is not None:
                        new_limit = self.round_to_spx_tick(-new_market)
                        if new_limit != current_limit:
                            entry_order.lmtPrice = new_limit
                            try:
                                self.ib.placeOrder(entry_combo, entry_order)
                                self.logger.info(f"  Chasing: limit updated {current_limit:.2f} -> {new_limit:.2f}")
                                current_limit = new_limit
                            except AssertionError:
                                # Order likely filled between status check and modify attempt
                                self.ib.sleep(0.5)
                                if entry_trade.orderStatus.status == 'Filled':
                                    result['fill_price'] = entry_trade.orderStatus.avgFillPrice
                                    result['entry_filled'] = True
                                    self.logger.info(f"ENTRY FILLED (during chase modify) @ {result['fill_price']:.2f}")
                                    if on_entry_filled:
                                        try:
                                            on_entry_filled(result['fill_price'])
                                        except Exception as cb_err:
                                            self.logger.warning(f"Entry filled callback error: {cb_err}")
                                    break
                                else:
                                    self.logger.warning(f"Chase modify failed but order not filled: {entry_trade.orderStatus.status}")
                    last_chase_time = time_module.time()

                # Log status periodically
                if int(time_module.time() - start_time) % 10 == 0:
                    self.logger.info(f"  Entry status: {status}, elapsed: {int(time_module.time() - start_time)}s")

            if entry_trade.orderStatus.status != 'Filled':
                result['error_message'] = "Entry order not filled within timeout"
                self.logger.error(result['error_message'])
                self.ib.cancelOrder(entry_order)
                return result

            # Use actual fill price for exit calculations
            # Detect if credit or debit spread based on fill price
            is_credit_spread = result['fill_price'] < 0
            fill_value = abs(result['fill_price'])

            # ===== STEP 5: CALCULATE EXIT PRICES =====
            # IMPORTANT: IBKR blocks SELL on ALL SPX combos ("riskless combination")
            # So exit action is ALWAYS BUY on reversed combo
            # The difference is in the PRICE:
            # - Credit spread: BUY at POSITIVE price (paying to close)
            # - Debit spread: BUY at NEGATIVE price (receiving credit to close)

            # ===== PHASE 2: BRACKET (EXIT) ORDERS =====
            self.logger.info("")
            self.logger.info("=" * 60)
            self.logger.info("PHASE 2: BRACKET ORDERS (TARGET + STOP-LOSS)")
            self.logger.info("=" * 60)

            if is_credit_spread:
                # CREDIT SPREAD: received premium at entry (fill_price < 0)
                # Exit: BUY reversed combo at POSITIVE price (pay to close)
                # Target: lower positive price = less pay = more profit
                # SL: higher positive price = more pay = loss
                target_price = self.round_to_spx_tick(fill_value * credit_target_pct)
                sl_trigger_price = self.round_to_spx_tick(fill_value * (1 + credit_sl_pct))
                # Use configurable sl_limit_offset for stop-limit orders
                sl_limit_price = self.round_to_spx_tick(sl_trigger_price + sl_limit_offset)
                self.logger.info(f"  Type: CREDIT SPREAD")
                self.logger.info(f"  Entry credit: ${fill_value:.2f}")
                self.logger.info(f"  TARGET: BUY LMT @ ${target_price:.2f} (keep {(1-credit_target_pct)*100:.0f}% profit)")
                self.logger.info(f"  STOP-LOSS: BUY STP LMT trigger=${sl_trigger_price:.2f}, limit=${sl_limit_price:.2f}")
            else:
                # DEBIT SPREAD: paid premium at entry (fill_price > 0)
                # Exit: BUY reversed combo to close position
                # Target: want to receive MORE credit = higher positive value in IBKR = profit
                # SL: receive LESS credit = lower positive value in IBKR = loss
                # debit_target_pct = 1.50 means exit at 150% of entry = 50% profit
                # debit_sl_pct = 0.50 means exit at 50% of entry = 50% loss
                # NOTE: Using POSITIVE prices to avoid IBKR "Riskless combination orders" rejection
                target_price = self.round_to_spx_tick(fill_value * debit_target_pct)  # Positive price for IBKR
                sl_trigger_price = self.round_to_spx_tick(fill_value * debit_sl_pct)  # Positive price for IBKR
                # Use configurable sl_limit_offset for stop-limit orders
                sl_limit_price = self.round_to_spx_tick(sl_trigger_price + sl_limit_offset)  # Above trigger for BUY STP
                self.logger.info(f"  Type: DEBIT SPREAD")
                self.logger.info(f"  Entry debit: ${fill_value:.2f}")
                self.logger.info(f"  TARGET: BUY LMT @ ${target_price:.2f} ({debit_target_pct*100:.0f}% of entry)")
                self.logger.info(f"  STOP-LOSS: BUY STP LMT trigger=${sl_trigger_price:.2f}, limit=${sl_limit_price:.2f}")

            # ===== STEP 6: BUILD REVERSED EXIT COMBO =====
            # Exit combo has REVERSED actions to close the position
            exit_combo = Contract()
            exit_combo.symbol = 'SPX'
            exit_combo.secType = 'BAG'
            exit_combo.currency = 'USD'
            exit_combo.exchange = 'SMART'

            exit_legs = []
            for leg in legs:
                cl = ComboLeg()
                cl.conId = leg['contract'].conId
                cl.ratio = leg.get('quantity', 1)
                # REVERSE the action: SELL -> BUY, BUY -> SELL
                cl.action = 'BUY' if leg['action'] == 'SELL' else 'SELL'
                cl.exchange = 'SMART'
                exit_legs.append(cl)

            exit_combo.comboLegs = exit_legs

            # ===== STEP 7: CREATE OCA GROUP =====
            # Use microsecond precision to ensure uniqueness if multiple orders placed quickly
            oca_group = f"EXIT_{datetime.now().strftime('%H%M%S%f')}"
            result['oca_group'] = oca_group
            self.logger.info(f"  OCA Group: {oca_group}")

            # ===== STEP 8: CREATE TARGET ORDER (LMT) =====
            target_order = Order()
            target_order.action = 'BUY'  # Always BUY - IBKR blocks SELL on combos
            target_order.totalQuantity = quantity
            target_order.orderType = 'LMT'
            target_order.lmtPrice = target_price
            target_order.tif = 'GTC'
            target_order.ocaGroup = oca_group
            target_order.ocaType = 1  # Cancel other orders on fill
            target_order.overridePercentageConstraints = True
            target_order.transmit = True

            # ===== STEP 9: CREATE STOP LOSS ORDER (STP LMT) =====
            sl_order = Order()
            sl_order.action = 'BUY'  # Always BUY - IBKR blocks SELL on combos
            sl_order.totalQuantity = quantity
            sl_order.orderType = 'STP LMT'  # Stop-Limit order
            sl_order.auxPrice = sl_trigger_price  # Stop trigger price
            sl_order.lmtPrice = sl_limit_price  # Limit price after trigger
            sl_order.tif = 'GTC'
            sl_order.ocaGroup = oca_group
            sl_order.ocaType = 1  # Cancel other orders on fill
            sl_order.overridePercentageConstraints = True
            sl_order.transmit = True

            # ===== STEP 10: PLACE EXIT ORDERS =====
            self.logger.info("  Placing orders...")

            # Place target order
            target_trade = self.ib.placeOrder(exit_combo, target_order)
            self.ib.sleep(1)
            result['target_order_id'] = target_trade.order.orderId
            self.logger.info(f"  Target order ID: {target_trade.order.orderId}")

            # Place stop loss order
            sl_trade = self.ib.placeOrder(exit_combo, sl_order)
            self.ib.sleep(1)
            result['sl_order_id'] = sl_trade.order.orderId
            self.logger.info(f"  SL order ID: {sl_trade.order.orderId}")

            # ===== STEP 11: VERIFY EXIT ORDERS =====
            self.ib.sleep(2)

            target_status = target_trade.orderStatus.status
            sl_status = sl_trade.orderStatus.status

            self.logger.info(f"  Target status: {target_status}")
            self.logger.info(f"  SL status: {sl_status}")
            self.logger.info("=" * 60)

            # Check for errors in order logs
            for name, trade in [('Target', target_trade), ('SL', sl_trade)]:
                for log in trade.log:
                    if log.message:
                        if 'Error' in log.message or 'Warning' in log.message:
                            self.logger.warning(f"{name}: {log.message}")

            # Success if both orders are working
            # PendingSubmit is a valid intermediate state (order sent, awaiting exchange acknowledgement)
            valid_statuses = ['PendingSubmit', 'PreSubmitted', 'Submitted']
            target_ok = target_status in valid_statuses
            sl_ok = sl_status in valid_statuses

            if target_ok and sl_ok:
                result['success'] = True
                result['is_credit_spread'] = is_credit_spread
                # Store target/stop prices for engine to log
                result['target_price'] = target_price
                result['sl_trigger_price'] = sl_trigger_price

                # ===== VALIDATION: Verify order prices match expected =====
                self.logger.info("")
                self.logger.info("[VALIDATION] Verifying bracket order prices...")
                placed_target_price = target_trade.order.lmtPrice
                placed_sl_trigger = sl_trade.order.auxPrice
                placed_sl_limit = sl_trade.order.lmtPrice

                validation_passed = True
                if abs(placed_target_price - target_price) > 0.01:
                    self.logger.warning(f"  ⚠️ TARGET PRICE MISMATCH: placed={placed_target_price:.2f}, expected={target_price:.2f}")
                    validation_passed = False
                else:
                    self.logger.info(f"  ✓ Target price verified: ${placed_target_price:.2f}")

                if abs(placed_sl_trigger - sl_trigger_price) > 0.01:
                    self.logger.warning(f"  ⚠️ SL TRIGGER MISMATCH: placed={placed_sl_trigger:.2f}, expected={sl_trigger_price:.2f}")
                    validation_passed = False
                else:
                    self.logger.info(f"  ✓ SL trigger price verified: ${placed_sl_trigger:.2f}")

                self.logger.info(f"  ✓ SL limit price: ${placed_sl_limit:.2f}")

                if validation_passed:
                    self.logger.info("[VALIDATION] ✓ All bracket order prices verified")
                else:
                    self.logger.warning("[VALIDATION] ⚠️ Price mismatch detected - orders may need review")
                self.logger.info("")
            else:
                # CRITICAL: One order placed, other failed - cancel the successful one
                # Position would be unprotected otherwise
                if target_ok and not sl_ok:
                    self.logger.error("=" * 60)
                    self.logger.error("SL ORDER FAILED - TARGET PLACED")
                    self.logger.error("=" * 60)
                    self.logger.error(f"  Target status: {target_status} (OK)")
                    self.logger.error(f"  SL status: {sl_status} (FAILED)")
                    self.logger.error("  Cancelling target order to retry both...")
                    try:
                        self.ib.cancelOrder(target_order)
                        self.ib.sleep(1)
                        self.logger.info("  Target order cancelled")
                    except Exception as cancel_err:
                        self.logger.error(f"  Failed to cancel target: {cancel_err}")
                    result['error_message'] = f"SL order failed ({sl_status}), target cancelled - retry needed"

                elif sl_ok and not target_ok:
                    self.logger.error("=" * 60)
                    self.logger.error("TARGET ORDER FAILED - SL PLACED")
                    self.logger.error("=" * 60)
                    self.logger.error(f"  Target status: {target_status} (FAILED)")
                    self.logger.error(f"  SL status: {sl_status} (OK)")
                    self.logger.error("  Cancelling SL order to retry both...")
                    try:
                        self.ib.cancelOrder(sl_order)
                        self.ib.sleep(1)
                        self.logger.info("  SL order cancelled")
                    except Exception as cancel_err:
                        self.logger.error(f"  Failed to cancel SL: {cancel_err}")
                    result['error_message'] = f"Target order failed ({target_status}), SL cancelled - retry needed"

                else:
                    # Both failed
                    result['error_message'] = f"Both exit orders failed: target={target_status}, sl={sl_status}"
                    self.logger.error(result['error_message'])

            return result

        except Exception as e:
            result['error_message'] = str(e)
            self.logger.error(f"Error placing spread with bracket: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return result

    def check_bracket_order_status(
        self,
        target_order_id: int,
        sl_order_id: int,
        oca_group: str
    ) -> Dict[str, Any]:
        """
        Check status of bracket exit orders.

        Args:
            target_order_id: Target (profit taker) order ID
            sl_order_id: Stop loss order ID
            oca_group: OCA group name

        Returns:
            Dict with 'position_closed', 'exit_type' ('target'/'sl'/None),
            'fill_price', 'target_status', 'sl_status',
            'partial_fill' (bool), 'filled_quantity', 'remaining_quantity', 'total_quantity'
        """
        result = {
            'position_closed': False,
            'exit_type': None,
            'fill_price': None,
            'target_status': None,
            'sl_status': None,
            'partial_fill': False,
            'filled_quantity': 0,
            'remaining_quantity': 0,
            'total_quantity': 0
        }

        try:
            self.ib.sleep(0.5)  # Let IB process updates

            # Get all open orders
            open_orders = self.ib.openOrders()

            # Check for our orders
            target_found = False
            sl_found = False

            for order in open_orders:
                if order.orderId == target_order_id:
                    target_found = True
                elif order.orderId == sl_order_id:
                    sl_found = True

            # Get trade objects for detailed status
            for trade in self.ib.trades():
                if trade.order.orderId == target_order_id:
                    result['target_status'] = trade.orderStatus.status
                    order_status = trade.orderStatus

                    # Track quantities for partial fill detection
                    filled = order_status.filled or 0
                    remaining = order_status.remaining or 0
                    total = filled + remaining

                    if order_status.status == 'Filled':
                        result['position_closed'] = True
                        result['exit_type'] = 'target'
                        result['fill_price'] = order_status.avgFillPrice
                        result['filled_quantity'] = filled
                        result['total_quantity'] = total
                    elif order_status.status == 'PartiallyFilled' or (filled > 0 and remaining > 0):
                        # Partial fill detected
                        result['partial_fill'] = True
                        result['exit_type'] = 'target'
                        result['fill_price'] = order_status.avgFillPrice
                        result['filled_quantity'] = filled
                        result['remaining_quantity'] = remaining
                        result['total_quantity'] = total

                elif trade.order.orderId == sl_order_id:
                    result['sl_status'] = trade.orderStatus.status
                    order_status = trade.orderStatus

                    # Track quantities for partial fill detection
                    filled = order_status.filled or 0
                    remaining = order_status.remaining or 0
                    total = filled + remaining

                    if order_status.status == 'Filled':
                        result['position_closed'] = True
                        result['exit_type'] = 'sl'
                        result['fill_price'] = order_status.avgFillPrice
                        result['filled_quantity'] = filled
                        result['total_quantity'] = total
                    elif order_status.status == 'PartiallyFilled' or (filled > 0 and remaining > 0):
                        # Partial fill detected
                        result['partial_fill'] = True
                        result['exit_type'] = 'sl'
                        result['fill_price'] = order_status.avgFillPrice
                        result['filled_quantity'] = filled
                        result['remaining_quantity'] = remaining
                        result['total_quantity'] = total

            return result

        except Exception as e:
            self.logger.error(f"Error checking bracket order status: {e}")
            return result

    def cancel_bracket_orders(
        self,
        target_order_id: int,
        sl_order_id: int
    ) -> bool:
        """
        Cancel bracket exit orders.

        Args:
            target_order_id: Target order ID to cancel
            sl_order_id: Stop loss order ID to cancel

        Returns:
            True if cancellation was successful
        """
        try:
            cancelled = 0
            already_done = 0
            not_found = 0
            target_ids = [target_order_id, sl_order_id]
            found_ids = set()

            for trade in self.ib.trades():
                if trade.order.orderId in target_ids:
                    found_ids.add(trade.order.orderId)
                    current_status = trade.orderStatus.status

                    # Check if cancellable
                    if current_status in ['Submitted', 'PreSubmitted', 'PendingSubmit']:
                        self.ib.cancelOrder(trade.order)
                        cancelled += 1
                        self.logger.debug(f"Cancelled bracket order ID {trade.order.orderId} (status: {current_status})")
                    elif current_status in ['Filled', 'Cancelled', 'ApiCancelled']:
                        already_done += 1
                        self.logger.debug(f"Bracket order ID {trade.order.orderId} already {current_status}")

            # Check for orders not found
            for order_id in target_ids:
                if order_id and order_id not in found_ids:
                    not_found += 1

            # Log consolidated summary
            if cancelled > 0 or already_done > 0:
                self.logger.info(f"[BRACKET CANCEL] Cancelled: {cancelled}, Already done: {already_done}")

            self.ib.sleep(1)
            return cancelled > 0

        except Exception as e:
            self.logger.error(f"Error cancelling bracket orders: {e}")
            return False

    def verify_bracket_orders_exist(
        self,
        target_order_id: Optional[int],
        sl_order_id: Optional[int]
    ) -> Dict[str, Any]:
        """
        Verify if saved bracket order IDs still exist on IBKR.

        Args:
            target_order_id: Target order ID to check
            sl_order_id: Stop loss order ID to check

        Returns:
            Dict with:
              - 'target_exists': bool
              - 'sl_exists': bool
              - 'target_status': str or None
              - 'sl_status': str or None
              - 'both_exist': bool
              - 'any_filled': bool
              - 'filled_order': 'target'/'sl'/None
        """
        result = {
            'target_exists': False,
            'sl_exists': False,
            'target_status': None,
            'sl_status': None,
            'both_exist': False,
            'any_filled': False,
            'filled_order': None
        }

        try:
            self.ib.sleep(0.5)  # Let IB process updates

            # Check all trades for our order IDs
            for trade in self.ib.trades():
                order_id = trade.order.orderId
                status = trade.orderStatus.status

                if order_id == target_order_id:
                    result['target_status'] = status
                    if status == 'Filled':
                        result['any_filled'] = True
                        result['filled_order'] = 'target'
                    elif status not in ['Cancelled', 'ApiCancelled', 'Inactive']:
                        result['target_exists'] = True

                elif order_id == sl_order_id:
                    result['sl_status'] = status
                    if status == 'Filled':
                        result['any_filled'] = True
                        result['filled_order'] = 'sl'
                    elif status not in ['Cancelled', 'ApiCancelled', 'Inactive']:
                        result['sl_exists'] = True

            result['both_exist'] = result['target_exists'] and result['sl_exists']

            self.logger.info(f"Bracket order verification: target={result['target_status']}, sl={result['sl_status']}")
            return result

        except Exception as e:
            self.logger.error(f"Error verifying bracket orders: {e}")
            return result

    def scan_for_existing_exit_orders(
        self,
        position_leg_conids: List[int]
    ) -> Dict[str, Any]:
        """
        Scan IBKR for existing exit orders that match the position legs.

        This is used during crash recovery when bracket_order_info is None.
        We check if there are existing combo orders that could be exit orders
        for our position (to avoid placing duplicate exit orders).

        Args:
            position_leg_conids: List of contract IDs from the position legs

        Returns:
            Dict with:
              - 'found': bool - whether matching exit orders were found
              - 'target_order_id': int or None
              - 'sl_order_id': int or None
              - 'oca_group': str or None
              - 'orders_found': list of order details
        """
        result = {
            'found': False,
            'target_order_id': None,
            'sl_order_id': None,
            'oca_group': None,
            'orders_found': []
        }

        try:
            self.ib.sleep(0.5)
            position_conids_set = set(position_leg_conids)

            self.logger.info(f"[SCAN] Looking for existing exit orders with conIds: {position_leg_conids}")

            # Get all open orders
            open_orders = self.ib.openOrders()
            matching_orders = []

            for order in open_orders:
                # Skip if not a combo order
                if not hasattr(order, 'contract') or order.contract.secType != 'BAG':
                    continue

                # Skip if not SPX
                if order.contract.symbol != 'SPX':
                    continue

                # Check if combo legs match our position legs
                if not order.contract.comboLegs:
                    continue

                order_conids = set(leg.conId for leg in order.contract.comboLegs)

                # If the conIds match, this could be an exit order
                if order_conids == position_conids_set:
                    # Get trade info for status
                    trade_info = None
                    for trade in self.ib.trades():
                        if trade.order.orderId == order.orderId:
                            trade_info = trade
                            break

                    status = trade_info.orderStatus.status if trade_info else 'Unknown'

                    # Only consider active orders
                    if status in ['PendingSubmit', 'PreSubmitted', 'Submitted']:
                        order_detail = {
                            'order_id': order.orderId,
                            'order_type': order.orderType,
                            'action': order.action,
                            'quantity': order.totalQuantity,
                            'status': status,
                            'oca_group': order.ocaGroup,
                            'lmt_price': getattr(order, 'lmtPrice', None),
                            'aux_price': getattr(order, 'auxPrice', None),  # Stop price
                        }
                        matching_orders.append(order_detail)
                        self.logger.info(f"[SCAN] Found matching order: ID={order.orderId}, type={order.orderType}, status={status}")

            if matching_orders:
                result['found'] = True
                result['orders_found'] = matching_orders

                # Try to identify target (LMT) and SL (STP LMT) orders
                for order_info in matching_orders:
                    if order_info['order_type'] == 'LMT' and result['target_order_id'] is None:
                        result['target_order_id'] = order_info['order_id']
                        result['oca_group'] = order_info['oca_group']
                    elif order_info['order_type'] == 'STP LMT' and result['sl_order_id'] is None:
                        result['sl_order_id'] = order_info['order_id']
                        if result['oca_group'] is None:
                            result['oca_group'] = order_info['oca_group']

                self.logger.info(f"[SCAN] Found {len(matching_orders)} existing exit orders")
                self.logger.info(f"[SCAN] Target ID: {result['target_order_id']}, SL ID: {result['sl_order_id']}")
            else:
                self.logger.info("[SCAN] No existing exit orders found")

            return result

        except Exception as e:
            self.logger.error(f"Error scanning for existing exit orders: {e}")
            return result

    def place_exit_orders_only(
        self,
        legs: List[Dict[str, Any]],
        quantity: int,
        entry_fill_price: float,
        credit_target_pct: float = 0.70,
        credit_sl_pct: float = 0.30,
        debit_target_pct: float = 1.50,
        debit_sl_pct: float = 0.50,
        slippage: float = 0.05,
        sl_limit_offset: float = 0.50  # $ offset from SL trigger to limit price
    ) -> Dict[str, Any]:
        """
        Place exit orders only (for recovered positions without exit orders).

        This is used during crash recovery when the saved exit orders no longer exist
        on IBKR. It re-places the Target and Stop Loss orders for an existing position.

        Args:
            legs: List of leg dictionaries with 'contract', 'action', 'quantity'
                  (action should be original ENTRY actions - will be reversed)
            quantity: Number of spreads
            entry_fill_price: The original entry fill price (used to calculate target/SL)
            credit_target_pct: For credit spreads - target % (0.50 = 50% profit)
            credit_sl_pct: For credit spreads - SL % (1.50 = 150% loss)
            debit_target_pct: For debit spreads - target % (1.50 = 50% profit)
            debit_sl_pct: For debit spreads - SL % (0.50 = 50% loss)
            slippage: Slippage for entry orders
            sl_limit_offset: $ offset from SL trigger to limit price

        Returns:
            Dict with 'success', 'target_order_id', 'sl_order_id', 'oca_group',
            'target_price', 'sl_trigger_price', 'error_message'
        """
        from datetime import datetime

        result = {
            'success': False,
            'target_order_id': None,
            'sl_order_id': None,
            'oca_group': None,
            'target_price': None,
            'sl_trigger_price': None,
            'error_message': None
        }

        try:
            self.logger.info(f"[RECOVERY] Placing exit orders for existing position")
            self.logger.info(f"  Entry fill price: {entry_fill_price:.2f}")
            self.logger.info(f"  Quantity: {quantity}")

            # Detect if credit or debit spread based on entry fill price
            is_credit_spread = entry_fill_price < 0
            fill_value = abs(entry_fill_price)

            # Calculate exit prices
            if is_credit_spread:
                target_price = self.round_to_spx_tick(fill_value * credit_target_pct)
                sl_trigger_price = self.round_to_spx_tick(fill_value * (1 + credit_sl_pct))
                # Use configurable sl_limit_offset for stop-limit orders
                sl_limit_price = self.round_to_spx_tick(sl_trigger_price + sl_limit_offset)
                self.logger.info(f"[CREDIT SPREAD] Exit prices:")
                self.logger.info(f"  Target: ${target_price:.2f}")
                self.logger.info(f"  SL trigger: ${sl_trigger_price:.2f}, limit: ${sl_limit_price:.2f} (offset=${sl_limit_offset:.2f})")
            else:
                # DEBIT SPREAD: Use POSITIVE prices to avoid IBKR rejection
                target_price = self.round_to_spx_tick(fill_value * debit_target_pct)
                sl_trigger_price = self.round_to_spx_tick(fill_value * debit_sl_pct)
                # Use configurable sl_limit_offset for stop-limit orders
                sl_limit_price = self.round_to_spx_tick(sl_trigger_price + sl_limit_offset)
                self.logger.info(f"[DEBIT SPREAD] Exit prices:")
                self.logger.info(f"  Target: ${target_price:.2f}")
                self.logger.info(f"  SL trigger: ${sl_trigger_price:.2f}, limit: ${sl_limit_price:.2f} (offset=${sl_limit_offset:.2f})")

            result['target_price'] = target_price
            result['sl_trigger_price'] = sl_trigger_price

            # Build REVERSED exit combo
            exit_combo = Contract()
            exit_combo.symbol = 'SPX'
            exit_combo.secType = 'BAG'
            exit_combo.currency = 'USD'
            exit_combo.exchange = 'SMART'

            exit_legs = []
            for leg in legs:
                cl = ComboLeg()
                cl.conId = leg['contract'].conId
                cl.ratio = leg.get('quantity', 1)
                # REVERSE the action: SELL -> BUY, BUY -> SELL
                cl.action = 'BUY' if leg['action'] == 'SELL' else 'SELL'
                cl.exchange = 'SMART'
                exit_legs.append(cl)
                self.logger.info(f"  Exit leg: conId={cl.conId}, action={cl.action} (reversed)")

            exit_combo.comboLegs = exit_legs

            # Create OCA group
            oca_group = f"EXIT_RECOV_{datetime.now().strftime('%H%M%S')}"
            result['oca_group'] = oca_group
            self.logger.info(f"OCA Group: {oca_group}")

            # Create TARGET order (LMT)
            target_order = Order()
            target_order.action = 'BUY'
            target_order.totalQuantity = quantity
            target_order.orderType = 'LMT'
            target_order.lmtPrice = target_price
            target_order.tif = 'GTC'
            target_order.ocaGroup = oca_group
            target_order.ocaType = 1
            target_order.overridePercentageConstraints = True
            target_order.transmit = True

            # Create STOP LOSS order (STP LMT)
            sl_order = Order()
            sl_order.action = 'BUY'
            sl_order.totalQuantity = quantity
            sl_order.orderType = 'STP LMT'
            sl_order.auxPrice = sl_trigger_price
            sl_order.lmtPrice = sl_limit_price
            sl_order.tif = 'GTC'
            sl_order.ocaGroup = oca_group
            sl_order.ocaType = 1
            sl_order.overridePercentageConstraints = True
            sl_order.transmit = True

            # Place exit orders
            self.logger.info("Placing exit orders...")

            target_trade = self.ib.placeOrder(exit_combo, target_order)
            self.ib.sleep(1)
            result['target_order_id'] = target_trade.order.orderId
            self.logger.info(f"Target order placed: ID={target_trade.order.orderId}")

            sl_trade = self.ib.placeOrder(exit_combo, sl_order)
            self.ib.sleep(1)
            result['sl_order_id'] = sl_trade.order.orderId
            self.logger.info(f"SL order placed: ID={sl_trade.order.orderId}")

            # Verify exit orders
            self.ib.sleep(2)

            target_status = target_trade.orderStatus.status
            sl_status = sl_trade.orderStatus.status

            self.logger.info(f"Target status: {target_status}")
            self.logger.info(f"SL status: {sl_status}")

            # Check for errors
            for name, trade in [('Target', target_trade), ('SL', sl_trade)]:
                for log in trade.log:
                    if log.message and ('Error' in log.message or 'Warning' in log.message):
                        self.logger.warning(f"{name}: {log.message}")

            # Success if both orders are working
            valid_statuses = ['PendingSubmit', 'PreSubmitted', 'Submitted']
            if target_status in valid_statuses and sl_status in valid_statuses:
                # Validate placed order prices match expected prices
                placed_target_price = target_trade.order.lmtPrice
                placed_sl_trigger = sl_trade.order.auxPrice

                if abs(placed_target_price - target_price) > 0.01:
                    self.logger.warning(f"[PRICE MISMATCH] Target order price {placed_target_price:.2f} != expected {target_price:.2f}")
                if abs(placed_sl_trigger - sl_trigger_price) > 0.01:
                    self.logger.warning(f"[PRICE MISMATCH] SL trigger price {placed_sl_trigger:.2f} != expected {sl_trigger_price:.2f}")

                result['success'] = True
                self.logger.info("[RECOVERY] Exit orders placed successfully!")
                self.logger.info(f"  Target @ ${placed_target_price:.2f}, SL trigger @ ${placed_sl_trigger:.2f}")
            else:
                result['error_message'] = f"Exit orders not working: target={target_status}, sl={sl_status}"
                self.logger.error(result['error_message'])

            return result

        except Exception as e:
            result['error_message'] = str(e)
            self.logger.error(f"Error placing exit orders: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return result

    def wait_for_spread_fill(
        self,
        combo_id: str,
        timeout: float = 60.0
    ) -> Optional[ComboOrder]:
        """
        Wait for a spread order to be filled.

        Args:
            combo_id: Combo order ID from place_spread_order
            timeout: Maximum time to wait in seconds

        Returns:
            ComboOrder if filled, None if timeout or not found
        """
        try:
            if combo_id not in self.combo_orders:
                self.logger.error(f"Combo order not found: {combo_id}")
                return None

            combo_order = self.combo_orders[combo_id]
            trade = getattr(combo_order, '_trade', None)

            if trade is None:
                self.logger.error(f"Trade reference not found for combo: {combo_id}")
                return None

            self.logger.info(f"Waiting for spread fill: {combo_id}, timeout={timeout}s")

            elapsed = 0.0
            poll_interval = 0.5

            while elapsed < timeout:
                # Process IB messages
                self.ib.sleep(poll_interval)
                elapsed += poll_interval

                # Check trade status
                status = trade.orderStatus.status

                self.logger.debug(f"Order status: {status}, elapsed: {elapsed:.1f}s")

                if status == 'Filled':
                    combo_order.status = OrderStatus.FILLED
                    combo_order.fill_time = datetime.now(US_EASTERN)

                    # Update leg statuses
                    for leg_order in combo_order.leg_orders:
                        leg_order.status = OrderStatus.FILLED
                        leg_order.filled_quantity = leg_order.quantity
                        leg_order.remaining_quantity = 0
                        leg_order.fill_time = datetime.now(US_EASTERN)

                    # Get fill price if available
                    if trade.orderStatus.avgFillPrice:
                        for leg_order in combo_order.leg_orders:
                            leg_order.avg_fill_price = trade.orderStatus.avgFillPrice

                    self.logger.info(
                        f"Spread order FILLED: {combo_id}, "
                        f"avg_price={trade.orderStatus.avgFillPrice}"
                    )
                    return combo_order

                elif status in ('Cancelled', 'ApiCancelled'):
                    combo_order.status = OrderStatus.CANCELLED
                    for leg_order in combo_order.leg_orders:
                        leg_order.status = OrderStatus.CANCELLED

                    self.logger.warning(f"Spread order CANCELLED: {combo_id}")
                    return combo_order

                elif status == 'Inactive':
                    self.logger.warning(f"Spread order INACTIVE: {combo_id}")
                    # Continue waiting, might become active

            # Timeout reached - ISSUE 5, 6: Cancel the unfilled order to prevent stale fills
            self.logger.warning(f"Timeout waiting for spread fill: {combo_id}")

            # Get config setting for auto-cancel on timeout
            should_cancel = True
            if self.config:
                should_cancel = getattr(self.config, 'cancel_unfilled_on_timeout', True)

            if should_cancel and trade:
                try:
                    # Check if order is still cancellable (avoid Error [161])
                    current_status = trade.orderStatus.status

                    if current_status in ['Submitted', 'PreSubmitted', 'PendingSubmit']:
                        # Order appears cancellable - attempt cancel
                        self.logger.info(f"Cancelling timed-out order: {combo_id} (status: {current_status})")
                        self.ib.cancelOrder(trade.order)
                        self.ib.sleep(1)  # Give time for cancellation to process

                        # RACE CONDITION FIX: Re-check status AFTER cancel attempt
                        # Order may have filled between our check and cancel request
                        final_status = trade.orderStatus.status

                        if final_status == 'Filled':
                            # Order filled during/after cancel - treat as success (not cancelled)
                            self.logger.info(f"Order {combo_id} FILLED during cancel attempt - treating as success")
                            combo_order.status = OrderStatus.FILLED
                            combo_order.fill_time = datetime.now(US_EASTERN)
                            for leg_order in combo_order.leg_orders:
                                leg_order.status = OrderStatus.FILLED
                                leg_order.filled_quantity = leg_order.quantity
                                leg_order.remaining_quantity = 0
                                leg_order.fill_time = datetime.now(US_EASTERN)
                            if trade.orderStatus.avgFillPrice:
                                for leg_order in combo_order.leg_orders:
                                    leg_order.avg_fill_price = trade.orderStatus.avgFillPrice
                            return combo_order
                        elif final_status in ('Cancelled', 'ApiCancelled'):
                            # Cancel confirmed
                            combo_order.status = OrderStatus.CANCELLED
                            for leg_order in combo_order.leg_orders:
                                leg_order.status = OrderStatus.CANCELLED
                            self.logger.info(f"Timed-out order cancelled successfully: {combo_id}")
                        else:
                            # Unexpected state - log and mark as cancelled for safety
                            self.logger.warning(f"Order {combo_id} in unexpected state after cancel: {final_status}")
                            combo_order.status = OrderStatus.CANCELLED
                            for leg_order in combo_order.leg_orders:
                                leg_order.status = OrderStatus.CANCELLED

                    elif current_status == 'Filled':
                        # Order filled during timeout wait - treat as success
                        self.logger.info(f"Order {combo_id} filled during timeout wait (status: {current_status})")
                        combo_order.status = OrderStatus.FILLED
                        combo_order.fill_time = datetime.now(US_EASTERN)
                        for leg_order in combo_order.leg_orders:
                            leg_order.status = OrderStatus.FILLED
                            leg_order.filled_quantity = leg_order.quantity
                            leg_order.remaining_quantity = 0
                            leg_order.fill_time = datetime.now(US_EASTERN)
                        if trade.orderStatus.avgFillPrice:
                            for leg_order in combo_order.leg_orders:
                                leg_order.avg_fill_price = trade.orderStatus.avgFillPrice
                        return combo_order
                    else:
                        # Order in other state (Cancelled, Inactive, etc.)
                        self.logger.info(f"Order {combo_id} not cancellable - status: {current_status}")

                except Exception as cancel_err:
                    # RACE CONDITION FIX: Even on exception, check if order actually filled
                    try:
                        final_status = trade.orderStatus.status
                        if final_status == 'Filled':
                            self.logger.info(f"Order {combo_id} FILLED despite cancel error - treating as success")
                            combo_order.status = OrderStatus.FILLED
                            for leg_order in combo_order.leg_orders:
                                leg_order.status = OrderStatus.FILLED
                            return combo_order
                    except:
                        pass
                    self.logger.error(f"Failed to cancel timed-out order {combo_id}: {cancel_err}")

            return None

        except Exception as e:
            self.logger.error(f"Error waiting for spread fill: {e}")

            # CRITICAL FIX: Cancel order on exception to prevent orphan positions
            # If we don't cancel, order may fill later while engine thinks it failed
            try:
                if combo_id in self.combo_orders:
                    combo_order = self.combo_orders[combo_id]
                    trade = getattr(combo_order, '_trade', None)
                    if trade:
                        current_status = trade.orderStatus.status
                        if current_status in ['Submitted', 'PreSubmitted', 'PendingSubmit']:
                            self.logger.warning(f"Cancelling order {combo_id} after exception to prevent orphan position")
                            self.ib.cancelOrder(trade.order)
                            self.ib.sleep(1)
                            # Re-check if it filled during cancel
                            final_status = trade.orderStatus.status
                            if final_status == 'Filled':
                                self.logger.info(f"Order {combo_id} FILLED during exception cleanup - returning success")
                                combo_order.status = OrderStatus.FILLED
                                for leg_order in combo_order.leg_orders:
                                    leg_order.status = OrderStatus.FILLED
                                return combo_order
                            else:
                                combo_order.status = OrderStatus.CANCELLED
                                self.logger.info(f"Order {combo_id} cancelled after exception")
                        elif current_status == 'Filled':
                            self.logger.info(f"Order {combo_id} already filled despite exception - returning success")
                            combo_order.status = OrderStatus.FILLED
                            for leg_order in combo_order.leg_orders:
                                leg_order.status = OrderStatus.FILLED
                            return combo_order
            except Exception as cleanup_err:
                self.logger.error(f"Failed to cleanup order {combo_id} after exception: {cleanup_err}")

            return None

    def get_atm_iv(
        self,
        symbol: str,
        underlying_price: float,
        expiry_rule: ExpiryRule = ExpiryRule.SAME_DAY
    ) -> Optional[float]:
        """
        Get ATM implied volatility from live option chain.

        Fetches the option chain for the specified expiry rule, finds ATM call and put,
        requests market data to get their implied volatility, and returns the average.

        Args:
            symbol: Underlying symbol (e.g., 'SPX')
            underlying_price: Current underlying price
            expiry_rule: Expiry rule (default: SAME_DAY for 0DTE)

        Returns:
            ATM IV as decimal (e.g., 0.15 for 15%), or None if unavailable
        """
        try:
            self.logger.debug(f"Getting ATM IV for {symbol}, underlying price: {underlying_price:.2f}")

            # Get option chain using expiry rule
            chain = self.get_option_chain_by_rule(symbol, expiry_rule, underlying_price)

            if chain is None:
                self.logger.warning("Could not get option chain for ATM IV")
                return None

            # Find ATM strike (rounded to nearest step, default 5 for SPX)
            strike_step = self.config.strike_step if self.config else 5
            atm_strike = round(underlying_price / strike_step) * strike_step

            self.logger.debug(f"ATM strike: {atm_strike}")

            # Get ATM call and put
            atm_call = chain.calls.get(atm_strike)
            atm_put = chain.puts.get(atm_strike)

            if not atm_call and not atm_put:
                self.logger.warning(f"No ATM options found at strike {atm_strike}")
                return None

            # Request quotes to get IV
            contracts_to_quote = [c for c in [atm_call, atm_put] if c]
            self.get_option_quotes(contracts_to_quote, timeout=5)

            # Collect IV values
            ivs = []
            if atm_call and atm_call.implied_vol:
                ivs.append(atm_call.implied_vol)
                self.logger.debug(f"ATM Call IV: {atm_call.implied_vol * 100:.2f}%")
            if atm_put and atm_put.implied_vol:
                ivs.append(atm_put.implied_vol)
                self.logger.debug(f"ATM Put IV: {atm_put.implied_vol * 100:.2f}%")

            if ivs:
                avg_iv = sum(ivs) / len(ivs)
                self.logger.debug(f"ATM IV (live): {avg_iv * 100:.2f}%")
                return avg_iv
            else:
                self.logger.warning("No IV data available for ATM options")
                return None

        except Exception as e:
            self.logger.error(f"Error getting ATM IV: {e}")
            return None

    def cancel_order(self, order_id: int) -> bool:
        """
        Cancel an order by order ID.

        Args:
            order_id: Order ID to cancel

        Returns:
            True if cancel request sent successfully, False otherwise
        """
        try:
            # Find the trade by order ID
            for trade in self.ib.trades():
                if trade.order.orderId == order_id:
                    current_status = trade.orderStatus.status

                    # Check if order is cancellable
                    if current_status in ['Submitted', 'PreSubmitted', 'PendingSubmit']:
                        self.logger.info(f"Cancelling order: {order_id} (status: {current_status})")
                        self.ib.cancelOrder(trade.order)
                        self.ib.sleep(0.5)

                        # Update stored order status
                        if order_id in self.orders:
                            self.orders[order_id].status = OrderStatus.CANCELLED

                        self.logger.info(f"Cancel request sent for order: {order_id}")
                        return True
                    elif current_status == 'Filled':
                        self.logger.info(f"Order {order_id} already filled - cancel not needed")
                        return True  # Consider it success since it's done
                    elif current_status == 'Cancelled':
                        self.logger.info(f"Order {order_id} already cancelled")
                        return True  # Already cancelled
                    else:
                        self.logger.info(f"Order {order_id} not cancellable (status: {current_status})")
                        return False

            # Also check combo orders
            for combo_id, combo_order in self.combo_orders.items():
                trade = getattr(combo_order, '_trade', None)
                if trade and trade.order.orderId == order_id:
                    current_status = trade.orderStatus.status

                    # Check if order is cancellable
                    if current_status in ['Submitted', 'PreSubmitted', 'PendingSubmit']:
                        self.logger.info(f"Cancelling combo order: {combo_id} (status: {current_status})")
                        self.ib.cancelOrder(trade.order)
                        self.ib.sleep(0.5)

                        combo_order.status = OrderStatus.CANCELLED
                        for leg_order in combo_order.leg_orders:
                            leg_order.status = OrderStatus.CANCELLED

                        self.logger.info(f"Cancel request sent for combo order: {combo_id}")
                        return True
                    elif current_status == 'Filled':
                        self.logger.info(f"Combo order {combo_id} already filled - cancel not needed")
                        return True
                    elif current_status == 'Cancelled':
                        self.logger.info(f"Combo order {combo_id} already cancelled")
                        return True
                    else:
                        self.logger.info(f"Combo order {combo_id} not cancellable (status: {current_status})")
                        return False

            self.logger.warning(f"Order not found: {order_id}")
            return False

        except Exception as e:
            self.logger.error(f"Error cancelling order: {e}")
            return False

    def cancel_all_open_orders(self, include_global_cancel: bool = True) -> int:
        """
        Cancel all open/pending orders including from previous sessions.

        ISSUE 5 FIX: Also uses reqGlobalCancel() to cancel draft/inactive orders
        that are only visible in TWS but not via API.

        Args:
            include_global_cancel: If True, also sends reqGlobalCancel to clear draft orders

        Returns:
            Number of orders cancelled
        """
        try:
            cancelled_count = 0

            # Check if connection is still valid
            if not self.ib or not self.ib.isConnected():
                self.logger.warning("Cannot cancel orders - not connected to IBKR")
                return 0

            # ISSUE 5 FIX Part 1: Use Global Cancel to clear ALL orders including drafts
            # This cancels orders that are in TWS but not yet transmitted to exchange
            if include_global_cancel:
                try:
                    self.logger.debug("Sending Global Cancel to clear all orders (including drafts)...")
                    self.ib.reqGlobalCancel()
                    self.ib.sleep(2)  # Wait for global cancel to process
                except RuntimeError as e:
                    # Handle "This event loop is already running" error during shutdown
                    if "event loop" in str(e).lower():
                        self.logger.warning(f"Event loop conflict during global cancel (safe to ignore during shutdown): {e}")
                    else:
                        raise

            # ISSUE 5 FIX Part 2: Request ALL open orders from TWS, not just current session
            # ib.openTrades() only shows orders from current connection
            # ib.reqAllOpenOrders() fetches ALL orders including from previous sessions
            try:
                self.ib.reqAllOpenOrders()
                self.ib.sleep(1)  # Wait for orders to be received
            except RuntimeError as e:
                if "event loop" in str(e).lower():
                    self.logger.warning(f"Event loop conflict during order request (safe to ignore during shutdown): {e}")
                    return 0
                raise

            open_trades = self.ib.openTrades()

            if not open_trades:
                self.logger.debug("No open orders to cancel")
                return 0

            # Track cancellation results
            already_filled_count = 0
            already_cancelled_count = 0
            not_cancellable_count = 0
            spx_orders_found = 0

            for trade in open_trades:
                # Only cancel SPX-related orders (don't touch other unrelated orders)
                contract = trade.contract
                is_spx = (hasattr(contract, 'symbol') and contract.symbol in ('SPX', 'SPXW'))
                # Also check combo legs for BAG orders
                if hasattr(contract, 'comboLegs') and contract.comboLegs:
                    is_spx = True  # Combo orders are typically our spread orders

                if not is_spx:
                    continue

                spx_orders_found += 1
                current_status = trade.orderStatus.status

                # Check if order is cancellable (removed 'Inactive' - not reliably cancellable)
                if current_status in ['Submitted', 'PreSubmitted', 'PendingSubmit']:
                    try:
                        self.ib.cancelOrder(trade.order)
                        cancelled_count += 1
                        self.logger.debug(f"  Cancelled order {trade.order.orderId} (status: {current_status})")
                    except RuntimeError as e:
                        if "event loop" in str(e).lower():
                            self.logger.warning(f"Event loop conflict cancelling order (shutdown in progress)")
                            break
                        raise
                elif current_status == 'Filled':
                    already_filled_count += 1
                    self.logger.debug(f"  Order {trade.order.orderId} already filled - skipping cancel")
                elif current_status == 'Cancelled':
                    already_cancelled_count += 1
                    self.logger.debug(f"  Order {trade.order.orderId} already cancelled - skipping")
                else:
                    not_cancellable_count += 1
                    self.logger.debug(f"  Order {trade.order.orderId} not cancellable (status: {current_status})")

            # Log consolidated summary instead of individual messages
            if spx_orders_found > 0:
                self.logger.info(f"[CANCEL SUMMARY] {spx_orders_found} SPX orders processed:")
                if cancelled_count > 0:
                    self.logger.info(f"  ✓ Cancelled: {cancelled_count}")
                if already_filled_count > 0:
                    self.logger.info(f"  ✓ Already filled: {already_filled_count} (Error [161] avoided)")
                if already_cancelled_count > 0:
                    self.logger.info(f"  ✓ Already cancelled: {already_cancelled_count}")
                if not_cancellable_count > 0:
                    self.logger.info(f"  ⚠️ Not cancellable: {not_cancellable_count}")

            try:
                self.ib.sleep(1)  # Wait for cancellations to process
            except RuntimeError:
                pass  # Ignore event loop errors during shutdown

            return cancelled_count

        except Exception as e:
            self.logger.error(f"Error cancelling all orders: {e}")
            return 0

    def close_spread_legged(
        self,
        legs: List[Dict[str, Any]],
        quantity: int,
        timeout: float = 60.0
    ) -> Optional[ComboOrder]:
        """
        Close a spread position leg-by-leg instead of as a combo order.

        Use this as a fallback when IBKR rejects combo close orders with
        "Riskless combination orders are not allowed" error.

        Args:
            legs: List of leg dictionaries with 'contract', 'action' keys
                  Actions should be the CLOSE actions (opposite of position)
            quantity: Number of contracts to close per leg
            timeout: Maximum time to wait for each leg to fill

        Returns:
            ComboOrder with fill information, or None if failed
        """
        import time as time_module

        try:
            self.logger.info(f"Closing spread leg-by-leg with {len(legs)} legs, quantity={quantity}")

            filled_legs = []
            total_fill_value = 0.0

            for i, leg in enumerate(legs):
                contract = leg['contract']
                action = leg['action']  # Should already be the closing action

                self.logger.info(f"Closing leg {i+1}/{len(legs)}: {contract.strike} {contract.right} - {action}")

                # Qualify the contract
                self.ib.qualifyContracts(contract)

                # Get current market data
                self.ib.reqMktData(contract, '', False, False)
                self.ib.sleep(1)
                ticker = self.ib.ticker(contract)

                # Determine limit price based on market data
                limit_price = None
                if ticker and ticker.bid and ticker.ask and ticker.bid > 0:
                    if action == 'BUY':
                        # Buying to close: use ask price (slightly aggressive)
                        limit_price = round(ticker.ask + 0.05, 2)
                    else:
                        # Selling to close: use bid price (slightly aggressive)
                        limit_price = round(ticker.bid - 0.05, 2)
                    self.logger.info(f"  Market: bid={ticker.bid}, ask={ticker.ask}, limit={limit_price}")
                else:
                    self.logger.warning(f"  No market data for leg {i+1}")

                # Create order
                order = Order()
                order.action = action
                order.totalQuantity = quantity
                order.tif = 'GTC'
                order.outsideRth = False

                if limit_price:
                    order.orderType = 'LMT'
                    order.lmtPrice = limit_price
                else:
                    # Fallback to market if no data
                    order.orderType = 'MKT'

                # Place order
                trade = self.ib.placeOrder(contract, order)
                self.logger.info(f"  Order placed: {trade.order.orderId}")

                # Wait for fill with price chasing
                start_time = time_module.time()
                filled = False
                chase_interval = 3.0
                last_chase_time = start_time

                while time_module.time() - start_time < timeout:
                    self.ib.sleep(0.5)
                    status = trade.orderStatus.status

                    if status == 'Filled':
                        fill_price = trade.orderStatus.avgFillPrice
                        self.logger.info(f"  FILLED @ ${fill_price}")
                        filled = True

                        # Calculate contribution to spread value
                        if action == 'BUY':
                            total_fill_value -= fill_price * quantity  # Buying costs money
                        else:
                            total_fill_value += fill_price * quantity  # Selling receives money

                        filled_legs.append({
                            'contract': contract,
                            'action': action,
                            'fill_price': fill_price,
                            'quantity': quantity
                        })
                        break

                    elif status in ['Cancelled', 'Inactive', 'ApiCancelled']:
                        self.logger.warning(f"  Order {status}")
                        break

                    # Price chase every interval
                    if order.orderType == 'LMT' and time_module.time() - last_chase_time > chase_interval:
                        # Update market data
                        ticker = self.ib.ticker(contract)
                        if ticker and ticker.bid and ticker.ask and ticker.bid > 0:
                            if action == 'BUY':
                                new_limit = round(ticker.ask + 0.10, 2)
                            else:
                                new_limit = round(ticker.bid - 0.10, 2)

                            if new_limit != order.lmtPrice:
                                order.lmtPrice = new_limit
                                self.ib.placeOrder(contract, order)
                                self.logger.info(f"  Chasing price: ${new_limit}")

                        last_chase_time = time_module.time()

                if not filled:
                    self.logger.warning(f"  Leg {i+1} not filled - cancelling")
                    try:
                        self.ib.cancelOrder(order)
                    except Exception as cancel_err:
                        self.logger.warning(f"  Failed to cancel limit order for leg {i+1}: {cancel_err}")

                    # Try market order as last resort
                    self.logger.info(f"  Trying MARKET order for leg {i+1}")
                    market_order = Order()
                    market_order.action = action
                    market_order.totalQuantity = quantity
                    market_order.orderType = 'MKT'
                    market_order.tif = 'GTC'

                    trade = self.ib.placeOrder(contract, market_order)

                    for _ in range(20):
                        self.ib.sleep(0.5)
                        if trade.orderStatus.status == 'Filled':
                            fill_price = trade.orderStatus.avgFillPrice
                            self.logger.info(f"  FILLED (market) @ ${fill_price}")
                            filled = True

                            if action == 'BUY':
                                total_fill_value -= fill_price * quantity
                            else:
                                total_fill_value += fill_price * quantity

                            filled_legs.append({
                                'contract': contract,
                                'action': action,
                                'fill_price': fill_price,
                                'quantity': quantity
                            })
                            break
                        elif trade.orderStatus.status in ['Cancelled', 'Inactive']:
                            break

                    if not filled:
                        self.logger.error(f"  FAILED to close leg {i+1} - manual intervention required!")

            # Create result ComboOrder
            if filled_legs:
                # Calculate net spread price
                net_spread_price = total_fill_value / quantity if quantity > 0 else 0

                combo_id = f"LEGGED_{datetime.now(US_EASTERN).strftime('%Y%m%d_%H%M%S')}"

                leg_orders = []
                for fl in filled_legs:
                    leg_order_info = OrderInfo(
                        order_id=0,
                        contract=fl['contract'],
                        order=None,
                        action=fl['action'],
                        quantity=fl['quantity'],
                        order_type='LMT',
                        status=OrderStatus.FILLED
                    )
                    leg_order_info.avg_fill_price = fl['fill_price']
                    leg_order_info.filled_quantity = fl['quantity']
                    leg_orders.append(leg_order_info)

                combo_order = ComboOrder(
                    combo_id=combo_id,
                    leg_orders=leg_orders,
                    status=OrderStatus.FILLED if len(filled_legs) == len(legs) else OrderStatus.PARTIAL
                )

                # Store net spread price in the first leg's avg_fill_price for compatibility
                # Keep the sign to preserve credit/debit info for correct P&L calculations
                if leg_orders:
                    leg_orders[0].avg_fill_price = net_spread_price

                self.logger.info(f"Leg-by-leg close complete: {len(filled_legs)}/{len(legs)} legs filled")
                self.logger.info(f"  Net spread price: ${net_spread_price:.2f}")

                return combo_order

            return None

        except Exception as e:
            self.logger.error(f"Error in leg-by-leg close: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return None