"""
Configuration loader module
Loads and validates all CSV configuration files
"""

import csv
from pathlib import Path
from datetime import datetime, time
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field

from ...enums import (
    DirectionBias, VolRegime, TrendRegime, DirectionSource,
    TrendMode, ExpiryRule, PriceField, OptionType, OrderSide
)


@dataclass
class StrategyLeg:
    """Represents a single leg in a strategy template"""
    leg_index: int
    option_type: OptionType
    side: OrderSide
    target_delta_abs: float
    expiry_rule: ExpiryRule
    quantity_factor: int = 1


@dataclass
class StrategyTemplate:
    """Represents a complete strategy template"""
    strategy_id: str
    legs: List[StrategyLeg] = field(default_factory=list)

    def is_credit_strategy(self) -> bool:
        """Check if this is a credit strategy based on strategy_id.

        Credit strategies: STRAT_CREDIT_*, STRAT_IRON_*, STRAT_SHORT_*, STRAT_RATIO_*
        Debit strategies: STRAT_DEBIT_*, STRAT_LONG_*
        """
        credit_prefixes = ("STRAT_CREDIT_", "STRAT_IRON_", "STRAT_SHORT_", "STRAT_RATIO_")
        return self.strategy_id.startswith(credit_prefixes)


@dataclass
class StrategyMappingRule:
    """Represents a rule for strategy selection"""
    direction: Optional[DirectionBias]  # None = ANY
    vol_regime: Optional[VolRegime]  # None = ANY
    trend_regime: Optional[TrendRegime]  # None = ANY
    custom_flag_1: Optional[str]  # None = ANY
    custom_flag_2: Optional[str]  # None = ANY
    strategy_id: str
    priority: int = 0


