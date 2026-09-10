#!/usr/bin/env python3
"""
Auto-Restart Wrapper for client Options Trading Engine

Automatically restarts the engine if it crashes unexpectedly.
Uses exponential backoff to prevent rapid restart loops.

Usage:
    python run_with_autorestart.py [--config CONFIG_DIR] [--max-restarts N]

Features:
    - Automatic restart on crash (non-zero exit code)
    - Exponential backoff (5s, 10s, 20s, 40s, up to 5min max)
    - Graceful shutdown on Ctrl+C (no restart)
    - Max restart limit to prevent infinite loops
    - Restart logging to state/restart_history.log
"""

import sys
import os
import subprocess
import time
import signal
import argparse
from datetime import datetime
from pathlib import Path

# Exit codes
EXIT_SUCCESS = 0
EXIT_KEYBOARD_INTERRUPT = 130  # Standard exit code for Ctrl+C
EXIT_GRACEFUL_SHUTDOWN = 0     # Engine requested shutdown

# Restart configuration
DEFAULT_MAX_RESTARTS = 10
BASE_DELAY_SECONDS = 5
MAX_DELAY_SECONDS = 300  # 5 minutes max
RESTART_COUNT_RESET_HOURS = 1  # Reset restart count after 1 hour of successful running


class AutoRestartWrapper:
    def __init__(self, config_dir: str = "config", max_restarts: int = DEFAULT_MAX_RESTARTS):
        self.config_dir = config_dir
        self.max_restarts = max_restarts
        self.restart_count = 0
        self.last_restart_time = None
        self.last_successful_start = None
        self.should_stop = False
        self.current_process = None

        # Setup logging - use logs directory
        self.log_dir = Path("logs")
        self.log_dir.mkdir(exist_ok=True)
        self.log_file = self.log_dir / "restart_history.log"

    def log(self, message: str):
        """Log message to console and file."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_line = f"[{timestamp}] {message}"
        print(log_line)

        try:
            with open(self.log_file, 'a') as f:
                f.write(log_line + "\n")
        except Exception as e:
            print(f"Warning: Could not write to log file: {e}")

    def signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        self.log(f"Received signal {signum} - initiating graceful shutdown")
        self.should_stop = True

        # Forward signal to child process
        if self.current_process and self.current_process.poll() is None:
            self.log("Forwarding shutdown signal to engine...")
            self.current_process.send_signal(signum)

    def calculate_delay(self) -> int:
        """Calculate restart delay with exponential backoff."""
        delay = min(BASE_DELAY_SECONDS * (2 ** self.restart_count), MAX_DELAY_SECONDS)
        return delay

    def should_reset_restart_count(self) -> bool:
        """Check if we should reset restart count (engine ran successfully for a while)."""
        if self.last_successful_start is None:
            return False

        hours_running = (datetime.now() - self.last_successful_start).total_seconds() / 3600
        return hours_running >= RESTART_COUNT_RESET_HOURS

    def run_engine(self) -> int:
        """Run the engine and return exit code."""
        cmd = [
            sys.executable,
            "main.py",
            "--config", self.config_dir
        ]

        self.log(f"Starting engine: {' '.join(cmd)}")
        self.last_successful_start = datetime.now()

        try:
            self.current_process = subprocess.Popen(
                cmd,
                stdout=sys.stdout,
                stderr=sys.stderr,
                stdin=sys.stdin
            )

            # Wait for process to complete
            exit_code = self.current_process.wait()
            self.current_process = None
            return exit_code

        except Exception as e:
            self.log(f"Error running engine: {e}")
            self.current_process = None
            return 1

    def should_restart(self, exit_code: int) -> bool:
        """Determine if engine should be restarted based on exit code."""
        # Don't restart on graceful shutdown
        if self.should_stop:
            self.log("Graceful shutdown requested - not restarting")
            return False

        # Don't restart on successful exit
        if exit_code == EXIT_SUCCESS:
            self.log("Engine exited successfully (code 0) - not restarting")
            return False

        # Don't restart on keyboard interrupt
        if exit_code == EXIT_KEYBOARD_INTERRUPT:
            self.log("Engine stopped by user (Ctrl+C) - not restarting")
            return False

        # Don't restart if max restarts exceeded
        if self.restart_count >= self.max_restarts:
            self.log(f"Max restarts ({self.max_restarts}) exceeded - not restarting")
            return False

        # Restart on crash
        self.log(f"Engine crashed with exit code {exit_code} - will restart")
        return True

    def run(self):
        """Main loop - run engine with auto-restart."""
        # Setup signal handlers (SIGTERM not available on Windows)
        signal.signal(signal.SIGINT, self.signal_handler)
        if hasattr(signal, 'SIGTERM'):
            signal.signal(signal.SIGTERM, self.signal_handler)

        self.log("=" * 60)
        self.log("AUTO-RESTART WRAPPER STARTED")
        self.log(f"Config: {self.config_dir}")
        self.log(f"Max restarts: {self.max_restarts}")
        self.log("=" * 60)

        while not self.should_stop:
            # Check if restart count should be reset
            if self.should_reset_restart_count():
                self.log(f"Engine ran successfully for {RESTART_COUNT_RESET_HOURS}+ hour(s) - resetting restart count")
                self.restart_count = 0

            # Run engine
            exit_code = self.run_engine()

            # Check if we should restart
            if not self.should_restart(exit_code):
                break

            # Increment restart count and calculate delay
            self.restart_count += 1
            delay = self.calculate_delay()

            self.log("=" * 60)
            self.log(f"RESTART #{self.restart_count}/{self.max_restarts}")
            self.log(f"Waiting {delay} seconds before restart...")
            self.log("(Press Ctrl+C to cancel restart)")
            self.log("=" * 60)

            # Wait with interrupt handling
            try:
                for _ in range(delay):
                    if self.should_stop:
                        self.log("Restart cancelled by user")
                        break
                    time.sleep(1)
            except KeyboardInterrupt:
                self.log("Restart cancelled by user")
                self.should_stop = True
                break

        self.log("=" * 60)
        self.log("AUTO-RESTART WRAPPER STOPPED")
        self.log(f"Total restarts: {self.restart_count}")
        self.log("=" * 60)

        return 0 if self.restart_count == 0 else 1


def main():
    parser = argparse.ArgumentParser(
        description="Auto-restart wrapper for client Options Trading Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python run_with_autorestart.py                      # Default config, max 10 restarts
    python run_with_autorestart.py --max-restarts 5     # Limit to 5 restarts
    python run_with_autorestart.py --config myconfig    # Use custom config

Restart Behavior:
    - Restarts on crash (non-zero exit code)
    - Does NOT restart on Ctrl+C or graceful shutdown
    - Exponential backoff: 5s, 10s, 20s, 40s... up to 5min
    - Restart count resets after 1 hour of successful running
    - Logs restart history to state/restart_history.log
        """
    )
    parser.add_argument(
        "--config",
        default="config",
        help="Configuration directory path (default: config)"
    )
    parser.add_argument(
        "--max-restarts",
        type=int,
        default=DEFAULT_MAX_RESTARTS,
        help=f"Maximum number of restarts (default: {DEFAULT_MAX_RESTARTS})"
    )

    args = parser.parse_args()

    # Change to script directory
    script_dir = Path(__file__).parent
    os.chdir(script_dir)

    wrapper = AutoRestartWrapper(
        config_dir=args.config,
        max_restarts=args.max_restarts
    )

    return wrapper.run()


if __name__ == "__main__":
    sys.exit(main())
