"""
Exception Handling Module

Author: client Options Trading Engine
"""

# Base
from .base import clientError

# Broker errors (1000-1999)
from .broker_errors import (
    BrokerError,
    ConnectionError,
    ConnectionTimeoutError,
    DisconnectedError,
    AuthenticationError,
)

# Data errors (2000-2999)
from .data_errors import (
    DataError,
    MarketDataError,
    NoMarketDataError,
    OptionChainError,
    NoOptionFoundError,
    StaleDataError,
    IVDataError,
)

# Strategy errors (3000-3999)
from .strategy_errors import (
    StrategyError,
    StrategyNotFoundError,
    StrategyBuildError,
    LegBuildError,
    NoStrategyMatchError,
    InvalidStrategyConfigError,
)

# Execution errors (4000-4999)
from .execution_errors import (
    ExecutionError,
    OrderSubmitError,
    OrderRejectError,
    OrderTimeoutError,
    PartialFillError,
    SpreadExecutionError,
    OrderCancelError,
)

# Position/Risk errors (5000-5999)
from .position_errors import (
    PositionError,
    NoPositionError,
    PositionAlreadyExistsError,
    RiskLimitExceededError,
    InsufficientCapitalError,
    InvalidPositionSizeError,
)

# Config errors (6000-6999)
from .config_errors import (
    ConfigError,
    ConfigFileNotFoundError,
    ConfigParseError,
    ConfigValidationError,
    MissingConfigError,
)

# System errors (9000-9999)
from .system_errors import (
    SystemError,
    CriticalError,
    EmergencyShutdownError,
    StateError,
)

# Error codes
from .error_codes import ERROR_CODES, get_error_name

__all__ = [
    # Base
    "clientError",
    # Broker
    "BrokerError",
    "ConnectionError",
    "ConnectionTimeoutError",
    "DisconnectedError",
    "AuthenticationError",
    # Data
    "DataError",
    "MarketDataError",
    "NoMarketDataError",
    "OptionChainError",
    "NoOptionFoundError",
    "StaleDataError",
    "IVDataError",
    # Strategy
    "StrategyError",
    "StrategyNotFoundError",
    "StrategyBuildError",
    "LegBuildError",
    "NoStrategyMatchError",
    "InvalidStrategyConfigError",
    # Execution
    "ExecutionError",
    "OrderSubmitError",
    "OrderRejectError",
    "OrderTimeoutError",
    "PartialFillError",
    "SpreadExecutionError",
    "OrderCancelError",
    # Position/Risk
    "PositionError",
    "NoPositionError",
    "PositionAlreadyExistsError",
    "RiskLimitExceededError",
    "InsufficientCapitalError",
    "InvalidPositionSizeError",
    # Config
    "ConfigError",
    "ConfigFileNotFoundError",
    "ConfigParseError",
    "ConfigValidationError",
    "MissingConfigError",
    # System
    "SystemError",
    "CriticalError",
    "EmergencyShutdownError",
    "StateError",
    # Error codes
    "ERROR_CODES",
    "get_error_name",
]