@dataclass
class EngineConfig:
    """Main configuration object for the trading engine"""
    # Symbol settings
    underlying_symbol: str = "SPX"
    underlying_exchange: str = "CBOE"
    underlying_sec_type: str = "IND"
    underlying_currency: str = "USD"

    # Session times
    session_start_time: time = time(9, 30)
    session_end_time: time = time(16, 0)
    bar_interval_minutes: int = 5

    # ORB settings
    use_orb: bool = True
    orb_window_minutes: int = 5
    orb_start_time: time = time(9, 30)
    orb_breakout_start_time: time = time(9, 35)
    orb_breakout_end_time: time = time(15, 30)

    # Trend filter settings
    use_trend_filter: bool = True
    ma_short_length: int = 50
    ma_long_length: int = 100
    trend_mode: TrendMode = TrendMode.CONFIRM_WITH_TREND

    # Direction source
    direction_source: DirectionSource = DirectionSource.ORB_AND_TREND_AGREE

    # Volatility settings
    use_ivp: bool = True
    ivp_window_days: int = 252
    ivp_1_threshold: float = 40.0
    ivp_2_threshold: float = 60.0
    iv_reference_times: List[time] = field(default_factory=lambda: [time(10, 5)])
    atm_strike_rounding_step: float = 5.0
    iv_smoothing_method: str = "average"

    # Options settings
    option_entry_price_field: PriceField = PriceField.MID
    use_broker_iv: bool = True
    use_broker_delta: bool = True
    underlying_trading_class: str = "SPXW"
    default_expiry_rule: ExpiryRule = ExpiryRule.SAME_DAY

    # Strike search settings
    strike_step: float = 5.0
    max_strike_range_points: float = 200.0
    strike_search_expand_step_points: float = 10.0
    max_spread_width_points: float = 25.0  # Max spread width for vertical spreads

    # Entry settings
    entry_time_mode: str = "on_signal_close_plus_offset"
    entry_offset_minutes: int = 2
    slippage_per_leg: float = 0.05

    # Order execution settings
    enable_price_chasing: bool = True
    chase_interval_seconds: float = 3.0
    max_chase_time_seconds: float = 86400.0  # 24 hours - chase continuously until filled
    max_price_deviation_pct: float = 3.0

    # Exit order settings
    # LIMIT = use price chasing like entry (better price)
    # MARKET = use market orders with continuous retry (guaranteed fill)
    exit_order_type: str = "LIMIT"
    market_exit_retry_interval: float = 5.0  # Seconds between MARKET order retries

    # Bracket order settings (IBKR handles exits)
    # When enabled, places SL + Target orders after entry fill
    # IBKR monitors and executes exits automatically
    use_bracket_orders: bool = False
    sl_limit_offset: float = 1.50  # $ amount added to SL trigger for limit price

    # Risk settings
    credit_target_factor: float = 0.30
    credit_stop_factor: float = 0.30
    debit_target_factor: float = 1.50
    debit_stop_factor: float = 0.50
    min_risk_reward_ratio: float = 0.15

    # Monitoring settings
    monitor_interval_minutes: int = 5
    cutoff_time_minutes: int = 5  # Minutes before session_end to force exit
    exit_on_target: bool = True
    exit_on_stop: bool = True
    exit_on_time: bool = True
    entry_cutoff_time_minutes: Optional[int] = None  # Minutes before session_end to block new entries

    # Computed time fields (calculated from session_end_time - minutes)
    cutoff_time: Optional[time] = None
    entry_cutoff_time: Optional[time] = None

    # Position sizing
    max_cap_to_be_used: float = 100000.0
    risk_per_trade: float = 5000.0  # Max risk per trade in dollars
    contract_size: int = 100
    max_contracts: int = 10  # Maximum contracts per trade

    # IBKR settings
    ibkr_host: str = "127.0.0.1"
    ibkr_port: int = 7497  # Paper trading
    ibkr_client_id: int = 1
    ibkr_bar_timezone_offset_hours: int = 1  # Hours to add to bar time to convert to ET (1 for CT, 0 for ET)

    # Logging settings
    log_dir: str = "logs"
    log_level: str = "INFO"
    log_trades: bool = True
    log_signals: bool = True

    # Execution Mode Settings
    signal_only_mode: bool = False  # If True, generate signals without placing orders
    ignore_manual_positions: bool = True  # DEPRECATED: Use auto_recover_positions instead
    auto_recover_positions: bool = False  # If True, auto-recover existing positions in TWS on startup

    # Connection Settings
    auto_reconnect: bool = True  # Automatically reconnect on disconnect
    max_reconnect_attempts: int = 5  # Maximum reconnection attempts
    reconnect_base_delay: float = 2.0  # Base delay between reconnection attempts (seconds)
    reconnect_max_delay: float = 60.0  # Maximum delay between reconnection attempts (seconds)
    connection_health_check_interval: int = 30  # Seconds between connection health checks

    # Circuit Breaker Settings
    circuit_breaker_enabled: bool = True  # Enable circuit breaker for error accumulation
    max_consecutive_errors: int = 10  # Max consecutive errors before circuit breaker trips
    error_cooldown_seconds: float = 300.0  # Cooldown period after circuit breaker trips (5 min)

    # Order Management Settings
    cancel_unfilled_on_timeout: bool = False  # Keep orders active, don't cancel on timeout
    cancel_stale_orders_on_startup: bool = True  # Cancel stale orders from previous sessions on startup
    order_timeout_seconds: float = 60.0  # Timeout for order fills
    order_tif: str = "DAY"  # Time-in-force: DAY, GTC, IOC, FOK

    # Daily P&L Limit (Risk Management)
    daily_loss_limit: float = 0.0  # Max daily loss before stopping trading (0 = disabled)
    daily_profit_target: float = 0.0  # Stop trading after reaching daily profit (0 = disabled)

    # Position Size Limits
    min_contracts: int = 1  # Minimum contracts per trade
    max_contracts: int = 10  # Maximum contracts per trade


