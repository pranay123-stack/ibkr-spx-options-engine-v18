#!/usr/bin/env python3
"""
client Options Trading Engine
Main entry point for the trading system

Usage:
    python main.py [--config CONFIG_DIR]

Example:
    python main.py --config config
"""

import sys
import argparse
import signal
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.engine import TradingEngine
from src.utils.market_calendar import get_market_status, print_market_status, MarketStatus


def signal_handler(signum, frame):
    """Handle shutdown signals"""
    print("\nShutdown signal received...")
    if hasattr(signal_handler, 'engine') and signal_handler.engine:
        signal_handler.engine.stop()


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="client Options Trading Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python main.py                    # Use default config directory
    python main.py --config myconfig  # Use custom config directory
    python main.py --help             # Show this help message

Configuration Files Required:
    config/parameters.csv        - Trading parameters
    config/strategy_mapping.csv  - Strategy selection rules
    config/strategy_templates.csv - Strategy leg definitions

IBKR Requirements:
    - TWS or IB Gateway must be running
    - API connections must be enabled
    - Paper trading port: 7497, Live: 7496
        """
    )
    parser.add_argument(
        "--config",
        default="config",
        help="Configuration directory path (default: config)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate configuration without connecting to IBKR"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force run even if market is closed (for testing)"
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show market status and exit"
    )

    args = parser.parse_args()

    # Validate config directory exists
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Error: Configuration directory '{args.config}' not found")
        return 1

    # Check for required config files
    required_files = [
        "parameters.csv",
        "strategy_mapping.csv",
        "strategy_templates.csv"
    ]
    missing_files = []
    for f in required_files:
        if not (config_path / f).exists():
            missing_files.append(f)

    if missing_files:
        print("Error: Missing required configuration files:")
        for f in missing_files:
            print(f"  - {args.config}/{f}")
        return 1

    # Create and initialize engine
    engine = TradingEngine(config_dir=args.config)
    signal_handler.engine = engine

    # Set up signal handlers (SIGTERM not available on Windows)
    signal.signal(signal.SIGINT, signal_handler)
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, signal_handler)

    try:
        print("=" * 60)
        print("client OPTIONS TRADING ENGINE")
        print("=" * 60)
        print()

        # Check market status
        market_info = print_market_status()

        # If --status flag, just show status and exit
        if args.status:
            return 0

        # Check if market is closed
        if not market_info.is_open:
            if market_info.status == MarketStatus.CLOSED_HOLIDAY:
                print(f"\n*** NO TRADING TODAY ***")
                print(f"Reason: {market_info.holiday_name}")
            elif market_info.status == MarketStatus.CLOSED_WEEKEND:
                print(f"\n*** NO TRADING TODAY ***")
                print(f"Reason: Weekend")
            else:
                print(f"\n*** MARKET CURRENTLY CLOSED ***")

            if market_info.next_open_et:
                print(f"\nMarket will open:")
                print(f"  US Eastern: {market_info.next_open_et.strftime('%A, %B %d at %I:%M %p ET')}")
                print(f"  India (IST): {market_info.next_open_ist.strftime('%A, %B %d at %I:%M %p IST')}")

            if not args.force:
                print("\nExiting. Use --force to run anyway (for testing).")
                return 0
            else:
                print("\n--force flag detected. Running anyway for testing...")
                print()

        # Initialize
        print("Initializing engine...")
        if not engine.initialize():
            print("Error: Initialization failed")
            return 1

        print("Initialization successful")

        if args.dry_run:
            print("\n[DRY RUN] Configuration validated successfully")
            print("Exiting without connecting to IBKR")
            return 0

        # Connect to IBKR
        print("\nConnecting to IBKR...")
        print("Make sure TWS or IB Gateway is running with API enabled")
        print()

        if not engine.connect():
            print("Error: Failed to connect to IBKR")
            print("\nTroubleshooting:")
            print("  1. Is TWS or IB Gateway running?")
            print("  2. Is API enabled in settings?")
            print("  3. Is the port correct? (Paper: 7497, Live: 7496)")
            return 1

        print("Connected successfully")
        print()

        # Run engine
        print("Starting trading engine...")
        print("Press Ctrl+C to stop")
        print()

        engine.run()

    except KeyboardInterrupt:
        print("\nShutdown requested by user")

    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        return 1

    finally:
        print("\nEngine stopped")

    return 0


if __name__ == "__main__":
    sys.exit(main())
