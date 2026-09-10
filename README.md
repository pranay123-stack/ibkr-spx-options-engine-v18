# IBKR SPX Options Engine — v18

A config-driven live trading engine for SPX index options on the Interactive Brokers TWS API.
Iteration 18 of a delivered engagement.

## Architecture

- `main.py` / `src/engine.py` — the run loop
- `src/data_classes/` — typed IV statistics, risk metrics and position models
- `config/` — strategy parameters, driven entirely from config rather than code edits
- `run_with_autorestart.py` — supervisor wrapper that restarts the engine on crash and
  records why it stopped
- `close_position.py` / `close_all_positions.py` — operator kill switches

Ships `Trading Engine – Developer Specification (Live, Config-Driven).pdf`, the written spec
the implementation was built against.

Includes committed implied-volatility series (`current_iv.csv`, `historical_iv.csv`) used
by the IV statistics layer.

## What's interesting

The **auto-restart supervisor** distinguishes a clean exit, a crash, and an operator Ctrl+C,
and only restarts on the middle case — a live options engine that blindly restarts after a
deliberate shutdown will re-enter positions you just closed.

## Honest status

Delivered under a commercial engagement, published with client identifiers, runtime logs,
position state and account references removed.

**96 source files, 1 test.** That ratio is the honest weakness: for an engine that places
multi-leg option orders against a live account, the order-construction and exit paths need
real coverage.

No performance results are published.

**Tech:** Python, ib_insync/TWS API