class ConfigLoader:
    """
    Loads and validates configuration from CSV files
    """

    def __init__(self, config_dir: str = "config"):
        self.config_dir = Path(config_dir)
        self.config = EngineConfig()
        self.strategy_mapping: List[StrategyMappingRule] = []
        self.strategy_templates: Dict[str, StrategyTemplate] = {}
        self.validation_errors: List[str] = []

    def load_all(self) -> bool:
        """
        Load all configuration files.
        Returns True if successful, False if validation fails.
        """
        self.validation_errors.clear()

        try:
            self._load_parameters()
            self._load_strategy_mapping()
            self._load_strategy_templates()
            self._validate_all()
        except Exception as e:
            self.validation_errors.append(f"Configuration loading error: {str(e)}")

        return len(self.validation_errors) == 0

    def _load_parameters(self):
        """Load parameters.csv"""
        params_file = self.config_dir / "parameters.csv"
        if not params_file.exists():
            self.validation_errors.append(f"File not found: {params_file}")
            return

        with open(params_file, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                param_name = row.get("parameter_name") or ""
                param_value = row.get("value") or ""

                param_name = param_name.strip()
                param_value = param_value.strip()

                if not param_name or param_name.startswith("#"):
                    continue

                self._set_parameter(param_name, param_value)

        # Compute cutoff times from session_end_time and minutes values
        self._compute_cutoff_times()

    def _compute_cutoff_times(self):
        """Compute cutoff_time and entry_cutoff_time from session_end_time - minutes"""
        # Compute cutoff_time = session_end_time - cutoff_time_minutes
        self.config.cutoff_time = self._subtract_minutes_from_time(
            self.config.session_end_time,
            self.config.cutoff_time_minutes
        )

        # Compute entry_cutoff_time if minutes value is set
        if self.config.entry_cutoff_time_minutes is not None:
            self.config.entry_cutoff_time = self._subtract_minutes_from_time(
                self.config.session_end_time,
                self.config.entry_cutoff_time_minutes
            )

    def _subtract_minutes_from_time(self, t: time, minutes: int) -> time:
        """Subtract minutes from a time object"""
        total_minutes = t.hour * 60 + t.minute - minutes
        # Handle underflow (wrap to previous day) and overflow (wrap to next day)
        total_minutes = total_minutes % (24 * 60)  # Handles both negative and >= 1440
        return time(total_minutes // 60, total_minutes % 60)

    def _set_parameter(self, name: str, value: str):
        """Set a parameter on the config object with type conversion"""
        if not hasattr(self.config, name):
            # Skip unknown parameters (might be comments or future params)
            return

        current_value = getattr(self.config, name)
        current_type = type(current_value)

        try:
            if current_type == bool:
                setattr(self.config, name, value.upper() in ("YES", "TRUE", "1"))
            elif current_type == int:
                setattr(self.config, name, int(value))
            elif current_type == float:
                setattr(self.config, name, float(value))
            elif current_type == time:
                parsed_time = self._parse_time(value)
                setattr(self.config, name, parsed_time)
            elif name == "entry_cutoff_time_minutes":
                # Special handling for Optional[int] field (default is None)
                setattr(self.config, name, int(value))
            elif current_type == list and name == "iv_reference_times":
                times = [self._parse_time(t.strip()) for t in value.split(",")]
                setattr(self.config, name, times)
            elif isinstance(current_value, DirectionSource):
                setattr(self.config, name, DirectionSource.from_string(value))
            elif isinstance(current_value, TrendMode):
                setattr(self.config, name, TrendMode.from_string(value))
            elif isinstance(current_value, ExpiryRule):
                setattr(self.config, name, ExpiryRule.from_string(value))
            elif isinstance(current_value, PriceField):
                setattr(self.config, name, PriceField.from_string(value))
            else:
                setattr(self.config, name, value)
        except Exception as e:
            self.validation_errors.append(
                f"Error parsing parameter '{name}' with value '{value}': {str(e)}"
            )

    def _parse_time(self, time_str: str) -> time:
        """Parse time string in HH:MM format"""
        parts = time_str.split(":")
        if len(parts) < 2:
            raise ValueError(f"Invalid time format '{time_str}'. Expected HH:MM format.")
        return time(int(parts[0]), int(parts[1]))

    def _load_strategy_mapping(self):
        """Load strategy_mapping.csv"""
        mapping_file = self.config_dir / "strategy_mapping.csv"
        if not mapping_file.exists():
            self.validation_errors.append(f"File not found: {mapping_file}")
            return

        with open(mapping_file, "r") as f:
            reader = csv.DictReader(f)
            priority = 0
            for row in reader:
                try:
                    # Skip comment lines
                    direction_val = row.get("direction") or ""
                    if direction_val.strip().startswith("#") or not direction_val.strip():
                        continue

                    rule = StrategyMappingRule(
                        direction=self._parse_direction(direction_val),
                        vol_regime=self._parse_vol_regime(row.get("vol_regime") or "ANY"),
                        trend_regime=self._parse_trend_regime(row.get("trend_regime") or "ANY"),
                        custom_flag_1=self._parse_any(row.get("custom_flag_1") or "ANY"),
                        custom_flag_2=self._parse_any(row.get("custom_flag_2") or "ANY"),
                        strategy_id=(row.get("strategy_id") or "").strip(),
                        priority=priority
                    )
                    self.strategy_mapping.append(rule)
                    priority += 1
                except Exception as e:
                    self.validation_errors.append(
                        f"Error parsing strategy mapping row: {row}, Error: {str(e)}"
                    )

    def _parse_direction(self, value: str) -> Optional[DirectionBias]:
        """Parse direction value, return None for ANY"""
        value = value.strip().upper()
        if value in ("ANY", ""):
            return None
        return DirectionBias(value)

    def _parse_vol_regime(self, value: str) -> Optional[VolRegime]:
        """Parse vol regime value, return None for ANY"""
        value = value.strip().lower()
        if value in ("any", ""):
            return None
        return VolRegime(value)

    def _parse_trend_regime(self, value: str) -> Optional[TrendRegime]:
        """Parse trend regime value, return None for ANY"""
        value = value.strip().upper()
        if value in ("ANY", ""):
            return None
        return TrendRegime(value)

    def _parse_any(self, value: str) -> Optional[str]:
        """Parse generic value, return None for ANY"""
        value = value.strip()
        if value.upper() in ("ANY", ""):
            return None
        return value

    def _load_strategy_templates(self):
        """Load strategy_templates.csv"""
        templates_file = self.config_dir / "strategy_templates.csv"
        if not templates_file.exists():
            self.validation_errors.append(f"File not found: {templates_file}")
            return

        with open(templates_file, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    strategy_id = (row.get("strategy_id") or "").strip()
                    if not strategy_id or strategy_id.startswith("#"):
                        continue

                    # Create template if it doesn't exist
                    if strategy_id not in self.strategy_templates:
                        self.strategy_templates[strategy_id] = StrategyTemplate(
                            strategy_id=strategy_id
                        )

                    # Parse leg - use default_expiry_rule from parameters.csv if not specified in template
                    expiry_value = row.get("expiry") or row.get("expiry_rule")
                    if not expiry_value or expiry_value.strip() == "":
                        # Use default from parameters.csv (single source of truth)
                        expiry_value = self.config.default_expiry_rule.value if hasattr(self.config.default_expiry_rule, 'value') else str(self.config.default_expiry_rule)

                    # Parse and validate quantity factor
                    qty_factor = int(row.get("qty") or row.get("quantity_factor") or 1)
                    if qty_factor <= 0:
                        raise ValueError(f"quantity_factor must be > 0, got {qty_factor}")

                    leg = StrategyLeg(
                        leg_index=int(row.get("leg") or row.get("leg_index") or 1),
                        option_type=OptionType.from_string(row.get("type") or row.get("option_type") or "C"),
                        side=OrderSide.from_string(row.get("side") or "BUY"),
                        target_delta_abs=float(row.get("delta") or row.get("target_delta_abs") or 0.25),
                        expiry_rule=ExpiryRule.from_string(expiry_value),
                        quantity_factor=qty_factor
                    )

                    self.strategy_templates[strategy_id].legs.append(leg)

                except Exception as e:
                    self.validation_errors.append(
                        f"Error parsing strategy template row: {row}, Error: {str(e)}"
                    )

        # Sort legs by index for each template
        for template in self.strategy_templates.values():
            template.legs.sort(key=lambda x: x.leg_index)

    def _validate_all(self):
        """Perform all validation checks"""
        self._validate_required_parameters()
        self._validate_ranges()
        self._validate_times()
        self._validate_strategy_consistency()

    def _validate_required_parameters(self):
        """Check that all required parameters are present and valid"""
        required = [
            "underlying_symbol",
            "session_start_time",
            "session_end_time",
            "credit_target_factor",
            "credit_stop_factor"
        ]
        for param in required:
            if not getattr(self.config, param, None):
                self.validation_errors.append(f"Missing required parameter: {param}")

    def _validate_ranges(self):
        """Validate parameter ranges"""
        # Credit target factor must be between 0 and 1
        if not (0 < self.config.credit_target_factor < 1):
            self.validation_errors.append(
                f"credit_target_factor ({self.config.credit_target_factor}) must be between 0 and 1"
            )

        # Credit stop factor must be greater than 0 (represents % above entry for SL)
        # e.g., 0.30 = SL at 130% of entry, 1.50 = SL at 250% of entry
        if not (self.config.credit_stop_factor > 0):
            self.validation_errors.append(
                f"credit_stop_factor ({self.config.credit_stop_factor}) must be greater than 0"
            )

        # Debit target factor must be greater than 1
        if not (self.config.debit_target_factor > 1):
            self.validation_errors.append(
                f"debit_target_factor ({self.config.debit_target_factor}) must be greater than 1"
            )

        # Debit stop factor must be between 0 and 1
        if not (0 < self.config.debit_stop_factor < 1):
            self.validation_errors.append(
                f"debit_stop_factor ({self.config.debit_stop_factor}) must be between 0 and 1"
            )

        # IVP thresholds
        if self.config.use_ivp:
            if not (self.config.ivp_1_threshold < self.config.ivp_2_threshold):
                self.validation_errors.append(
                    f"ivp_1_threshold ({self.config.ivp_1_threshold}) must be less than "
                    f"ivp_2_threshold ({self.config.ivp_2_threshold})"
                )

        # MA lengths
        if self.config.use_trend_filter:
            if not (self.config.ma_short_length < self.config.ma_long_length):
                self.validation_errors.append(
                    f"ma_short_length ({self.config.ma_short_length}) must be less than "
                    f"ma_long_length ({self.config.ma_long_length})"
                )

    def _validate_times(self):
        """Validate time-related parameters"""
        # Session start before end (allow overnight sessions where end < start)
        # e.g., 20:00 to 02:30 is valid (overnight session)
        # Only invalid if they are exactly equal
        if self.config.session_start_time == self.config.session_end_time:
            self.validation_errors.append(
                "session_start_time cannot equal session_end_time"
            )

        # ORB breakout start must be after ORB window
        orb_end = self._add_minutes_to_time(
            self.config.orb_start_time,
            self.config.orb_window_minutes
        )
        if self.config.orb_breakout_start_time < orb_end:
            self.validation_errors.append(
                f"orb_breakout_start_time must be after ORB window ends "
                f"(ORB ends at {orb_end})"
            )

        # Cutoff minutes must be positive
        if self.config.cutoff_time_minutes <= 0:
            self.validation_errors.append(
                "cutoff_time_minutes must be positive (e.g., 5 for 5 min before session end)"
            )

        # Bug #9 fix: Validate cutoff doesn't exceed session duration
        session_start = self.config.session_start_time
        session_end = self.config.session_end_time
        if session_start and session_end:
            # Calculate session duration in minutes
            start_mins = session_start.hour * 60 + session_start.minute
            end_mins = session_end.hour * 60 + session_end.minute
            if end_mins < start_mins:
                # Overnight session (e.g., 20:00 to 02:30)
                session_duration = (24 * 60 - start_mins) + end_mins
            else:
                session_duration = end_mins - start_mins

            if self.config.cutoff_time_minutes >= session_duration:
                self.validation_errors.append(
                    f"cutoff_time_minutes ({self.config.cutoff_time_minutes}) must be less than "
                    f"session duration ({session_duration} minutes)"
                )

        # Entry cutoff minutes must be positive and greater than cutoff (if configured)
        if self.config.entry_cutoff_time_minutes is not None:
            if self.config.entry_cutoff_time_minutes <= 0:
                self.validation_errors.append(
                    "entry_cutoff_time_minutes must be positive"
                )
            elif self.config.entry_cutoff_time_minutes <= self.config.cutoff_time_minutes:
                self.validation_errors.append(
                    f"entry_cutoff_time_minutes ({self.config.entry_cutoff_time_minutes}) must be greater than "
                    f"cutoff_time_minutes ({self.config.cutoff_time_minutes}) - entry cutoff should be earlier"
                )

    def _add_minutes_to_time(self, t: time, minutes: int) -> time:
        """Add minutes to a time object"""
        total_minutes = t.hour * 60 + t.minute + minutes
        return time(total_minutes // 60, total_minutes % 60)

    def _validate_strategy_consistency(self):
        """Validate strategy mapping and templates are consistent"""
        # Check all strategy IDs in mapping exist in templates
        for rule in self.strategy_mapping:
            if rule.strategy_id == "NO_TRADE":
                continue
            if rule.strategy_id not in self.strategy_templates:
                self.validation_errors.append(
                    f"Strategy '{rule.strategy_id}' in mapping not found in templates"
                )

        # Check for duplicate leg indices in templates
        for template in self.strategy_templates.values():
            leg_indices = [leg.leg_index for leg in template.legs]
            if len(leg_indices) != len(set(leg_indices)):
                self.validation_errors.append(
                    f"Duplicate leg indices in strategy '{template.strategy_id}'"
                )

            # Validate delta values
            for leg in template.legs:
                if not (0.01 <= leg.target_delta_abs <= 0.99):
                    self.validation_errors.append(
                        f"Invalid delta {leg.target_delta_abs} in strategy "
                        f"'{template.strategy_id}' leg {leg.leg_index}"
                    )

    def get_validation_errors(self) -> List[str]:
        """Get list of validation errors"""
        return self.validation_errors

    def print_config_summary(self, logger=None):
        """Print configuration summary using logger"""
        import logging
        if logger is None:
            logger = logging.getLogger("ConfigLoader")

        logger.info("=" * 60)
        logger.info("CONFIGURATION SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Underlying: {self.config.underlying_symbol}")
        logger.info(f"Session: {self.config.session_start_time} - {self.config.session_end_time}")
        logger.info(f"ORB Enabled: {self.config.use_orb}")
        logger.info(f"Trend Filter Enabled: {self.config.use_trend_filter}")
        logger.info(f"IVP Enabled: {self.config.use_ivp}")
        logger.info(f"Strategy Mapping Rules: {len(self.strategy_mapping)}")
        logger.info(f"Strategy Templates: {len(self.strategy_templates)}")
        logger.info(f"Total Legs: {sum(len(t.legs) for t in self.strategy_templates.values())}")
        logger.info("=" * 60)
