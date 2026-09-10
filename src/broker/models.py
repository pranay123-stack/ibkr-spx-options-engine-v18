"""
Broker Data Models

Data classes used across broker modules:
- ConnectionConfig: IBKR connection settings
- MarketDataType: Market data type constants
- OHLCBar: OHLC bar data with validation
- OptionContract: Option contract with market data and Greeks
- OptionChain: Option chain for a specific expiry
- OrderInfo: Order status and fill information
- ComboOrder: Multi-leg combo order
- AccountInfo: Account information
- PositionInfo: Position information

Author: client Options Trading Engine
"""

from datetime import datetime
from typing import Dict, Optional, List
from dataclasses import dataclass, field

from ibapi.contract import Contract
from ibapi.order import Order
from ibapi.common import BarData

from ..utils.timezone import US_EASTERN
from ..enums import OptionType, OrderStatus, PriceField


# ============================================================================
# Connection Configuration
# ============================================================================

@dataclass
class ConnectionConfig:
    """IBKR Connection configuration"""
    host: str = "127.0.0.1"
    port: int = 7496  # 7496=Live, 7497=Paper
    client_id: int = 1
    timeout: float = 30.0
    auto_reconnect: bool = True
    reconnect_attempts: int = 3
    reconnect_delay: float = 5.0


@dataclass
class MarketDataType:
    """Market data type constants"""
    LIVE = 1
    FROZEN = 2
    DELAYED = 3
    DELAYED_FROZEN = 4


# ============================================================================
# Market Data
# ============================================================================

