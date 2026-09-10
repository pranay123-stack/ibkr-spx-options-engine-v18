#!/usr/bin/env python3
"""
Standalone script to close all SPX option positions via IBKR
Uses market orders for quick execution
"""
import sys
from ib_insync import IB, MarketOrder

def main():
    ib = IB()

    # Configuration
    HOST = '127.0.0.1'
    PORT = 7497  # 7497=Paper, 7496=Live
    CLIENT_ID = 99  # Use different client ID than main engine

    try:
        print(f"Connecting to IBKR at {HOST}:{PORT}...")
        ib.connect(HOST, PORT, clientId=CLIENT_ID)
        print("Connected!")

        # Get all positions
        positions = ib.positions()
        spx_positions = [p for p in positions if p.contract.symbol == 'SPX']

        if not spx_positions:
            print("\nNo SPX positions found. Nothing to close.")
            return

        print(f"\n{'='*60}")
        print(f"Found {len(spx_positions)} SPX position(s):")
        print(f"{'='*60}")
        for pos in spx_positions:
            side = "LONG" if pos.position > 0 else "SHORT"
            print(f"  {pos.contract.localSymbol}: {side} {abs(pos.position)} @ avg ${pos.avgCost:.2f}")

        # Cancel all open orders first
        print(f"\n{'='*60}")
        print("Cancelling all open orders...")
        open_orders = ib.openOrders()
        for order in open_orders:
            try:
                ib.cancelOrder(order)
            except Exception as e:
                print(f"  Warning cancelling order: {e}")
        ib.sleep(2)
        print(f"Cancelled {len(open_orders)} order(s)")

        # Close each position with market order
        print(f"\n{'='*60}")
        print("Closing positions with MARKET orders...")
        print(f"{'='*60}")

        trades = []
        for pos in spx_positions:
            contract = pos.contract
            qty = abs(int(pos.position))

            # Qualify the contract
            ib.qualifyContracts(contract)

            # Opposite action to close
            action = 'SELL' if pos.position > 0 else 'BUY'

            print(f"\n  Closing: {contract.localSymbol}")
            print(f"    Action: {action} {qty}")

            order = MarketOrder(action, qty)
            order.tif = 'DAY'
            trade = ib.placeOrder(contract, order)
            trades.append((contract.localSymbol, trade))
            ib.sleep(1)

        # Wait for fills
        print(f"\n{'='*60}")
        print("Waiting for fills...")
        ib.sleep(5)

        # Check order statuses
        print(f"\n{'='*60}")
        print("Order Results:")
        print(f"{'='*60}")
        for symbol, trade in trades:
            status = trade.orderStatus.status
            fill_price = trade.orderStatus.avgFillPrice if trade.orderStatus.avgFillPrice else 0
            print(f"  {symbol}: {status}", end="")
            if status == 'Filled':
                print(f" @ ${fill_price:.2f}")
            else:
                print()

        # Verify final positions
        ib.sleep(2)
        final_positions = [p for p in ib.positions() if p.contract.symbol == 'SPX']

        print(f"\n{'='*60}")
        if not final_positions:
            print("SUCCESS: All SPX positions closed!")
        else:
            print(f"WARNING: {len(final_positions)} position(s) remaining:")
            for pos in final_positions:
                print(f"  {pos.contract.localSymbol}: {pos.position}")
        print(f"{'='*60}")

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
