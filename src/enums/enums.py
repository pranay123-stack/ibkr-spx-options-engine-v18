"""
Enumeration classes for the trading engine
"""

from enum import Enum, auto


class DirectionBias(Enum):
    """Direction bias from ORB or trend analysis"""
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"

    @classmethod
    def from_string(cls, value: str) -> "DirectionBias":
        """Convert string to enum, handles 'ANY' as wildcard"""
        if value.upper() == "ANY":
            return None
        return cls(value.upper())


class VolRegime(Enum):
    """Volatility regime based on IVP"""
    VOL_1 = "vol_1"  # Low volatility
    VOL_2 = "vol_2"  # Medium volatility
    VOL_3 = "vol_3"  # High volatility

    @classmethod
    def from_string(cls, value: str) -> "VolRegime":
        """Convert string to enum, handles 'ANY' as wildcard"""
        if value.upper() == "ANY":
            return None
        return cls(value.lower())


class TrendRegime(Enum):
    """Trend regime from moving average analysis"""
    UPTREND = "UPTREND"
    DOWNTREND = "DOWNTREND"
    SIDEWAYS = "SIDEWAYS"

    @classmethod
    def from_string(cls, value: str) -> "TrendRegime":
        """Convert string to enum, handles 'ANY' as wildcard"""
        if value.upper() == "ANY":
            return None
        return cls(value.upper())


class ExitReason(Enum):
    """Reason for position exit"""
    TARGET = "TARGET"
    STOP = "STOP"
    TIME = "TIME"
    MANUAL = "MANUAL"
    ERROR = "ERROR"


class OptionType(Enum):
    """Option type"""
    CALL = "C"
    PUT = "P"

    @classmethod
    def from_string(cls, value: str) -> "OptionType":
        if value.upper() in ("C", "CALL"):
            return cls.CALL
        elif value.upper() in ("P", "PUT"):
            return cls.PUT
        raise ValueError(f"Invalid option type: {value}")


class OrderSide(Enum):
    """Order side"""
    BUY = "BUY"
    SELL = "SELL"

    @classmethod
    def from_string(cls, value: str) -> "OrderSide":
        return cls(value.upper())


class DirectionSource(Enum):
    """Source for direction bias determination"""
    ORB_ONLY = "orb_only"
    TREND_ONLY = "trend_only"
    ORB_AND_TREND_AGREE = "orb_and_trend_agree"
    WITHIN_RANGE = "within_range"

    @classmethod
    def from_string(cls, value: str) -> "DirectionSource":
        return cls(value.lower())


class TrendMode(Enum):
    """Mode for trend filter application"""
    CONFIRM_WITH_TREND = "confirm_with_trend"
    IGNORE_TREND = "ignore_trend"

    @classmethod
    def from_string(cls, value: str) -> "TrendMode":
        return cls(value.lower())


class ExpiryRule(Enum):
    """Rule for option expiry selection"""
    SAME_DAY = "same_day"
    NEAREST = "nearest"
    NEAREST_WEEKLY = "nearest_weekly"
    PLUS_ONE_DAY = "plus_one_day"

    @classmethod
    def from_string(cls, value: str) -> "ExpiryRule":
        return cls(value.lower())


class PriceField(Enum):
    """Price field to use for option pricing"""
    LAST = "last"
    MID = "mid"
    BID = "bid"
    ASK = "ask"

    @classmethod
    def from_string(cls, value: str) -> "PriceField":
        return cls(value.lower())


class EngineState(Enum):
    """Trading engine state"""
    INITIALIZING = auto()
    WAITING_FOR_SESSION = auto()
    COLLECTING_ORB = auto()
    WAITING_FOR_BREAKOUT = auto()
    MONITORING_SIGNALS = auto()
    IDLE_NO_TRADE = auto()  # No breakout detected, engine idle until session end
    POSITION_OPEN = auto()
    MONITORING_POSITION = auto()
    SESSION_ENDED = auto()
    ERROR = auto()
    SHUTDOWN = auto()


class OrderStatus(Enum):
    """Order status"""
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    ERROR = "ERROR"
