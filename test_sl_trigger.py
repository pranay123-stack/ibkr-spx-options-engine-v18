#!/usr/bin/env python3
"""
Standalone test script to:
1. Place a credit spread order
2. Place SL order after fill
3. Monitor if SL triggers properly

Usage: python3 test_sl_trigger.py

NOTE: Run during market hours (9:30 AM - 4:00 PM ET)
"""
import sys
import time
from datetime import datetime
from ib_insync import IB, Contract, ComboLeg, Order, TagValue

# Configuration
HOST = '127.0.0.1'
PORT = 7497  # 7497=Paper, 7496=Live
CLIENT_ID = 88

# SL Configuration
SL_TRIGGER_PCT = 1.5  # 150% of entry = 50% loss for credit spread
SL_LIMIT_OFFSET = 0.10  # $0.10 above trigger


def round_to_tick(price: float, tick: float = 0.05) -> float:
    """Round price to SPX tick size"""
    return round(round(price / tick) * tick, 2)


def create_put_credit_spread(ib: IB, short_strike: float, long_strike: float, expiry: str):
    """Create put credit spread contracts"""

    # Short put (sell higher strike)
    short_put = Contract()
    short_put.symbol = 'SPX'
    short_put.secType = 'OPT'
    short_put.exchange = 'SMART'
    short_put.currency = 'USD'
    short_put.lastTradeDateOrContractMonth = expiry
    short_put.strike = short_strike
    short_put.right = 'P'
    short_put.multiplier = '100'
    short_put.tradingClass = 'SPXW'  # Weekly options

    # Long put (buy lower strike)
    long_put = Contract()
    long_put.symbol = 'SPX'
    long_put.secType = 'OPT'
    long_put.exchange = 'SMART'
    long_put.currency = 'USD'
    long_put.lastTradeDateOrContractMonth = expiry
    long_put.strike = long_strike
    long_put.right = 'P'
    long_put.multiplier = '100'
    long_put.tradingClass = 'SPXW'  # Weekly options

    # Qualify contracts
    ib.qualifyContracts(short_put)
    ib.qualifyContracts(long_put)

    print(f"  Short put: {short_put.localSymbol} (conId={short_put.conId})")
    print(f"  Long put: {long_put.localSymbol} (conId={long_put.conId})")

    return short_put, long_put


def create_combo_contract(short_contract: Contract, long_contract: Contract):
    """Create BAG combo contract for spread"""
    combo = Contract()
    combo.symbol = 'SPX'
    combo.secType = 'BAG'
    combo.exchange = 'SMART'
    combo.currency = 'USD'

    # Leg 1: SELL short put
    leg1 = ComboLeg()
    leg1.conId = short_contract.conId
    leg1.ratio = 1
    leg1.action = 'SELL'
    leg1.exchange = 'SMART'

    # Leg 2: BUY long put
    leg2 = ComboLeg()
    leg2.conId = long_contract.conId
    leg2.ratio = 1
    leg2.action = 'BUY'
    leg2.exchange = 'SMART'

    combo.comboLegs = [leg1, leg2]

    return combo


def create_exit_combo_contract(short_contract: Contract, long_contract: Contract):
    """Create BAG combo contract for closing spread (reversed actions)"""
    combo = Contract()
    combo.symbol = 'SPX'
    combo.secType = 'BAG'
    combo.exchange = 'SMART'
    combo.currency = 'USD'

    # Leg 1: BUY to close short put
    leg1 = ComboLeg()
    leg1.conId = short_contract.conId
    leg1.ratio = 1
    leg1.action = 'BUY'
    leg1.exchange = 'SMART'

    # Leg 2: SELL to close long put
    leg2 = ComboLeg()
    leg2.conId = long_contract.conId
    leg2.ratio = 1
    leg2.action = 'SELL'
    leg2.exchange = 'SMART'

    combo.comboLegs = [leg1, leg2]

    return combo


def get_spread_market_price(ib: IB, short_contract: Contract, long_contract: Contract) -> float:
    """Get current spread market price (mid)"""
    # Request tickers
    short_ticker = ib.reqMktData(short_contract, '', False, False)
    long_ticker = ib.reqMktData(long_contract, '', False, False)
    ib.sleep(2)

    # Get mid prices
    short_bid = short_ticker.bid if short_ticker.bid and short_ticker.bid > 0 else 0
    short_ask = short_ticker.ask if short_ticker.ask and short_ticker.ask > 0 else 0
    short_mid = (short_bid + short_ask) / 2 if short_bid > 0 and short_ask > 0 else 0

    long_bid = long_ticker.bid if long_ticker.bid and long_ticker.bid > 0 else 0
    long_ask = long_ticker.ask if long_ticker.ask and long_ticker.ask > 0 else 0
    long_mid = (long_bid + long_ask) / 2 if long_bid > 0 and long_ask > 0 else 0

    # Cancel market data
    ib.cancelMktData(short_contract)
    ib.cancelMktData(long_contract)

    # Credit spread value = short - long (positive for credit)
    spread_value = short_mid - long_mid

    return spread_value