@dataclass
class OHLCBar:
    """OHLC bar data structure with validation (Issue 7, 8)"""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int = 0

    def is_valid(self) -> bool:
        """Check if this bar has valid OHLC data (Issue 7)"""
        try:
            # Check all values are positive
            if self.open <= 0 or self.high <= 0 or self.low <= 0 or self.close <= 0:
                return False
            # Check OHLC relationships
            if self.high < self.low:
                return False
            if self.high < max(self.open, self.close):
                return False
            if self.low > min(self.open, self.close):
                return False
            return True
        except (TypeError, ValueError):
            return False

    @classmethod
    def from_ibkr_bar(cls, bar: BarData) -> "OHLCBar":
        """
        Convert IBKR BarData to OHLCBar with proper error handling (Issue 7, 8)

        IMPORTANT: IBKR returns bar timestamps in US Eastern Time (EST/EDT).
        This method parses the timestamp and stores it as US Eastern time.
        The timestamp represents when the bar STARTED (not ended).

        For a 5-min bar at 9:30 AM EST:
        - Bar data covers 9:30:00 - 9:34:59 EST
        - Timestamp will be 9:30:00 EST
        """
        dt = None

        try:
            # Handle different date formats from IBKR
            bar_date = str(bar.date) if bar.date else ""

            if isinstance(bar.date, datetime):
                # Already a datetime object - ensure it's treated as US Eastern
                if bar.date.tzinfo is None:
                    # Naive datetime from IBKR is in US Eastern
                    dt = bar.date.replace(tzinfo=US_EASTERN)
                else:
                    dt = bar.date
            elif len(bar_date) == 8:  # YYYYMMDD (daily bars)
                dt = datetime.strptime(bar_date, "%Y%m%d").replace(tzinfo=US_EASTERN)
            elif ' ' in bar_date:  # YYYYMMDD HH:MM:SS (intraday bars)
                # This is the timestamp in US Eastern Time from IBKR
                dt = datetime.strptime(bar_date, "%Y%m%d %H:%M:%S").replace(tzinfo=US_EASTERN)
            elif 'T' in bar_date:  # ISO format
                dt = datetime.fromisoformat(bar_date.replace('Z', '+00:00'))
            else:
                # Fallback: try multiple formats (all assumed to be US Eastern)
                for fmt in ["%Y%m%d", "%Y%m%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
                    try:
                        dt = datetime.strptime(bar_date, fmt).replace(tzinfo=US_EASTERN)
                        break
                    except ValueError:
                        continue

            if dt is None:
                # Last resort: use current time in US Eastern
                dt = datetime.now(US_EASTERN)

        except Exception:
            # Use current time in US Eastern if parsing fails entirely
            dt = datetime.now(US_EASTERN)

        # Extract OHLCV with defaults for invalid data
        try:
            open_price = float(bar.open) if bar.open is not None else 0.0
            high_price = float(bar.high) if bar.high is not None else 0.0
            low_price = float(bar.low) if bar.low is not None else 0.0
            close_price = float(bar.close) if bar.close is not None else 0.0
            volume = int(bar.volume) if hasattr(bar, 'volume') and bar.volume is not None else 0
        except (TypeError, ValueError):
            open_price = high_price = low_price = close_price = 0.0
            volume = 0

        return cls(
            timestamp=dt,
            open=open_price,
            high=high_price,
            low=low_price,
            close=close_price,
            volume=volume
        )


# ============================================================================
# Options Data
# ============================================================================

@dataclass
class OptionContract:
    """Represents an option contract with market data"""
    symbol: str
    strike: float
    expiry: str  # YYYYMMDD format
    option_type: OptionType
    contract: Contract

    # Market data
    bid: float = 0.0
    ask: float = 0.0
    last: float = 0.0
    mid: float = 0.0

    # Greeks
    implied_vol: Optional[float] = None
    delta: Optional[float] = None
    gamma: Optional[float] = None
    vega: Optional[float] = None
    theta: Optional[float] = None

    # Underlying price at quote time
    underlying_price: Optional[float] = None
    req_id: Optional[int] = None

    def get_price(self, price_field: PriceField) -> float:
        """Get price based on configured field"""
        if price_field == PriceField.BID:
            return self.bid
        elif price_field == PriceField.ASK:
            return self.ask
        elif price_field == PriceField.LAST:
            return self.last if self.last > 0 else self.mid
        else:  # MID
            return self.mid if self.mid > 0 else (self.bid + self.ask) / 2


@dataclass
class OptionChain:
    """Represents an option chain for a specific expiry"""
    symbol: str
    expiry: str
    underlying_price: float
    calls: Dict[float, OptionContract] = field(default_factory=dict)
    puts: Dict[float, OptionContract] = field(default_factory=dict)
    strikes: List[float] = field(default_factory=list)

    def get_atm_strike(self, strike_step: float = 5.0) -> float:
        """Get the at-the-money strike"""
        if not self.strikes:
            return round(self.underlying_price / strike_step) * strike_step
        return min(self.strikes, key=lambda x: abs(x - self.underlying_price))

    def get_contract_by_delta(
        self,
        option_type: OptionType,
        target_delta: float,
        underlying_price: float
    ) -> Optional[OptionContract]:
        """Find option contract closest to target delta"""
        contracts = self.calls if option_type == OptionType.CALL else self.puts

        if not contracts:
            return None

        valid_contracts = [
            (strike, contract) for strike, contract in contracts.items()
            if contract.delta is not None
        ]

        if not valid_contracts:
            return self._estimate_by_moneyness(option_type, target_delta, underlying_price)

        target_abs = abs(target_delta)
        best_contract = min(
            valid_contracts,
            key=lambda x: abs(abs(x[1].delta) - target_abs)
        )

        return best_contract[1]

    def _estimate_by_moneyness(
        self,
        option_type: OptionType,
        target_delta: float,
        underlying_price: float
    ) -> Optional[OptionContract]:
        """Estimate strike based on delta approximation when Greeks unavailable"""
        contracts = self.calls if option_type == OptionType.CALL else self.puts

        if not contracts:
            return None

        if option_type == OptionType.CALL:
            otm_factor = 1 + (0.50 - abs(target_delta)) * 0.1
            target_strike = underlying_price * otm_factor
        else:
            otm_factor = 1 - (0.50 - abs(target_delta)) * 0.1
            target_strike = underlying_price * otm_factor

        best_strike = min(contracts.keys(), key=lambda x: abs(x - target_strike))
        return contracts[best_strike]


# ============================================================================
# Order Data
# ============================================================================

@dataclass
class OrderInfo:
    """Information about a submitted order"""
    order_id: int
    contract: Contract
    order: Order
    action: str
    quantity: int
    order_type: str

    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: int = 0
    remaining_quantity: int = 0
    avg_fill_price: float = 0.0
    last_fill_price: float = 0.0

    submit_time: datetime = None
    fill_time: Optional[datetime] = None
    error_code: Optional[int] = None
    error_message: Optional[str] = None

    def __post_init__(self):
        if self.submit_time is None:
            self.submit_time = datetime.now(US_EASTERN)
        self.remaining_quantity = self.quantity


@dataclass
class ComboOrder:
    """Information about a combo/spread order"""
    combo_id: str
    leg_orders: List[OrderInfo] = field(default_factory=list)
    status: OrderStatus = OrderStatus.PENDING
    submit_time: datetime = None
    fill_time: Optional[datetime] = None

    def __post_init__(self):
        if self.submit_time is None:
            self.submit_time = datetime.now(US_EASTERN)

    def is_fully_filled(self) -> bool:
        return all(o.status == OrderStatus.FILLED for o in self.leg_orders)

    def get_total_filled(self) -> int:
        return sum(1 for o in self.leg_orders if o.status == OrderStatus.FILLED)


# ============================================================================
# Account Data
# ============================================================================

@dataclass
class AccountInfo:
    """Account information"""
    account_id: str = ""
    net_liquidation: float = 0.0
    total_cash: float = 0.0
    buying_power: float = 0.0
    available_funds: float = 0.0
    excess_liquidity: float = 0.0
    maintenance_margin: float = 0.0
    initial_margin: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    currency: str = "USD"
    last_update: Optional[datetime] = None


@dataclass
class PositionInfo:
    """Position information"""
    account: str
    symbol: str
    sec_type: str
    strike: float
    right: str
    expiry: str
    position: float
    avg_cost: float
    market_value: float = 0.0
    unrealized_pnl: float = 0.0
    contract: Optional[Contract] = None
