"""
Logging module for the trading engine
Provides structured logging for trades, signals, and system events
with colored console output for better readability.
"""

import logging
import csv
import os
import sys
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from pathlib import Path
import json

from ..timezone import US_EASTERN, IST


# ============================================================================
# ANSI COLOR CODES
# ============================================================================

class Colors:
    """ANSI color codes for terminal output"""
    # Reset
    RESET = "\033[0m"

    # Regular colors
    BLACK = "\033[30m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"

    # Bright colors
    BRIGHT_BLACK = "\033[90m"
    BRIGHT_RED = "\033[91m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_BLUE = "\033[94m"
    BRIGHT_MAGENTA = "\033[95m"
    BRIGHT_CYAN = "\033[96m"
    BRIGHT_WHITE = "\033[97m"

    # Background colors
    BG_BLACK = "\033[40m"
    BG_RED = "\033[41m"
    BG_GREEN = "\033[42m"
    BG_YELLOW = "\033[43m"
    BG_BLUE = "\033[44m"
    BG_MAGENTA = "\033[45m"
    BG_CYAN = "\033[46m"
    BG_WHITE = "\033[47m"

    # Styles
    BOLD = "\033[1m"
    DIM = "\033[2m"
    UNDERLINE = "\033[4m"
    BLINK = "\033[5m"
    REVERSE = "\033[7m"


# ============================================================================
# PHASE COLORS - Different colors for different trading phases
# ============================================================================

class PhaseColors:
    """Colors for different trading phases - makes logs easy to scan"""
    # Startup/Init - Blue
    STARTUP = Colors.BRIGHT_BLUE

    # Connection - Cyan
    CONNECTION = Colors.BRIGHT_CYAN

    # Waiting/Idle - Gray
    WAITING = Colors.BRIGHT_BLACK

    # ORB Collection - Yellow
    ORB = Colors.BRIGHT_YELLOW

    # Breakout Detection - Magenta
    BREAKOUT = Colors.BRIGHT_MAGENTA + Colors.BOLD

    # Signal Generation - Cyan
    SIGNAL = Colors.BRIGHT_CYAN

    # Strategy Selection - Blue
    STRATEGY = Colors.BRIGHT_BLUE

    # Entry - Green
    ENTRY = Colors.BRIGHT_GREEN + Colors.BOLD

    # Position Monitoring - White
    POSITION = Colors.WHITE

    # Exit - Yellow
    EXIT = Colors.BRIGHT_YELLOW + Colors.BOLD

    # Profit/Win - Green
    WIN = Colors.BRIGHT_GREEN + Colors.BOLD

    # Loss - Red
    LOSS = Colors.BRIGHT_RED + Colors.BOLD

    # Health Check - Cyan
    HEALTH = Colors.CYAN

    # Error - Red
    ERROR = Colors.BRIGHT_RED

    # Warning - Yellow
    WARNING = Colors.BRIGHT_YELLOW

    # Shutdown - Yellow
    SHUTDOWN = Colors.YELLOW


class ColoredFormatter(logging.Formatter):
    """
    Custom formatter that colors the ENTIRE log line based on log level.
    Also highlights key trading terms and values with special colors.
    """

    # Full line colors for each level - mixed colors for better distinction
    LEVEL_COLORS = {
        logging.DEBUG: Colors.BRIGHT_BLACK,  # Gray/dim for debug
        logging.INFO: Colors.CYAN,           # Cyan for info (default)
        logging.WARNING: Colors.BRIGHT_YELLOW,
        logging.ERROR: Colors.BRIGHT_RED,
        logging.CRITICAL: Colors.BG_RED + Colors.WHITE + Colors.BOLD,
    }

    # Keywords to highlight with special colors (stand out from base color)
    HIGHLIGHT_PATTERNS = {
        # States and events - make them pop
        "BREAKOUT": Colors.BRIGHT_MAGENTA + Colors.BOLD,
        "SIGNAL": Colors.BRIGHT_CYAN + Colors.BOLD,
        "ENTRY": Colors.BRIGHT_GREEN + Colors.BOLD,
        "EXIT": Colors.BRIGHT_YELLOW + Colors.BOLD,
        "POSITION": Colors.BRIGHT_BLUE + Colors.BOLD,
        "TARGET": Colors.BRIGHT_GREEN + Colors.BOLD,
        "STOP": Colors.BRIGHT_RED + Colors.BOLD,
        "WIN": Colors.BRIGHT_GREEN + Colors.BOLD,
        "LOSS": Colors.BRIGHT_RED + Colors.BOLD,
        "PROFIT": Colors.BRIGHT_GREEN + Colors.BOLD,
        "PASS": Colors.BRIGHT_GREEN + Colors.BOLD,
        "FAIL": Colors.BRIGHT_RED + Colors.BOLD,
        "OK": Colors.BRIGHT_GREEN,
        "ERROR": Colors.BRIGHT_RED + Colors.BOLD,
        "WARNING": Colors.BRIGHT_YELLOW + Colors.BOLD,
        "CRITICAL": Colors.BG_RED + Colors.WHITE + Colors.BOLD,
        # Directions
        "BULLISH": Colors.BRIGHT_GREEN + Colors.BOLD,
        "BEARISH": Colors.BRIGHT_RED + Colors.BOLD,
        "NEUTRAL": Colors.BRIGHT_YELLOW,
        # Regimes
        "HIGH_VOL": Colors.BRIGHT_RED + Colors.BOLD,
        "LOW_VOL": Colors.BRIGHT_GREEN + Colors.BOLD,
        "MED_VOL": Colors.BRIGHT_YELLOW,
        "UPTREND": Colors.BRIGHT_GREEN + Colors.BOLD,
        "DOWNTREND": Colors.BRIGHT_RED + Colors.BOLD,
        "RANGING": Colors.BRIGHT_YELLOW,
        # Actions
        "BUY": Colors.BRIGHT_GREEN + Colors.BOLD,
        "SELL": Colors.BRIGHT_RED + Colors.BOLD,
        "CREDIT": Colors.BRIGHT_CYAN + Colors.BOLD,
        "DEBIT": Colors.BRIGHT_MAGENTA + Colors.BOLD,
        # Session
        "SESSION STARTED": Colors.BRIGHT_GREEN + Colors.BOLD,
        "SESSION ENDED": Colors.BRIGHT_YELLOW + Colors.BOLD,
        "ENGINE STARTING": Colors.BRIGHT_CYAN + Colors.BOLD,
        "SHUTDOWN": Colors.BRIGHT_YELLOW + Colors.BOLD,
        "CONNECTED": Colors.BRIGHT_GREEN + Colors.BOLD,
        "DISCONNECTED": Colors.BRIGHT_RED + Colors.BOLD,
    }

    def __init__(self, fmt=None, datefmt=None, use_colors=True):
        super().__init__(fmt, datefmt)
        self.use_colors = use_colors and self._supports_color()

    def _supports_color(self) -> bool:
        """Check if the terminal supports colors"""
        # Check if output is a TTY
        if not hasattr(sys.stdout, 'isatty') or not sys.stdout.isatty():
            return False
        # Check for NO_COLOR environment variable
        if os.environ.get('NO_COLOR'):
            return False
        # Check for TERM
        term = os.environ.get('TERM', '')
        if term == 'dumb':
            return False
        return True

    def _highlight_keywords(self, message: str, base_color: str) -> str:
        """Highlight special keywords in the message"""
        result = message
        for keyword, color in self.HIGHLIGHT_PATTERNS.items():
            if keyword in result:
                # Apply keyword color, then reset back to base color
                result = result.replace(
                    keyword,
                    f"{color}{keyword}{Colors.RESET}{base_color}"
                )
        return result

    def _highlight_dollars(self, message: str, base_color: str) -> str:
        """Highlight dollar amounts with green/red based on sign"""
        import re
        result = message
        dollar_pattern = r'\$-?\d+\.?\d*'

        for match in re.finditer(dollar_pattern, message):
            amount_str = match.group()
            try:
                amount = float(amount_str.replace('$', ''))
                if amount > 0:
                    color = Colors.BRIGHT_GREEN + Colors.BOLD
                elif amount < 0:
                    color = Colors.BRIGHT_RED + Colors.BOLD
                else:
                    color = Colors.WHITE
                result = result.replace(
                    amount_str,
                    f"{color}{amount_str}{Colors.RESET}{base_color}",
                    1
                )
            except ValueError:
                pass
        return result

    def format(self, record: logging.LogRecord) -> str:
        """Format the log record with FULL LINE coloring"""
        if not self.use_colors:
            return super().format(record)

        # Get base color for this log level
        base_color = self.LEVEL_COLORS.get(record.levelno, Colors.WHITE)

        # Format the record first (plain)
        orig_msg = record.msg
        formatted = super().format(record)

        # Restore original
        record.msg = orig_msg

        # Apply base color to entire line
        colored_line = f"{base_color}{formatted}{Colors.RESET}"

        # Now highlight special keywords (they will pop out from the base color)
        colored_line = self._highlight_keywords(colored_line, base_color)

        # Highlight dollar amounts
        colored_line = self._highlight_dollars(colored_line, base_color)

        return colored_line


class TradingLogger:
    """
    Comprehensive logging class for the trading engine.
    Handles console logging, file logging, trade logs, and signal logs.
    """

    def __init__(
        self,
        log_dir: str = "logs",
        log_level: str = "INFO",
        log_trades: bool = True,
        log_signals: bool = True,
        log_to_console: bool = True
    ):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self.log_level = getattr(logging, log_level.upper(), logging.INFO)
        self.log_trades = log_trades
        self.log_signals = log_signals
        self.log_to_console = log_to_console

        # Create date-specific subdirectory for logs
        self.today = datetime.now(US_EASTERN).strftime("%Y-%m-%d")
        self.run_timestamp = datetime.now(US_EASTERN).strftime("%H%M%S")  # Unique per run
        self.daily_dir = self.log_dir / self.today
        self.daily_dir.mkdir(parents=True, exist_ok=True)

        # Create trading_data folder with subfolders for CSV files
        self.trading_data_dir = Path("trading_data") / self.today
        self.trades_dir = self.trading_data_dir / "trades"
        self.signals_dir = self.trading_data_dir / "signals"
        self.trades_dir.mkdir(parents=True, exist_ok=True)
        self.signals_dir.mkdir(parents=True, exist_ok=True)

        # Initialize loggers
        self._setup_main_logger()
        self._setup_trade_log()
        self._setup_signal_log()

    def _setup_main_logger(self):
        """Setup main application logger with colored console output"""
        self.logger = logging.getLogger("clientEngine")
        self.logger.setLevel(self.log_level)
        self.logger.handlers.clear()

        # File handler (no colors for file logs) - Issue 14: Use UTF-8 encoding
        # Single log file per day, appends across multiple runs
        log_file = self.daily_dir / f"engine_{self.today}.log"
        file_handler = logging.FileHandler(log_file, mode='a', encoding='utf-8')
        file_handler.setLevel(self.log_level)

        # File format (plain text, no colors)
        file_format = logging.Formatter(
            "[%(asctime)s] %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        file_handler.setFormatter(file_format)
        self.logger.addHandler(file_handler)

        # Console handler with colors
        if self.log_to_console:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(self.log_level)

            # Use ColoredFormatter for console output
            colored_format = ColoredFormatter(
                "[%(asctime)s] %(levelname)s - %(message)s",
                datefmt="%H:%M:%S",
                use_colors=True
            )
            console_handler.setFormatter(colored_format)
            self.logger.addHandler(console_handler)

    def _setup_trade_log(self):
        """Setup CSV trade log in trades/ subfolder (lazy - file created on first write)"""
        if not self.log_trades:
            return

        # Single CSV file per day (not per run) - accumulates all trades across restarts
        self.trade_log_file = self.trades_dir / f"trades_{self.today}.csv"
        # Simplified trade CSV format (18 columns)
        self.trade_log_headers = [
            "trade_date", "entry_time", "exit_time", "position_id", "strategy",
            "direction", "expiry", "legs", "contracts", "entry_spread", "exit_spread",
            "target", "stop", "exit_reason", "pnl", "duration_min", "spx_entry", "spx_exit"
        ]
        # File will be created on first trade write (lazy initialization)

    def _setup_signal_log(self):
        """Setup CSV signal log in signals/ subfolder (lazy - file created on first write)"""
        if not self.log_signals:
            return

        # Single CSV file per day (not per run) - accumulates all signals across restarts
        self.signal_log_file = self.signals_dir / f"signals_{self.today}.csv"
        # Simplified signal CSV format (14 columns)
        self.signal_log_headers = [
            "timestamp", "spx_price", "orb_high", "orb_low", "breakout",
            "direction", "ivp", "trend", "strategy", "legs",
            "entry_spread", "signal_ok", "executed", "reason"
        ]
        # File will be created on first signal write (lazy initialization)

    # Main logging methods
    def info(self, message: str):
        """Log info message"""
        self.logger.info(message)

    def debug(self, message: str):
        """Log debug message"""
        self.logger.debug(message)

    def warning(self, message: str):
        """Log warning message"""
        self.logger.warning(message)

    def error(self, message: str):
        """Log error message"""
        self.logger.error(message)

    def critical(self, message: str):
        """Log critical message"""
        self.logger.critical(message)

    # ========== Phase-Colored Logging ==========

    def _supports_color(self) -> bool:
        """Check if terminal supports colors"""
        if not hasattr(sys.stdout, 'isatty') or not sys.stdout.isatty():
            return False
        if os.environ.get('NO_COLOR'):
            return False
        return True

    def _colored(self, message: str, color: str) -> str:
        """Apply color to message if supported"""
        if self._supports_color():
            return f"{color}{message}{Colors.RESET}"
        return message

    def _phase_log(self, message: str, phase_color: str, level: str = "info"):
        """Log a message with phase-specific color"""
        colored_msg = self._colored(message, phase_color)
        getattr(self.logger, level)(colored_msg)

    # Structured logging methods
    def log_startup(self, version: str, config_dir: str):
        """Log engine startup - BLUE"""
        self._phase_log("=" * 60, PhaseColors.STARTUP)
        self._phase_log("ENGINE STARTING", PhaseColors.STARTUP)
        self._phase_log(f"Version: {version}", PhaseColors.STARTUP)
        self._phase_log(f"Config directory: {config_dir}", PhaseColors.STARTUP)
        self._phase_log(f"Log directory: {self.log_dir}", PhaseColors.STARTUP)
        self._phase_log("=" * 60, PhaseColors.STARTUP)

    def log_all_explanations(self, config):
        """
        Log all concept explanations ONCE at system startup.
        Call this after engine initialization to show all trading concepts.
        """
        # Mark all as explained so they don't show again during trades
        self._orb_calc_explained = True
        self._trend_calc_explained = True
        self._strategy_selection_explained = True
        self._delta_strike_explained = True

        self._phase_log("", PhaseColors.STARTUP)
        self._phase_log("=" * 70, PhaseColors.STARTUP)
        self._phase_log("                    TRADING STRATEGY CONCEPTS", PhaseColors.STARTUP)
        self._phase_log("=" * 70, PhaseColors.STARTUP)

        # ORB Explanation
        self._phase_log("", PhaseColors.ORB)
        self._phase_log("[1. ORB - OPENING RANGE BREAKOUT]", PhaseColors.ORB)
        self._phase_log(f"   - Collects price bars for first {getattr(config, 'orb_window_minutes', 30)} min after market open", PhaseColors.ORB)
        self._phase_log("   - ORB High = Highest price, ORB Low = Lowest price", PhaseColors.ORB)
        self._phase_log("   - BULLISH breakout: Price > ORB High", PhaseColors.ORB)
        self._phase_log("   - BEARISH breakout: Price < ORB Low", PhaseColors.ORB)

        # IV/IVP Explanation
        self._phase_log("", PhaseColors.SIGNAL)
        self._phase_log("[2. IV & IVP - IMPLIED VOLATILITY]", PhaseColors.SIGNAL)
        self._phase_log("   - ATM IV: Average of Call/Put IV at nearest strike", PhaseColors.SIGNAL)
        self._phase_log("   - IVP: Percentile rank vs last N days", PhaseColors.SIGNAL)
        self._phase_log(f"   - VOL_1 (LOW): IVP < {getattr(config, 'ivp_1_threshold', 33)}%", PhaseColors.SIGNAL)
        self._phase_log(f"   - VOL_2 (MID): {getattr(config, 'ivp_1_threshold', 33)}% <= IVP < {getattr(config, 'ivp_2_threshold', 66)}%", PhaseColors.SIGNAL)
        self._phase_log(f"   - VOL_3 (HIGH): IVP >= {getattr(config, 'ivp_2_threshold', 66)}%", PhaseColors.SIGNAL)

        # Trend Regime Explanation
        self._phase_log("", PhaseColors.SIGNAL)
        self._phase_log("[3. TREND REGIME - MOVING AVERAGES]", PhaseColors.SIGNAL)
        self._phase_log("   - Uses MA50 and MA100 for trend detection", PhaseColors.SIGNAL)
        self._phase_log("   - UPTREND: MA50 > MA100 AND Price > MA50", PhaseColors.SIGNAL)
        self._phase_log("   - DOWNTREND: MA50 < MA100 AND Price < MA50", PhaseColors.SIGNAL)
        self._phase_log("   - SIDEWAYS: No clear trend direction", PhaseColors.SIGNAL)

        # Strategy Selection Explanation
        self._phase_log("", PhaseColors.STRATEGY)
        self._phase_log("[4. STRATEGY SELECTION]", PhaseColors.STRATEGY)
        self._phase_log("   - DIRECTION: BULLISH → PUT spreads, BEARISH → CALL spreads", PhaseColors.STRATEGY)
        self._phase_log("   - VOL_1 (LOW IVP): Debit spreads (buy options)", PhaseColors.STRATEGY)
        self._phase_log("   - VOL_3 (HIGH IVP): Credit spreads (sell options)", PhaseColors.STRATEGY)
        self._phase_log("   - Lookup: strategy_mapping.csv maps conditions → strategy", PhaseColors.STRATEGY)

        # Delta & Strike Selection
        self._phase_log("", PhaseColors.STRATEGY)
        self._phase_log("[5. DELTA & STRIKE SELECTION]", PhaseColors.STRATEGY)
        self._phase_log("   - Delta = probability option expires ITM", PhaseColors.STRATEGY)
        self._phase_log("   - Find strike closest to target delta from template", PhaseColors.STRATEGY)
        self._phase_log("   - CREDIT: SELL higher delta, BUY lower delta", PhaseColors.STRATEGY)
        self._phase_log("   - DEBIT: BUY higher delta, SELL lower delta", PhaseColors.STRATEGY)

        # Target & Stop Calculation
        self._phase_log("", PhaseColors.ENTRY)
        self._phase_log("[6. TARGET & STOP CALCULATION]", PhaseColors.ENTRY)
        self._phase_log(f"   - CREDIT: Target={getattr(config, 'credit_target_factor', 0.70)*100:.0f}% of credit, Stop={(1+getattr(config, 'credit_stop_factor', 0.30))*100:.0f}%", PhaseColors.ENTRY)
        self._phase_log(f"   - DEBIT: Target={getattr(config, 'debit_target_factor', 1.5)*100:.0f}% of entry, Stop={getattr(config, 'debit_stop_factor', 0.5)*100:.0f}%", PhaseColors.ENTRY)

        # Position Sizing
        self._phase_log("", PhaseColors.ENTRY)
        self._phase_log("[7. POSITION SIZING]", PhaseColors.ENTRY)
        self._phase_log(f"   - Risk per trade: ${getattr(config, 'risk_per_trade', 500):.0f}", PhaseColors.ENTRY)
        self._phase_log(f"   - Max capital: ${getattr(config, 'max_cap_to_be_used', 5000):.0f}", PhaseColors.ENTRY)
        self._phase_log(f"   - Max contracts: {getattr(config, 'max_contracts', 10)}", PhaseColors.ENTRY)
        self._phase_log("   - Contracts = MIN(risk_limit, capital_limit, max_contracts)", PhaseColors.ENTRY)

        self._phase_log("", PhaseColors.STARTUP)
        self._phase_log("=" * 70, PhaseColors.STARTUP)
        self._phase_log("", PhaseColors.STARTUP)

    def log_config_loaded(self, config_name: str, item_count: int):
        """Log configuration file loaded - BLUE"""
        self._phase_log(f"Loaded {config_name}: {item_count} items", PhaseColors.STARTUP)

    def log_connection_status(self, status: str, details: str = ""):
        """Log IBKR connection status - CYAN"""
        self._phase_log(f"IBKR Connection: {status} {details}", PhaseColors.CONNECTION)

    def log_session_start(self):
        """Log trading session start - GREEN"""
        self._phase_log("", PhaseColors.ENTRY)
        self._phase_log("", PhaseColors.ENTRY)
        self._phase_log("*" * 70, PhaseColors.ENTRY)
        self._phase_log("***   SESSION STARTED - BEGIN TRADING   ***", PhaseColors.ENTRY)
        self._phase_log("*" * 70, PhaseColors.ENTRY)
        self._phase_log("", PhaseColors.ENTRY)

    def log_session_end(self):
        """Log trading session end - YELLOW"""
        self._phase_log("", PhaseColors.SHUTDOWN)
        self._phase_log("", PhaseColors.SHUTDOWN)
        self._phase_log("*" * 70, PhaseColors.SHUTDOWN)
        self._phase_log("***   SESSION ENDED   ***", PhaseColors.SHUTDOWN)
        self._phase_log("*" * 70, PhaseColors.SHUTDOWN)
        self._phase_log("", PhaseColors.SHUTDOWN)

    def log_orb_calculated(self, or_high: float, or_low: float, orb_window_minutes: int = 30):
        """Log opening range calculation - YELLOW"""
        # ORB explanation is now shown at startup via log_all_explanations()
        self._phase_log(f"Opening Range Calculated - High: {or_high:.2f}, Low: {or_low:.2f}", PhaseColors.ORB)

    def log_breakout(self, direction: str, price: float, breach_time: datetime):
        """Log ORB breakout - MAGENTA"""
        self._phase_log(
            f"BREAKOUT DETECTED - Direction: {direction}, "
            f"Price: {price:.2f}, Time: {breach_time.strftime('%H:%M:%S')}",
            PhaseColors.BREAKOUT
        )

    def log_signal(self, signal_data: Dict[str, Any]):
        """Log signal evaluation to CSV with simplified format (14 columns)."""
        if not self.log_signals:
            return

        # Helper to round numeric values
        def round_val(val, decimals=2):
            if val is None or val == "":
                return ""
            try:
                return round(float(val), decimals)
            except (ValueError, TypeError):
                return val

        # Build combined legs string: "P6900S|P6875B"
        legs = signal_data.get("legs", [])
        legs_str = "|".join([
            f"{leg.get('type', '')}{int(leg.get('strike', 0))}{leg.get('side', '')[0]}"
            for leg in legs if leg.get('type') and leg.get('strike')
        ])

        row = [
            datetime.now(US_EASTERN).strftime("%Y-%m-%d %H:%M:%S"),
            round_val(signal_data.get("underlying_price", ""), 2),
            round_val(signal_data.get("or_high", ""), 2),
            round_val(signal_data.get("or_low", ""), 2),
            round_val(signal_data.get("breakout_price", ""), 2),
            signal_data.get("direction_bias", ""),
            round_val(signal_data.get("ivp", ""), 1),
            signal_data.get("trend_regime", ""),
            signal_data.get("strategy_selected", ""),
            legs_str,
            round_val(signal_data.get("entry_spread", ""), 2),
            "YES" if signal_data.get("signal_ok") else "NO",
            "YES" if signal_data.get("executed") else "NO",
            signal_data.get("reason", "")
        ]

        # Create file with headers on first write (lazy initialization)
        if not self.signal_log_file.exists():
            with open(self.signal_log_file, "w", newline="", encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(self.signal_log_headers)

        # Issue 14: Use UTF-8 encoding for append operations
        with open(self.signal_log_file, "a", newline="", encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(row)

    def log_trade_entry(self, trade_data: Dict[str, Any]):
        """Log trade entry - GREEN"""
        self._phase_log("=" * 40, PhaseColors.ENTRY)
        self._phase_log(f"TRADE ENTRY - Strategy: {trade_data.get('strategy_id')}", PhaseColors.ENTRY)
        self._phase_log(f"Direction: {trade_data.get('direction_bias')}", PhaseColors.ENTRY)
        self._phase_log(f"Vol Regime: {trade_data.get('vol_regime')}", PhaseColors.ENTRY)
        self._phase_log(f"Contracts: {trade_data.get('contracts')}", PhaseColors.ENTRY)
        self._phase_log(f"Entry Spread: {trade_data.get('entry_spread'):.4f}", PhaseColors.ENTRY)
        self._phase_log(f"Target: {trade_data.get('target_spread'):.4f}", PhaseColors.ENTRY)
        self._phase_log(f"Stop: {trade_data.get('stop_spread'):.4f}", PhaseColors.ENTRY)

        # Log legs
        for i, leg in enumerate(trade_data.get("legs", []), 1):
            self._phase_log(
                f"Leg {i}: {leg['type']} {leg['strike']} {leg['side']} "
                f"(Delta target: {leg['delta_target']:.2f}, "
                f"actual: {leg['delta_actual']:.2f})",
                PhaseColors.ENTRY
            )
        self._phase_log("=" * 40, PhaseColors.ENTRY)

    def log_trade_exit(self, trade_data: Dict[str, Any]):
        """Write trade exit to CSV (screen logging handled by log_trade_closed_simple)"""
        # Write to trade log CSV only - screen output handled by log_trade_closed_simple
        if self.log_trades:
            self._write_trade_to_csv(trade_data)

    def _round_value(self, value, decimals: int = 2):
        """Round a numeric value to specified decimal places, handling None/empty."""
        if value is None or value == "":
            return ""
        try:
            return round(float(value), decimals)
        except (ValueError, TypeError):
            return value

    def _write_trade_to_csv(self, trade_data: Dict[str, Any]):
        """Write completed trade to CSV file with simplified format (18 columns)."""
        legs = trade_data.get("legs", [])

        # Calculate duration if entry and exit times are available
        duration_min = ""
        entry_time_str = trade_data.get("entry_time", "")
        exit_time_str = trade_data.get("exit_time", "")
        entry_time_only = ""
        exit_time_only = ""
        trade_date = ""

        if entry_time_str and exit_time_str:
            try:
                entry_dt = datetime.strptime(entry_time_str, "%Y-%m-%d %H:%M:%S")
                exit_dt = datetime.strptime(exit_time_str, "%Y-%m-%d %H:%M:%S")
                duration_min = int((exit_dt - entry_dt).total_seconds() / 60)
                entry_time_only = entry_dt.strftime("%H:%M:%S")
                exit_time_only = exit_dt.strftime("%H:%M:%S")
                trade_date = exit_dt.strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                pass

        if not trade_date:
            trade_date = datetime.now(US_EASTERN).strftime("%Y-%m-%d")

        # Build combined legs string: "P6900S|P6875B"
        legs_str = "|".join([
            f"{leg.get('type', '')}{int(leg.get('strike', 0))}{leg.get('side', '')[0]}"
            for leg in legs if leg.get('type') and leg.get('strike')
        ])

        row = [
            trade_date,
            entry_time_only,
            exit_time_only,
            trade_data.get("position_id", ""),
            trade_data.get("strategy_id", ""),
            trade_data.get("direction_bias", ""),
            trade_data.get("expiry", ""),
            legs_str,
            trade_data.get("contracts", ""),
            self._round_value(trade_data.get("entry_spread", ""), 2),
            self._round_value(trade_data.get("exit_spread", ""), 2),
            self._round_value(trade_data.get("target_spread", ""), 2),
            self._round_value(trade_data.get("stop_spread", ""), 2),
            trade_data.get("exit_reason", ""),
            self._round_value(trade_data.get("total_pnl", ""), 2),
            duration_min,
            self._round_value(trade_data.get("underlying_price_entry", ""), 2),
            self._round_value(trade_data.get("underlying_price_exit", ""), 2)
        ]

        # Create file with headers on first write (lazy initialization)
        if not self.trade_log_file.exists():
            with open(self.trade_log_file, "w", newline="", encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(self.trade_log_headers)

        # Issue 14: Use UTF-8 encoding for append operations
        with open(self.trade_log_file, "a", newline="", encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(row)

    def log_position_monitor(
        self,
        current_spread: float,
        target: float,
        stop: float,
        underlying_price: float
    ):
        """Log position monitoring checkpoint"""
        self.debug(
            f"Position Monitor - Spread: {current_spread:.4f}, "
            f"Target: {target:.4f}, Stop: {stop:.4f}, "
            f"Underlying: {underlying_price:.2f}"
        )

    def log_error_with_traceback(self, message: str, exc: Exception):
        """Log error with full traceback"""
        import traceback
        self.error(f"{message}: {str(exc)}")
        self.error(traceback.format_exc())

    def log_order_submitted(self, order_id: int, details: str):
        """Log order submission"""
        self.info(f"Order Submitted - ID: {order_id}, {details}")

    def log_order_filled(self, order_id: int, fill_price: float, quantity: int):
        """Log order fill"""
        self.info(
            f"Order Filled - ID: {order_id}, "
            f"Price: {fill_price:.4f}, Qty: {quantity}"
        )

    def log_order_status(self, order_id: int, status: str, filled: int, remaining: int):
        """Log order status update"""
        self.debug(
            f"Order Status - ID: {order_id}, Status: {status}, "
            f"Filled: {filled}, Remaining: {remaining}"
        )

    # ========== Trader-Friendly Detailed Logging Methods ==========

    def log_state_transition(self, from_state: str, to_state: str, reason: str = ""):
        """Log engine state transition with clear formatting - MAGENTA"""
        self._phase_log("=" * 50, PhaseColors.BREAKOUT)
        self._phase_log(f"[STATE CHANGE] {from_state} -> {to_state}", PhaseColors.BREAKOUT)
        if reason:
            self._phase_log(f"  Reason: {reason}", PhaseColors.BREAKOUT)
        self._phase_log("=" * 50, PhaseColors.BREAKOUT)

    def log_waiting_status(self, target_time: str, current_time: str, description: str = ""):
        """Log waiting status for session start - GRAY"""
        self._phase_log(f"[WAITING] Current: {current_time} | Target: {target_time}", PhaseColors.WAITING)
        if description:
            self._phase_log(f"   {description}", PhaseColors.WAITING)

    def log_market_data_update(self, symbol: str, price: float, change: float = None):
        """Log market data price update - WHITE"""
        if change is not None:
            direction = "UP" if change >= 0 else "DOWN"
            self._phase_log(f"[PRICE] {symbol}: ${price:.2f} ({direction} {abs(change):.2f})", PhaseColors.POSITION)
        else:
            self._phase_log(f"[PRICE] {symbol}: ${price:.2f}", PhaseColors.POSITION)

    def log_orb_status(self, phase: str, or_high: float = None, or_low: float = None,
                       current_price: float = None, time_remaining: str = None,
                       bar_count: int = None):
        """Log ORB phase status with detailed information - YELLOW"""
        self._phase_log("-" * 50, PhaseColors.ORB)
        self._phase_log(f"[ORB STATUS] {phase}", PhaseColors.ORB)
        if bar_count is not None:
            self._phase_log(f"   Bars Collected: {bar_count}", PhaseColors.ORB)
        if or_high is not None and or_low is not None:
            range_width = or_high - or_low
            midpoint = (or_high + or_low) / 2
            self._phase_log(f"   Range: High={or_high:.2f} | Low={or_low:.2f}", PhaseColors.ORB)
            self._phase_log(f"   Width: {range_width:.2f} points | Midpoint: {midpoint:.2f}", PhaseColors.ORB)
        if current_price is not None:
            if or_high and or_low:
                if current_price > or_high:
                    self._phase_log(f"   Current: ${current_price:.2f} (ABOVE range by {current_price - or_high:.2f})", PhaseColors.ORB)
                elif current_price < or_low:
                    self._phase_log(f"   Current: ${current_price:.2f} (BELOW range by {or_low - current_price:.2f})", PhaseColors.ORB)
                else:
                    self._phase_log(f"   Current: ${current_price:.2f} (WITHIN range)", PhaseColors.ORB)
            else:
                self._phase_log(f"   Current Price: ${current_price:.2f}", PhaseColors.ORB)
        if time_remaining:
            self._phase_log(f"   Time Remaining: {time_remaining}", PhaseColors.ORB)
        self._phase_log("-" * 50, PhaseColors.ORB)

    def log_breakout_details(self, direction: str, breach_price: float, or_high: float,
                             or_low: float, breach_time: datetime):
        """Log detailed breakout information - MAGENTA BOLD"""
        # Format breach time in both ET and IST
        if breach_time.tzinfo is None:
            # Assume naive datetime is in ET
            breach_time_et = breach_time.replace(tzinfo=US_EASTERN)
        else:
            breach_time_et = breach_time.astimezone(US_EASTERN)
        breach_time_ist = breach_time_et.astimezone(IST)

        et_str = breach_time_et.strftime('%H:%M:%S')
        ist_str = breach_time_ist.strftime('%H:%M:%S')

        self._phase_log("=" * 60, PhaseColors.BREAKOUT)
        self._phase_log(f"*** BREAKOUT DETECTED! ***", PhaseColors.BREAKOUT)
        self._phase_log(f"   Direction: {direction}", PhaseColors.BREAKOUT)
        self._phase_log(f"   Breach Price: ${breach_price:.2f}", PhaseColors.BREAKOUT)
        self._phase_log(f"   Breach Time: {et_str} ET / {ist_str} IST", PhaseColors.BREAKOUT)
        self._phase_log(f"   ORB Range: High=${or_high:.2f} | Low=${or_low:.2f}", PhaseColors.BREAKOUT)
        if direction == "BULLISH":
            self._phase_log(f"   Breakout Margin: +{breach_price - or_high:.2f} above high", PhaseColors.BREAKOUT)
        else:
            self._phase_log(f"   Breakout Margin: -{or_low - breach_price:.2f} below low", PhaseColors.BREAKOUT)
        self._phase_log("=" * 60, PhaseColors.BREAKOUT)

    def log_signal_evaluation(self, signal_data: dict):
        """Log detailed signal evaluation results - CYAN"""
        # Trend explanation is now shown at startup via log_all_explanations()
        self._phase_log("-" * 60, PhaseColors.SIGNAL)
        self._phase_log("[SIGNAL EVALUATION]", PhaseColors.SIGNAL)
        self._phase_log(f"   Direction Bias: {signal_data.get('direction_bias', 'N/A')}", PhaseColors.SIGNAL)
        self._phase_log(f"   Trend Regime: {signal_data.get('trend_regime', 'N/A')}", PhaseColors.SIGNAL)
        self._phase_log(f"   Vol Regime: {signal_data.get('vol_regime', 'N/A')}", PhaseColors.SIGNAL)
        self._phase_log(f"   Underlying Price: ${signal_data.get('underlying_price', 0):.2f}", PhaseColors.SIGNAL)

        if signal_data.get('or_high') and signal_data.get('or_low'):
            self._phase_log(f"   ORB Range: {signal_data.get('or_high'):.2f} - {signal_data.get('or_low'):.2f}", PhaseColors.SIGNAL)

        if signal_data.get('ma_short') and signal_data.get('ma_long'):
            self._phase_log(f"   Moving Averages: MA50={signal_data.get('ma_short'):.2f} | MA100={signal_data.get('ma_long'):.2f}", PhaseColors.SIGNAL)

        if signal_data.get('iv_atm'):
            self._phase_log(f"   ATM IV: {signal_data.get('iv_atm')*100:.2f}%", PhaseColors.SIGNAL)

        if signal_data.get('ivp') is not None:
            self._phase_log(f"   IVP: {signal_data.get('ivp'):.1f}%", PhaseColors.SIGNAL)

        signal_ok = signal_data.get('signal_ok', False)
        self._phase_log(f"   Signal Valid: {'YES' if signal_ok else 'NO'}", PhaseColors.SIGNAL)
        self._phase_log(f"   Reason: {signal_data.get('reason', 'N/A')}", PhaseColors.SIGNAL)
        self._phase_log("-" * 60, PhaseColors.SIGNAL)

    def log_strategy_selection(self, strategy_id: str, direction: str, vol_regime: str,
                               trend_regime: str, is_valid: bool, reason: str = ""):
        """Log strategy selection details - BLUE"""
        # Strategy selection explanation is now shown at startup via log_all_explanations()
        self._phase_log("-" * 60, PhaseColors.STRATEGY)
        self._phase_log("[STRATEGY SELECTION]", PhaseColors.STRATEGY)
        self._phase_log(f"   Conditions: {direction} | {vol_regime} | {trend_regime}", PhaseColors.STRATEGY)
        self._phase_log(f"   Selected Strategy: {strategy_id}", PhaseColors.STRATEGY)
        self._phase_log(f"   Valid: {'YES' if is_valid else 'NO'}", PhaseColors.STRATEGY)
        if reason:
            self._phase_log(f"   Reason: {reason}", PhaseColors.STRATEGY)
        self._phase_log("-" * 60, PhaseColors.STRATEGY)

    def log_strategy_build_details(self, strategy_id: str, legs: list, entry_spread: float,
                                   is_credit: bool, underlying_price: float):
        """Log detailed strategy build information - BLUE"""
        # Delta/Strike explanation is now shown at startup via log_all_explanations()
        self._phase_log("=" * 60, PhaseColors.STRATEGY)
        self._phase_log(f"[STRATEGY BUILT] {strategy_id}", PhaseColors.STRATEGY)
        self._phase_log(f"   Type: {'CREDIT' if is_credit else 'DEBIT'} Spread", PhaseColors.STRATEGY)
        self._phase_log(f"   Underlying: ${underlying_price:.2f}", PhaseColors.STRATEGY)
        self._phase_log(f"   Entry Spread: ${entry_spread:.4f}", PhaseColors.STRATEGY)
        self._phase_log(f"   Legs ({len(legs)}):", PhaseColors.STRATEGY)

        for i, leg in enumerate(legs, 1):
            delta_str = f"{leg.get('delta_actual', leg.get('actual_delta', 0)):.3f}" if leg.get('delta_actual') or leg.get('actual_delta') else "N/A"
            self._phase_log(f"      Leg {i}: {leg.get('option_type', leg.get('type', 'N/A'))} "
                     f"Strike={leg.get('strike', 'N/A')} "
                     f"Side={leg.get('side', 'N/A')} "
                     f"(Target Delta={leg.get('delta_target', leg.get('target_delta', 0)):.2f}, "
                     f"Actual Delta={delta_str})", PhaseColors.STRATEGY)
        self._phase_log("=" * 60, PhaseColors.STRATEGY)

    def log_trade_execution_summary(self, strategy_id: str, legs: list,
                                    entry_spread: float, fill_spread: float,
                                    contracts: int, is_credit: bool):
        """Log clean trade execution summary with entry vs fill prices and slippage"""
        self._phase_log("=" * 60, PhaseColors.ENTRY)
        self._phase_log(f"[TRADE EXECUTED] {strategy_id}", PhaseColors.ENTRY)
        self._phase_log(f"   Contracts: {contracts} | Type: {'CREDIT' if is_credit else 'DEBIT'}", PhaseColors.ENTRY)
        self._phase_log("", PhaseColors.ENTRY)
        self._phase_log("   LEGS:", PhaseColors.ENTRY)

        for leg in legs:
            leg_type = leg.get('option_type', 'N/A')
            strike = leg.get('strike', 0)
            side = leg.get('side', 'N/A')
            entry_price = leg.get('entry_price', 0)
            fill_price = leg.get('fill_price', 0)
            slippage = fill_price - entry_price if side == 'BUY' else entry_price - fill_price

            self._phase_log(f"      {leg_type} {strike} {side}:", PhaseColors.ENTRY)
            self._phase_log(f"         Entry: ${entry_price:.2f} | Fill: ${fill_price:.2f} | Slippage: ${slippage:.2f}", PhaseColors.ENTRY)

        self._phase_log("", PhaseColors.ENTRY)
        self._phase_log("   SPREAD:", PhaseColors.ENTRY)
        spread_slippage = abs(fill_spread) - abs(entry_spread) if is_credit else fill_spread - entry_spread
        self._phase_log(f"      Entry: ${entry_spread:.2f} | Fill: ${fill_spread:.2f} | Slippage: ${spread_slippage:.2f}", PhaseColors.ENTRY)
        self._phase_log("=" * 60, PhaseColors.ENTRY)

    def log_position_created(self, position_id: str, strategy_id: str, contracts: int,
                            entry_spread: float, target_spread: float, stop_spread: float,
                            is_credit: bool, direction: str, vol_regime: str):
        """Log detailed position creation - GREEN"""
        self._phase_log("=" * 60, PhaseColors.ENTRY)
        self._phase_log(f"*** POSITION OPENED: {position_id} ***", PhaseColors.ENTRY)
        self._phase_log(f"   Strategy: {strategy_id}", PhaseColors.ENTRY)
        self._phase_log(f"   Direction: {direction} | Vol Regime: {vol_regime}", PhaseColors.ENTRY)
        self._phase_log(f"   Type: {'CREDIT' if is_credit else 'DEBIT'}", PhaseColors.ENTRY)
        self._phase_log(f"   Contracts: {contracts}", PhaseColors.ENTRY)
        self._phase_log(f"   Entry Spread: ${entry_spread:.4f}", PhaseColors.ENTRY)
        target_pct = abs(target_spread - entry_spread) / abs(entry_spread) * 100 if entry_spread != 0 else 0
        stop_pct = abs(stop_spread - entry_spread) / abs(entry_spread) * 100 if entry_spread != 0 else 0
        self._phase_log(f"   Target Spread: ${target_spread:.4f} ({target_pct:.1f}% {'profit' if is_credit else 'gain'})", PhaseColors.ENTRY)
        self._phase_log(f"   Stop Spread: ${stop_spread:.4f} ({stop_pct:.1f}% loss)", PhaseColors.ENTRY)
        self._phase_log("=" * 60, PhaseColors.ENTRY)

    def log_trade_opened_simple(self, legs: list, contracts: int, is_credit: bool,
                                 entry_spread: float, target_price: float, stop_price: float):
        """
        Simple trader-friendly trade log.
        Shows: legs with prices, entry, target, stop - that's it.
        """
        # Determine spread type from legs
        option_types = [leg.get('option_type', 'P') for leg in legs]
        spread_type = "PUT" if 'P' in option_types else "CALL"
        credit_debit = "CREDIT" if is_credit else "DEBIT"

        self._phase_log("=" * 50, PhaseColors.ENTRY)
        self._phase_log(f"TRADE OPENED - {spread_type} {credit_debit} SPREAD", PhaseColors.ENTRY)
        self._phase_log("=" * 50, PhaseColors.ENTRY)

        # Log each leg simply
        for leg in legs:
            opt_type = leg.get('option_type', 'P')
            strike = leg.get('strike', 0)
            side = leg.get('side', 'BUY')
            price = leg.get('fill_price', leg.get('entry_price', 0))
            self._phase_log(f"   {side:4} {opt_type} {strike:.0f} @ ${price:.2f}", PhaseColors.ENTRY)

        self._phase_log("   " + "-" * 30, PhaseColors.ENTRY)
        self._phase_log(f"   Contracts: {contracts}", PhaseColors.ENTRY)

        if is_credit:
            self._phase_log(f"   Entry: ${abs(entry_spread):.2f} credit received", PhaseColors.ENTRY)
            self._phase_log(f"   Target: ${target_price:.2f} (buy back at 50%)", PhaseColors.ENTRY)
            self._phase_log(f"   Stop: ${stop_price:.2f}", PhaseColors.ENTRY)
        else:
            self._phase_log(f"   Entry: ${abs(entry_spread):.2f} debit paid", PhaseColors.ENTRY)
            self._phase_log(f"   Target: ${abs(target_price):.2f} (sell at 150%)", PhaseColors.ENTRY)
            self._phase_log(f"   Stop: ${abs(stop_price):.2f}", PhaseColors.ENTRY)

        self._phase_log("=" * 50, PhaseColors.ENTRY)

        # P&L Calculation explanation
        self._phase_log("-" * 50, PhaseColors.ENTRY)
        self._phase_log("[P&L CALCULATION]", PhaseColors.ENTRY)
        if is_credit:
            self._phase_log(f"   Type: CREDIT SPREAD", PhaseColors.ENTRY)
            self._phase_log(f"   Entry Spread: {entry_spread:.2f} (negative = credit received)", PhaseColors.ENTRY)
            self._phase_log(f"   Formula: (Entry - Current) x Contracts x 100", PhaseColors.ENTRY)
            self._phase_log(f"   Profit when: Current < Entry (cheaper to buy back)", PhaseColors.ENTRY)
            self._phase_log(f"   Loss when: Current > Entry (more expensive to buy back)", PhaseColors.ENTRY)
        else:
            self._phase_log(f"   Type: DEBIT SPREAD", PhaseColors.ENTRY)
            self._phase_log(f"   Entry Spread: {entry_spread:.2f} (positive = debit paid)", PhaseColors.ENTRY)
            self._phase_log(f"   Formula: (Current - Entry) x Contracts x 100", PhaseColors.ENTRY)
            self._phase_log(f"   Profit when: Current > Entry (can sell for more)", PhaseColors.ENTRY)
            self._phase_log(f"   Loss when: Current < Entry (worth less)", PhaseColors.ENTRY)
        self._phase_log(f"   Contracts: {contracts}", PhaseColors.ENTRY)
        self._phase_log("-" * 50, PhaseColors.ENTRY)

    def log_trade_closed_simple(self, exit_reason: str, entry_spread: float,
                                 exit_spread: float, realized_pnl: float, contracts: int,
                                 target_spread: float = None, stop_spread: float = None,
                                 entry_time: str = None, exit_time: str = None,
                                 duration_minutes: int = None):
        """Simple trader-friendly exit log with slippage detection and duration."""
        color = PhaseColors.WIN if realized_pnl >= 0 else PhaseColors.LOSS
        result = "PROFIT" if realized_pnl >= 0 else "LOSS"

        self._phase_log("=" * 50, color)
        self._phase_log(f"TRADE CLOSED - {exit_reason.upper()}", color)
        self._phase_log("=" * 50, color)
        self._phase_log(f"   Entry: ${abs(entry_spread):.2f} | Exit: ${abs(exit_spread):.2f}", color)
        self._phase_log(f"   Contracts: {contracts}", color)
        self._phase_log(f"   P&L: ${realized_pnl:+.2f} {result}", color)

        # Show duration if available
        if duration_minutes is not None and entry_time and exit_time:
            self._phase_log(f"   Duration: {duration_minutes} min ({entry_time} -> {exit_time})", color)
        elif duration_minutes is not None:
            self._phase_log(f"   Duration: {duration_minutes} min", color)

        # Show slippage if significant (> $0.50 beyond target/stop)
        if exit_reason.upper() in ['TARGET', 'STOP'] and exit_spread:
            if exit_reason.upper() == 'TARGET' and target_spread is not None:
                expected = abs(target_spread)
                actual = abs(exit_spread)
                slippage = abs(actual - expected)
                if slippage > 0.50:
                    pct = (slippage / expected * 100) if expected > 0 else 0
                    self._phase_log(f"   ⚠️ Slippage: ${slippage:.2f} ({pct:.1f}% beyond target)", PhaseColors.WARNING)
            elif exit_reason.upper() == 'STOP' and stop_spread is not None:
                expected = abs(stop_spread)
                actual = abs(exit_spread)
                slippage = abs(actual - expected)
                if slippage > 0.50:
                    pct = (slippage / expected * 100) if expected > 0 else 0
                    self._phase_log(f"   ⚠️ Slippage: ${slippage:.2f} ({pct:.1f}% beyond stop limit)", PhaseColors.WARNING)

        self._phase_log("=" * 50, color)

    def log_position_update(self, position_id: str, current_spread: float, entry_spread: float,
                           target_spread: float, stop_spread: float, unrealized_pnl: float,
                           underlying_price: float, is_credit: bool):
        """Log position monitoring update - WHITE"""
        # Calculate progress (with division-by-zero protection)
        if is_credit:
            denominator = entry_spread - target_spread
            target_progress = (entry_spread - current_spread) / denominator * 100 if denominator != 0 else 0
        else:
            denominator = target_spread - entry_spread
            target_progress = (current_spread - entry_spread) / denominator * 100 if denominator != 0 else 0

        target_progress = max(0, min(100, target_progress))

        self._phase_log("-" * 50, PhaseColors.POSITION)
        self._phase_log(f"[POSITION UPDATE] {position_id}", PhaseColors.POSITION)
        self._phase_log(f"   Current Spread: ${current_spread:.4f}", PhaseColors.POSITION)
        self._phase_log(f"   Entry: ${entry_spread:.4f} | Target: ${target_spread:.4f} | Stop: ${stop_spread:.4f}", PhaseColors.POSITION)
        pnl_status = "PROFIT" if unrealized_pnl >= 0 else "LOSS"
        self._phase_log(f"   Unrealized P&L: ${unrealized_pnl:.2f} ({pnl_status})", PhaseColors.POSITION)
        self._phase_log(f"   Progress to Target: {target_progress:.1f}%", PhaseColors.POSITION)
        self._phase_log(f"   Underlying: ${underlying_price:.2f}", PhaseColors.POSITION)

        # Visual progress bar
        bar_length = 20
        filled = int(bar_length * target_progress / 100)
        bar = "#" * filled + "-" * (bar_length - filled)
        self._phase_log(f"   [{bar}] {target_progress:.1f}%", PhaseColors.POSITION)
        self._phase_log("-" * 50, PhaseColors.POSITION)

    def log_position_closed(self, position_id: str, exit_reason: str, entry_spread: float,
                           exit_spread: float, realized_pnl: float, contracts: int,
                           entry_time: str, exit_time: str, duration_minutes: int = None):
        """Log detailed position closure - WIN/LOSS color based on PnL"""
        pnl_per_contract = realized_pnl / contracts if contracts > 0 else 0
        pnl_status = "WIN" if realized_pnl >= 0 else "LOSS"
        color = PhaseColors.WIN if realized_pnl >= 0 else PhaseColors.LOSS

        self._phase_log("=" * 60, color)
        self._phase_log(f"*** POSITION CLOSED: {position_id} ({pnl_status}) ***", color)
        self._phase_log(f"   Exit Reason: {exit_reason}", color)
        self._phase_log(f"   Entry Spread: ${entry_spread:.4f}", color)
        self._phase_log(f"   Exit Spread: ${exit_spread:.4f}", color)
        self._phase_log(f"   Entry Time: {entry_time}", color)
        self._phase_log(f"   Exit Time: {exit_time}", color)
        if duration_minutes:
            self._phase_log(f"   Duration: {duration_minutes} minutes", color)
        self._phase_log("-" * 30, color)
        self._phase_log(f"   P&L per Contract: ${pnl_per_contract:.2f}", color)
        self._phase_log(f"   Total P&L: ${realized_pnl:.2f}", color)
        self._phase_log("=" * 60, color)

    def log_monitoring_cycle(self, cycle_num: int, current_time: str, next_check: str,
                            has_position: bool, underlying_price: float = None):
        """Log monitoring cycle status"""
        self.debug(f"[MONITOR] Cycle #{cycle_num} at {current_time}")
        self.debug(f"   Next check: {next_check}")
        self.debug(f"   Position open: {'Yes' if has_position else 'No'}")
        if underlying_price:
            self.debug(f"   Underlying: ${underlying_price:.2f}")

    def log_daily_summary_detailed(self, summary: dict):
        """Log detailed daily trading summary - WIN/LOSS color based on total PnL"""
        total_pnl = summary.get('total_pnl', 0)
        color = PhaseColors.WIN if total_pnl >= 0 else PhaseColors.LOSS

        self._phase_log("=" * 60, color)
        self._phase_log("*** DAILY TRADING SUMMARY ***", color)
        self._phase_log("=" * 60, color)
        self._phase_log(f"   Total Trades: {summary.get('total_trades', 0)}", color)
        self._phase_log(f"   Winning Trades: {summary.get('winning_trades', 0)}", color)
        self._phase_log(f"   Losing Trades: {summary.get('losing_trades', 0)}", color)

        total_trades = summary.get('total_trades', 0)
        if total_trades > 0:
            win_rate = summary.get('winning_trades', 0) / total_trades * 100
            self._phase_log(f"   Win Rate: {win_rate:.1f}%", color)

        self._phase_log("-" * 30, color)
        pnl_status = "PROFIT" if total_pnl >= 0 else "LOSS"
        self._phase_log(f"   Total P&L: ${total_pnl:.2f} ({pnl_status})", color)
        self._phase_log(f"   Average Win: ${summary.get('avg_win', 0):.2f}", color)
        self._phase_log(f"   Average Loss: ${summary.get('avg_loss', 0):.2f}", color)
        self._phase_log(f"   Largest Win: ${summary.get('largest_win', 0):.2f}", color)
        self._phase_log(f"   Largest Loss: ${summary.get('largest_loss', 0):.2f}", color)
        self._phase_log("=" * 60, color)

    def get_daily_summary_from_csv(self) -> dict:
        """
        Read all trades from today's CSV file and calculate daily summary.
        This works across engine restarts since all trades go to same file.
        """
        summary = {
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "total_pnl": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "largest_win": 0.0,
            "largest_loss": 0.0,
            "win_rate": 0.0
        }

        if not hasattr(self, 'trade_log_file') or not self.trade_log_file.exists():
            return summary

        try:
            wins = []
            losses = []

            with open(self.trade_log_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        pnl = float(row.get('pnl', 0) or 0)
                        summary["total_trades"] += 1
                        summary["total_pnl"] += pnl

                        if pnl > 0:
                            wins.append(pnl)
                        elif pnl < 0:
                            losses.append(pnl)
                    except (ValueError, TypeError):
                        continue

            summary["winning_trades"] = len(wins)
            summary["losing_trades"] = len(losses)

            if wins:
                summary["avg_win"] = sum(wins) / len(wins)
                summary["largest_win"] = max(wins)

            if losses:
                summary["avg_loss"] = sum(losses) / len(losses)
                summary["largest_loss"] = min(losses)

            if summary["total_trades"] > 0:
                summary["win_rate"] = (len(wins) / summary["total_trades"]) * 100

        except Exception as e:
            self.warning(f"Error reading trade CSV for daily summary: {e}")

        return summary

    def log_error_detailed(self, error_type: str, error_msg: str, context: dict = None):
        """Log detailed error with context - RED"""
        self._phase_log("!" * 60, PhaseColors.ERROR)
        self._phase_log(f"ERROR: {error_type}", PhaseColors.ERROR)
        self._phase_log(f"   Message: {error_msg}", PhaseColors.ERROR)
        if context:
            self._phase_log("   Context:", PhaseColors.ERROR)
            for key, value in context.items():
                self._phase_log(f"      {key}: {value}", PhaseColors.ERROR)
        self._phase_log("!" * 60, PhaseColors.ERROR)

    def log_cutoff_warning(self, cutoff_time: str, current_time: str, minutes_remaining: int):
        """Log time cutoff warning - YELLOW"""
        self._phase_log(f"TIME CUTOFF WARNING", PhaseColors.EXIT)
        self._phase_log(f"   Current Time: {current_time}", PhaseColors.EXIT)
        self._phase_log(f"   Cutoff Time: {cutoff_time}", PhaseColors.EXIT)
        self._phase_log(f"   Minutes Remaining: {minutes_remaining}", PhaseColors.EXIT)

    def log_no_trade_reason(self, reason: str, direction: str = None, vol_regime: str = None,
                           trend_regime: str = None):
        """Log reason for not taking a trade - GRAY (waiting state)"""
        self._phase_log("-" * 50, PhaseColors.WAITING)
        self._phase_log(f"[NO TRADE]", PhaseColors.WAITING)
        self._phase_log(f"   Reason: {reason}", PhaseColors.WAITING)
        if direction:
            self._phase_log(f"   Direction: {direction}", PhaseColors.WAITING)
        if vol_regime:
            self._phase_log(f"   Vol Regime: {vol_regime}", PhaseColors.WAITING)
        if trend_regime:
            self._phase_log(f"   Trend Regime: {trend_regime}", PhaseColors.WAITING)
        self._phase_log("-" * 50, PhaseColors.WAITING)

    def log_health_check(self, check_name: str, passed: bool, message: str):
        """Log health check result - CYAN with PASS/FAIL highlighting"""
        status = "PASS" if passed else "FAIL"
        status_color = PhaseColors.WIN if passed else PhaseColors.LOSS
        self._phase_log(f"  [{self._colored(status, status_color)}] {check_name}: {message}", PhaseColors.HEALTH)

    # ========== PERIODIC STATUS LOGGING ==========

    def log_heartbeat(self, cycle: int, state: str, underlying_price: float = None,
                      has_position: bool = False, position_pnl: float = None):
        """Log periodic heartbeat - shows trader what's happening each cycle"""
        now = datetime.now(US_EASTERN).strftime("%H:%M:%S")

        # Build status line
        status_parts = [f"[{now}]", f"Cycle #{cycle}", f"State: {state}"]

        if underlying_price:
            status_parts.append(f"SPX: ${underlying_price:.2f}")

        if has_position:
            pnl_str = f"${position_pnl:.2f}" if position_pnl else "N/A"
            pnl_color = PhaseColors.WIN if (position_pnl and position_pnl >= 0) else PhaseColors.LOSS
            status_parts.append(f"PnL: {self._colored(pnl_str, pnl_color)}")

        self._phase_log(" | ".join(status_parts), PhaseColors.POSITION)

    def log_minute_status(self, minute: int, state: str, underlying_price: float,
                          or_high: float = None, or_low: float = None,
                          has_position: bool = False, unrealized_pnl: float = None):
        """Log status every minute - comprehensive view for trader"""
        now = datetime.now(US_EASTERN)
        self._phase_log("─" * 70, PhaseColors.POSITION)
        self._phase_log(f"[MINUTE {minute}] {now.strftime('%H:%M:%S')} | State: {state}", PhaseColors.POSITION)
        self._phase_log(f"   SPX Price: ${underlying_price:.2f}", PhaseColors.POSITION)

        if or_high and or_low:
            range_pos = "ABOVE" if underlying_price > or_high else "BELOW" if underlying_price < or_low else "WITHIN"
            self._phase_log(f"   ORB Range: ${or_low:.2f} - ${or_high:.2f} ({range_pos})", PhaseColors.ORB)

        if has_position:
            pnl_status = "PROFIT" if unrealized_pnl >= 0 else "LOSS"
            color = PhaseColors.WIN if unrealized_pnl >= 0 else PhaseColors.LOSS
            self._phase_log(f"   Position: OPEN | Unrealized: {self._colored(f'${unrealized_pnl:.2f} ({pnl_status})', color)}", color)
        else:
            self._phase_log(f"   Position: NONE", PhaseColors.WAITING)

        self._phase_log("─" * 70, PhaseColors.POSITION)

    # ========== DATA FETCH LOGGING ==========

    def log_price_bars_fetched(self, bars: list, symbol: str = "SPX", show_count: int = 5, timezone_offset_hours: int = 1):
        """Log fetched price bars - shows last N bars with ET and IST times

        Args:
            bars: List of OHLC bars
            symbol: Symbol name
            show_count: Number of bars to display
            timezone_offset_hours: Hours to add to convert IBKR time to ET (default 1 for CT→ET)
        """
        if not bars:
            self._phase_log(f"[DATA] No price bars received for {symbol}", PhaseColors.ERROR)
            return

        total = len(bars)
        self._phase_log(f"[DATA] Fetched {total} price bars for {symbol}", PhaseColors.CONNECTION)
        self._phase_log(f"   Showing last {min(show_count, total)} bars:", PhaseColors.CONNECTION)
        self._phase_log(f"   {'Time(ET)':<10} {'Time(IST)':<10} {'Open':>10} {'High':>10} {'Low':>10} {'Close':>10}", PhaseColors.CONNECTION)
        self._phase_log(f"   {'-'*10} {'-'*10} {'-'*10} {'-'*10} {'-'*10} {'-'*10}", PhaseColors.CONNECTION)

        for bar in bars[-show_count:]:
            if hasattr(bar, 'timestamp') and bar.timestamp:
                # Convert IBKR time (CT) to ET by adding offset
                et_time = bar.timestamp + timedelta(hours=timezone_offset_hours)
                et_str = et_time.strftime("%H:%M:%S")
                # Convert ET to IST (add 10:30 hours)
                ist_time = et_time + timedelta(hours=10, minutes=30)
                ist_str = ist_time.strftime("%H:%M:%S")
            else:
                et_str = "N/A"
                ist_str = "N/A"
            open_p = getattr(bar, 'open', 0)
            high_p = getattr(bar, 'high', 0)
            low_p = getattr(bar, 'low', 0)
            close_p = getattr(bar, 'close', 0)
            self._phase_log(f"   {et_str:<10} {ist_str:<10} {open_p:>10.2f} {high_p:>10.2f} {low_p:>10.2f} {close_p:>10.2f}", PhaseColors.CONNECTION)

    def log_option_chain_fetched(self, chain: list, expiry: str, underlying_price: float, show_count: int = 5):
        """Log fetched option chain - shows N closest strikes"""
        if not chain:
            self._phase_log(f"[DATA] No option chain received for expiry {expiry}", PhaseColors.ERROR)
            return

        total = len(chain)
        self._phase_log(f"[DATA] Fetched option chain: {total} contracts for {expiry}", PhaseColors.CONNECTION)
        self._phase_log(f"   Underlying: ${underlying_price:.2f} | Showing {min(show_count * 2, total)} contracts near ATM:", PhaseColors.CONNECTION)

        # Header
        self._phase_log(f"   {'Strike':>8} {'Type':>6} {'Bid':>8} {'Ask':>8} {'Mid':>8} {'Delta':>8} {'IV':>8}", PhaseColors.CONNECTION)
        self._phase_log(f"   {'-'*8} {'-'*6} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*8}", PhaseColors.CONNECTION)

        # Sort by distance from underlying and show closest
        sorted_chain = sorted(chain, key=lambda x: abs(getattr(x, 'strike', underlying_price) - underlying_price))

        for opt in sorted_chain[:show_count * 2]:
            strike = getattr(opt, 'strike', 0)
            opt_type = getattr(opt, 'option_type', getattr(opt, 'right', 'N/A'))
            bid = getattr(opt, 'bid', 0)
            ask = getattr(opt, 'ask', 0)
            mid = (bid + ask) / 2 if bid and ask else 0
            delta = getattr(opt, 'delta', 0)
            iv = getattr(opt, 'implied_vol', getattr(opt, 'iv', 0))

            # Color based on option type
            color = PhaseColors.WIN if opt_type in ['C', 'CALL', 'Call'] else PhaseColors.LOSS
            self._phase_log(f"   {strike:>8.0f} {opt_type:>6} {bid:>8.2f} {ask:>8.2f} {mid:>8.2f} {delta:>8.3f} {iv*100:>7.1f}%", color)

    def log_greeks_fetched(self, option_data: dict, strike: float, option_type: str):
        """Log fetched Greeks for a specific option"""
        self._phase_log(f"[GREEKS] {option_type} Strike ${strike:.0f}:", PhaseColors.SIGNAL)
        self._phase_log(f"   Delta: {option_data.get('delta', 0):.4f}", PhaseColors.SIGNAL)
        self._phase_log(f"   Gamma: {option_data.get('gamma', 0):.6f}", PhaseColors.SIGNAL)
        self._phase_log(f"   Theta: {option_data.get('theta', 0):.4f}", PhaseColors.SIGNAL)
        self._phase_log(f"   Vega:  {option_data.get('vega', 0):.4f}", PhaseColors.SIGNAL)
        self._phase_log(f"   IV:    {option_data.get('iv', 0)*100:.2f}%", PhaseColors.SIGNAL)

    def log_iv_data_fetched(self, iv_data: dict, symbol: str = "SPX"):
        """Log fetched IV data"""
        self._phase_log(f"[IV DATA] {symbol} Volatility Metrics:", PhaseColors.SIGNAL)
        self._phase_log(f"   ATM IV:      {iv_data.get('atm_iv', 0)*100:.2f}%", PhaseColors.SIGNAL)
        self._phase_log(f"   IV Rank:     {iv_data.get('iv_rank', 0):.1f}%", PhaseColors.SIGNAL)
        self._phase_log(f"   IVP:         {iv_data.get('ivp', 0):.1f}%", PhaseColors.SIGNAL)
        self._phase_log(f"   HV 20-day:   {iv_data.get('hv_20', 0)*100:.2f}%", PhaseColors.SIGNAL)
        self._phase_log(f"   IV/HV Ratio: {iv_data.get('iv_hv_ratio', 0):.2f}", PhaseColors.SIGNAL)

    # ========== STRATEGY-SPECIFIC LOGGING ==========

    def log_strategy_legs_detail(self, strategy_id: str, legs: list, underlying_price: float):
        """Log detailed strategy legs with all relevant data"""
        self._phase_log(f"[STRATEGY LEGS] {strategy_id} @ SPX ${underlying_price:.2f}", PhaseColors.STRATEGY)
        self._phase_log(f"   {'Leg':<4} {'Type':<6} {'Strike':>8} {'Side':<5} {'Delta':>8} {'Price':>8} {'Qty':>4}", PhaseColors.STRATEGY)
        self._phase_log(f"   {'-'*4} {'-'*6} {'-'*8} {'-'*5} {'-'*8} {'-'*8} {'-'*4}", PhaseColors.STRATEGY)

        total_delta = 0
        total_credit = 0

        for i, leg in enumerate(legs, 1):
            opt_type = leg.get('option_type', leg.get('type', 'N/A'))
            strike = leg.get('strike', 0)
            side = leg.get('side', 'N/A')
            delta = leg.get('delta_actual', leg.get('delta', 0))
            price = leg.get('price', leg.get('mid', 0))
            qty = leg.get('quantity', 1)

            # Adjust delta sign based on side
            if side in ['SELL', 'SHORT', 'S']:
                total_delta -= abs(delta) * qty
                total_credit += price * qty
            else:
                total_delta += abs(delta) * qty
                total_credit -= price * qty

            color = PhaseColors.WIN if side in ['SELL', 'SHORT', 'S'] else PhaseColors.LOSS
            self._phase_log(f"   {i:<4} {opt_type:<6} {strike:>8.0f} {side:<5} {delta:>8.3f} {price:>8.2f} {qty:>4}", color)

        self._phase_log(f"   {'-'*50}", PhaseColors.STRATEGY)
        credit_type = "CREDIT" if total_credit > 0 else "DEBIT"
        self._phase_log(f"   Net Delta: {total_delta:.3f} | Net {credit_type}: ${abs(total_credit):.2f}", PhaseColors.STRATEGY)

    def log_spread_pricing(self, strategy_id: str, entry_spread: float, target_spread: float,
                           stop_spread: float, is_credit: bool, contracts: int):
        """Log spread pricing and risk/reward"""
        max_profit = abs(entry_spread - target_spread) * contracts * 100
        max_loss = abs(stop_spread - entry_spread) * contracts * 100
        rr_ratio = max_profit / max_loss if max_loss > 0 else 0

        spread_type = "CREDIT" if is_credit else "DEBIT"
        self._phase_log(f"[SPREAD PRICING] {strategy_id} ({spread_type})", PhaseColors.STRATEGY)
        self._phase_log(f"   Entry Spread:  ${entry_spread:.4f}", PhaseColors.STRATEGY)
        self._phase_log(f"   Target Spread: ${target_spread:.4f}", PhaseColors.STRATEGY)
        self._phase_log(f"   Stop Spread:   ${stop_spread:.4f}", PhaseColors.STRATEGY)
        self._phase_log(f"   Contracts:     {contracts}", PhaseColors.STRATEGY)
        self._phase_log(f"   ─────────────────────────", PhaseColors.STRATEGY)
        self._phase_log(f"   Max Profit: {self._colored(f'${max_profit:.2f}', PhaseColors.WIN)}", PhaseColors.STRATEGY)
        self._phase_log(f"   Max Loss:   {self._colored(f'${max_loss:.2f}', PhaseColors.LOSS)}", PhaseColors.STRATEGY)
        self._phase_log(f"   R:R Ratio:  {rr_ratio:.2f}:1", PhaseColors.STRATEGY)

    def log_delta_selection(self, leg_name: str, target_delta: float, candidates: list, selected: dict):
        """Log delta selection process - shows candidates and chosen strike"""
        self._phase_log(f"[DELTA SELECT] {leg_name} - Target Delta: {target_delta:.2f}", PhaseColors.SIGNAL)

        if candidates:
            self._phase_log(f"   Top {min(3, len(candidates))} candidates:", PhaseColors.SIGNAL)
            for i, cand in enumerate(candidates[:3], 1):
                strike = cand.get('strike', 0)
                delta = cand.get('delta', 0)
                diff = abs(delta - target_delta)
                self._phase_log(f"   {i}. Strike ${strike:.0f} | Delta {delta:.3f} | Diff {diff:.3f}", PhaseColors.SIGNAL)

        if selected:
            self._phase_log(f"   ► SELECTED: Strike ${selected.get('strike', 0):.0f} | Delta {selected.get('delta', 0):.3f}", PhaseColors.ENTRY)

    # ========== MARKET REGIME LOGGING ==========

    def log_market_regime(self, direction: str, vol_regime: str, trend_regime: str,
                          ivp: float = None, ma_short: float = None, ma_long: float = None,
                          underlying_price: float = None):
        """Log current market regime analysis"""
        self._phase_log("╔" + "═" * 50 + "╗", PhaseColors.SIGNAL)
        self._phase_log("║" + " MARKET REGIME ANALYSIS ".center(50) + "║", PhaseColors.SIGNAL)
        self._phase_log("╠" + "═" * 50 + "╣", PhaseColors.SIGNAL)

        # Direction with color
        dir_color = PhaseColors.WIN if direction == "BULLISH" else PhaseColors.LOSS if direction == "BEARISH" else PhaseColors.WAITING
        self._phase_log(f"║  Direction:   {self._colored(direction, dir_color):<40}║", PhaseColors.SIGNAL)

        # Vol regime with color
        vol_color = PhaseColors.LOSS if "HIGH" in vol_regime else PhaseColors.WIN if "LOW" in vol_regime else PhaseColors.WAITING
        self._phase_log(f"║  Vol Regime:  {self._colored(vol_regime, vol_color):<40}║", PhaseColors.SIGNAL)

        # Trend with color
        trend_color = PhaseColors.WIN if "UP" in trend_regime else PhaseColors.LOSS if "DOWN" in trend_regime else PhaseColors.WAITING
        self._phase_log(f"║  Trend:       {self._colored(trend_regime, trend_color):<40}║", PhaseColors.SIGNAL)

        self._phase_log("╠" + "═" * 50 + "╣", PhaseColors.SIGNAL)

        if ivp is not None:
            self._phase_log(f"║  IVP:         {ivp:.1f}%{' ':42}║", PhaseColors.SIGNAL)
        if underlying_price and ma_short and ma_long:
            above_ma = "Above" if underlying_price > ma_short else "Below"
            self._phase_log(f"║  Price vs MA: {above_ma} MA50 (${ma_short:.2f}){' ':21}║", PhaseColors.SIGNAL)
            self._phase_log(f"║  MA50/MA100:  ${ma_short:.2f} / ${ma_long:.2f}{' ':20}║", PhaseColors.SIGNAL)

        self._phase_log("╚" + "═" * 50 + "╝", PhaseColors.SIGNAL)

    # ========== EXECUTION LOGGING ==========

    def log_order_execution(self, order_type: str, legs: list, status: str,
                            fill_price: float = None, slippage: float = None):
        """Log order execution with all details"""
        status_color = PhaseColors.WIN if status == "FILLED" else PhaseColors.LOSS if status in ["REJECTED", "CANCELLED"] else PhaseColors.WAITING

        self._phase_log(f"[ORDER] {order_type} - Status: {self._colored(status, status_color)}", PhaseColors.ENTRY)

        for i, leg in enumerate(legs, 1):
            action = leg.get('action', 'N/A')
            strike = leg.get('strike', 0)
            opt_type = leg.get('option_type', 'N/A')
            qty = leg.get('quantity', 1)
            price = leg.get('price', 0)
            self._phase_log(f"   Leg {i}: {action} {qty}x {opt_type} ${strike:.0f} @ ${price:.2f}", PhaseColors.ENTRY)

        if fill_price is not None:
            self._phase_log(f"   Fill Price: ${fill_price:.4f}", PhaseColors.ENTRY)
        if slippage is not None:
            slip_color = PhaseColors.WIN if slippage <= 0 else PhaseColors.LOSS
            self._phase_log(f"   Slippage: {self._colored(f'${slippage:.4f}', slip_color)}", PhaseColors.ENTRY)

    def log_execution_summary(self, strategy_id: str, entry_time: str, entry_price: float,
                              contracts: int, direction: str, vol_regime: str):
        """Log execution summary after trade is opened"""
        self._phase_log("┌" + "─" * 50 + "┐", PhaseColors.ENTRY)
        self._phase_log("│" + " TRADE EXECUTED ".center(50) + "│", PhaseColors.ENTRY)
        self._phase_log("├" + "─" * 50 + "┤", PhaseColors.ENTRY)
        self._phase_log(f"│  Strategy:  {strategy_id:<38}│", PhaseColors.ENTRY)
        self._phase_log(f"│  Time:      {entry_time:<38}│", PhaseColors.ENTRY)
        self._phase_log(f"│  Direction: {direction:<38}│", PhaseColors.ENTRY)
        self._phase_log(f"│  Regime:    {vol_regime:<38}│", PhaseColors.ENTRY)
        self._phase_log(f"│  Contracts: {contracts:<38}│", PhaseColors.ENTRY)
        self._phase_log(f"│  Entry:     ${entry_price:.4f}{' ':31}│", PhaseColors.ENTRY)
        self._phase_log("└" + "─" * 50 + "┘", PhaseColors.ENTRY)