def check_market_hours():
    """Check if US market is open"""
    from datetime import datetime
    import pytz

    et = pytz.timezone('America/New_York')
    now_et = datetime.now(et)

    # Market hours: 9:30 AM - 4:00 PM ET, Monday-Friday
    if now_et.weekday() >= 5:  # Saturday or Sunday
        return False, f"Weekend - market closed. Current ET time: {now_et.strftime('%H:%M')}"

    market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)

    if now_et < market_open:
        return False, f"Pre-market - opens at 9:30 AM ET. Current ET time: {now_et.strftime('%H:%M')}"
    elif now_et > market_close:
        return False, f"After-hours - closed at 4:00 PM ET. Current ET time: {now_et.strftime('%H:%M')}"

    return True, f"Market open. Current ET time: {now_et.strftime('%H:%M')}"


def main():
    ib = IB()

    try:
        print(f"{'='*60}")
        print("SL TRIGGER TEST SCRIPT")
        print(f"{'='*60}")

        # Check market hours
        try:
            market_open, msg = check_market_hours()
            print(f"Market status: {msg}")
            if not market_open:
                print("\nWARNING: Market is closed. Orders may be rejected.")
                print("Run this script during market hours (9:30 AM - 4:00 PM ET)")
                response = input("\nContinue anyway? (y/n): ").strip().lower()
                if response != 'y':
                    print("Exiting.")
                    return
        except ImportError:
            print("Note: Install pytz to check market hours: pip install pytz")

        print(f"\nConnecting to IBKR at {HOST}:{PORT}...")
        ib.connect(HOST, PORT, clientId=CLIENT_ID)
        print("Connected!\n")

        # Get SPX price
        spx = Contract()
        spx.symbol = 'SPX'
        spx.secType = 'IND'
        spx.exchange = 'CBOE'
        spx.currency = 'USD'
        ib.qualifyContracts(spx)

        ticker = ib.reqMktData(spx, '', False, False)
        ib.sleep(2)
        spx_price = ticker.last if ticker.last and ticker.last > 0 else ticker.close
        ib.cancelMktData(spx)

        print(f"SPX Price: ${spx_price:.2f}\n")

        # Calculate strikes - put spread ~25 points below current price
        short_strike = round(spx_price - 25, 0)
        short_strike = round(short_strike / 5) * 5  # Round to nearest 5
        long_strike = short_strike - 25  # 25-wide spread

        # Get today's 0DTE expiry
        expiry = datetime.now().strftime('%Y%m%d')

        print(f"{'='*60}")
        print("CREATING PUT CREDIT SPREAD")
        print(f"{'='*60}")
        print(f"  Expiry: {expiry}")
        print(f"  Short strike: {short_strike}")
        print(f"  Long strike: {long_strike}")
        print(f"  Width: ${short_strike - long_strike}")
        print()

        # Create spread contracts
        short_put, long_put = create_put_credit_spread(ib, short_strike, long_strike, expiry)

        # Get current spread value
        print(f"\n{'='*60}")
        print("GETTING MARKET PRICES")
        print(f"{'='*60}")
        spread_value = get_spread_market_price(ib, short_put, long_put)

        print(f"  Spread value: ${spread_value:.2f}")

        if spread_value <= 0:
            print("\nERROR: Could not get valid spread price. Market may be closed.")
            return

        # Create entry combo
        entry_combo = create_combo_contract(short_put, long_put)

        # Calculate limit price (slightly worse than mid for fill)
        entry_limit = round_to_tick(spread_value * 0.90)  # Accept 10% less credit for faster fill

        print(f"\n{'='*60}")
        print("PLACING ENTRY ORDER")
        print(f"{'='*60}")
        print(f"  Action: SELL (credit spread)")
        print(f"  Quantity: 1")
        print(f"  Limit: ${entry_limit:.2f} credit")

        # Create entry order
        entry_order = Order()
        entry_order.action = 'SELL'
        entry_order.totalQuantity = 1
        entry_order.orderType = 'LMT'
        entry_order.lmtPrice = entry_limit
        entry_order.tif = 'DAY'
        entry_order.smartComboRoutingParams = [TagValue('NonGuaranteed', '1')]

        # Place entry order
        entry_trade = ib.placeOrder(entry_combo, entry_order)
        print(f"  Order ID: {entry_trade.order.orderId}")

        # Wait for fill
        print("\nWaiting for entry fill...")
        timeout = 60
        start_time = time.time()

        while time.time() - start_time < timeout:
            ib.sleep(1)
            status = entry_trade.orderStatus.status
            print(f"  Status: {status}          ", end='\r')

            if status == 'Filled':
                break
            elif status in ['Cancelled', 'Inactive']:
                print(f"\n\nOrder {status}!")
                # Show error if available
                if entry_trade.log:
                    for log_entry in entry_trade.log:
                        if log_entry.errorCode:
                            print(f"  Error {log_entry.errorCode}: {log_entry.message}")
                print("\nThis usually happens when:")
                print("  1. Market is closed or near close")
                print("  2. The spread is too far OTM")
                print("  3. Insufficient margin/buying power")
                return

        if entry_trade.orderStatus.status != 'Filled':
            print(f"\n\nEntry order not filled after {timeout}s. Cancelling...")
            ib.cancelOrder(entry_trade.order)
            return

        fill_price = abs(entry_trade.orderStatus.avgFillPrice)
        print(f"\n\nENTRY FILLED @ ${fill_price:.2f} credit")

        # Calculate SL prices
        sl_trigger = round_to_tick(fill_price * SL_TRIGGER_PCT)
        sl_limit = round_to_tick(sl_trigger + SL_LIMIT_OFFSET)

        print(f"\n{'='*60}")
        print("PLACING SL ORDER")
        print(f"{'='*60}")
        print(f"  Entry: ${fill_price:.2f}")
        print(f"  SL Trigger ({SL_TRIGGER_PCT*100:.0f}%): ${sl_trigger:.2f}")
        print(f"  SL Limit (+${SL_LIMIT_OFFSET}): ${sl_limit:.2f}")

        # Create exit combo (reversed actions)
        exit_combo = create_exit_combo_contract(short_put, long_put)

        # Create SL order (Stop-Limit)
        sl_order = Order()
        sl_order.action = 'BUY'  # Buy to close
        sl_order.totalQuantity = 1
        sl_order.orderType = 'STP LMT'
        sl_order.auxPrice = sl_trigger  # Stop trigger price
        sl_order.lmtPrice = sl_limit    # Limit price after trigger
        sl_order.tif = 'DAY'
        sl_order.smartComboRoutingParams = [TagValue('NonGuaranteed', '1')]

        # Place SL order
        sl_trade = ib.placeOrder(exit_combo, sl_order)
        print(f"  SL Order ID: {sl_trade.order.orderId}")

        ib.sleep(2)
        sl_status = sl_trade.orderStatus.status
        print(f"  SL Status: {sl_status}")

        if sl_status in ['Cancelled', 'Inactive']:
            print("\nERROR: SL order was rejected!")
            if sl_trade.log:
                for log_entry in sl_trade.log:
                    if log_entry.errorCode:
                        print(f"  Error {log_entry.errorCode}: {log_entry.message}")

            # Close position manually
            print("\nClosing position with market order...")
            close_order = Order()
            close_order.action = 'BUY'
            close_order.totalQuantity = 1
            close_order.orderType = 'MKT'
            close_order.tif = 'DAY'
            close_order.smartComboRoutingParams = [TagValue('NonGuaranteed', '1')]

            close_trade = ib.placeOrder(exit_combo, close_order)
            ib.sleep(3)
            print(f"Close order status: {close_trade.orderStatus.status}")
            return

        # Monitor position
        print(f"\n{'='*60}")
        print("MONITORING POSITION (Press Ctrl+C to exit)")
        print(f"{'='*60}")
        print(f"  Entry: ${fill_price:.2f}")
        print(f"  SL Trigger: ${sl_trigger:.2f}")
        print()

        while True:
            try:
                # Check SL order status
                sl_status = sl_trade.orderStatus.status

                if sl_status == 'Filled':
                    sl_fill = abs(sl_trade.orderStatus.avgFillPrice)
                    loss = (sl_fill - fill_price) * 100
                    print(f"\n{'='*60}")
                    print("SL TRIGGERED AND FILLED!")
                    print(f"{'='*60}")
                    print(f"  Entry: ${fill_price:.2f}")
                    print(f"  Exit: ${sl_fill:.2f}")
                    print(f"  Loss: ${loss:.2f}")
                    break

                elif sl_status in ['Cancelled', 'Inactive']:
                    print(f"\nWARNING: SL order {sl_status}!")
                    if sl_trade.log:
                        for log_entry in sl_trade.log:
                            if log_entry.message:
                                print(f"  Log: {log_entry.message}")
                    break

                # Get current spread price
                current_spread = get_spread_market_price(ib, short_put, long_put)
                if current_spread > 0:
                    pnl = (fill_price - current_spread) * 100  # Credit spread P&L

                    timestamp = datetime.now().strftime('%H:%M:%S')
                    print(f"[{timestamp}] Spread: ${current_spread:.2f} | P&L: ${pnl:+.2f} | SL: {sl_status}")

                ib.sleep(5)

            except KeyboardInterrupt:
                print("\n\nInterrupted by user")

                # Cancel SL and close position
                print("Cancelling SL order...")
                ib.cancelOrder(sl_trade.order)
                ib.sleep(1)

                print("Closing position with market order...")
                close_order = Order()
                close_order.action = 'BUY'
                close_order.totalQuantity = 1
                close_order.orderType = 'MKT'
                close_order.tif = 'DAY'
                close_order.smartComboRoutingParams = [TagValue('NonGuaranteed', '1')]

                close_trade = ib.placeOrder(exit_combo, close_order)
                ib.sleep(3)
                print(f"Close order status: {close_trade.orderStatus.status}")
                break

    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if ib.isConnected():
            ib.disconnect()
        print("\nDisconnected from IBKR")


if __name__ == "__main__":
    main()
