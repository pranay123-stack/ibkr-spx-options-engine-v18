#!/usr/bin/env python3
"""
Quick script to close the current position via IBKR
"""

import sys
import time
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from ib_insync import IB, Contract, ComboLeg, Order
import json

def main():
    # Load state
    state_file = Path(__file__).parent / "state" / "position_state.json"
    if not state_file.exists():
        print("No position state file found!")
        return

    with open(state_file) as f:
        state = json.load(f)

    position = state.get("position")
    if not position:
        print("No position in state file!")
        return

    legs = position.get("legs", [])
    contracts = position.get("contracts", 1)
    is_credit = position.get("is_credit", True)

    print(f"Position: {position['strategy_id']}")
    print(f"Contracts: {contracts}")
    print(f"Entry spread: ${position['entry_spread']:.2f}")
    print(f"Legs: {len(legs)}")
    for leg in legs:
        print(f"  {leg['side']} {leg['option_type']} {leg['strike']} @ ${leg['entry_price']:.2f}")

    # Connect to IBKR
    ib = IB()
    print("\nConnecting to IBKR...")
    try:
        ib.connect('127.0.0.1', 7497, clientId=99)
    except Exception as e:
        print(f"Failed to connect: {e}")
        return

    print("Connected!")

    # Build combo contract for closing
    spx = Contract(symbol='SPX', secType='IND', exchange='CBOE', currency='USD')
    ib.qualifyContracts(spx)
    spx_conid = spx.conId

    combo_legs = []
    for leg in legs:
        # Create option contract
        opt = Contract(
            symbol='SPX',
            secType='OPT',
            exchange='SMART',
            currency='USD',
            lastTradeDateOrContractMonth=leg['expiry'],
            strike=leg['strike'],
            right=leg['option_type'],
            multiplier='100'
        )
        ib.qualifyContracts(opt)

        # Reverse the action to close
        close_action = 'SELL' if leg['side'] == 'BUY' else 'BUY'

        combo_leg = ComboLeg(
            conId=opt.conId,
            ratio=1,
            action=close_action,
            exchange='SMART'
        )
        combo_legs.append(combo_leg)
        print(f"  Close leg: {close_action} {leg['option_type']} {leg['strike']} (conId={opt.conId})")

    # Create combo contract
    combo = Contract(
        symbol='SPX',
        secType='BAG',
        exchange='SMART',
        currency='USD',
        comboLegs=combo_legs
    )

    # Get current market price
    print("\nGetting market price...")
    ticker = ib.reqMktData(combo, '', False, False)
    ib.sleep(2)

    bid = ticker.bid if ticker.bid and ticker.bid > 0 else None
    ask = ticker.ask if ticker.ask and ticker.ask > 0 else None

    print(f"Market: Bid={bid}, Ask={ask}")

    # For closing a credit spread, we're BUYING back
    # Use slightly aggressive price to ensure fill
    if bid and ask:
        mid = (bid + ask) / 2
        # For buying back (closing credit spread), use slightly above mid
        limit_price = round(mid + 0.10, 2)
    elif ask:
        limit_price = ask
    else:
        print("No market data available!")
        ib.cancelMktData(ticker.contract)
        ib.disconnect()
        return

    print(f"Closing at limit price: ${limit_price:.2f}")

    # Create market order to close
    order = Order(
        action='BUY',  # Buy back to close credit spread
        totalQuantity=contracts,
        orderType='LMT',
        lmtPrice=limit_price,
        tif='GTC'
    )

    print(f"\nPlacing close order: BUY {contracts} @ ${limit_price:.2f}")
    confirm = input("Confirm? (y/n): ")

    if confirm.lower() != 'y':
        print("Cancelled.")
        ib.cancelMktData(ticker.contract)
        ib.disconnect()
        return

    trade = ib.placeOrder(combo, order)
    print(f"Order placed: ID={trade.order.orderId}")

    # Wait for fill
    print("Waiting for fill...")
    for i in range(30):
        ib.sleep(1)
        if trade.orderStatus.status == 'Filled':
            print(f"\n*** FILLED @ ${trade.orderStatus.avgFillPrice:.2f} ***")
            break
        elif trade.orderStatus.status in ['Cancelled', 'Inactive']:
            print(f"\nOrder {trade.orderStatus.status}")
            break
        print(f"  Status: {trade.orderStatus.status}, Filled: {trade.orderStatus.filled}/{contracts}")
    else:
        print("\nTimeout - order still pending")
        print(f"Final status: {trade.orderStatus.status}")

    # Clean up
    ib.cancelMktData(ticker.contract)
    ib.disconnect()

    # If filled, remove state file
    if trade.orderStatus.status == 'Filled':
        print("\nRemoving state file...")
        state_file.unlink()
        print("Done!")

if __name__ == "__main__":
    main()
