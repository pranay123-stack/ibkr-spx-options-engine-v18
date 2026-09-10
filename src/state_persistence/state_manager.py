"""
State Manager for Crash Recovery

Handles saving, loading, and reconciling position state for crash recovery.

Author: client Options Trading Engine
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, TYPE_CHECKING

from .models import (
    PositionState,
    SavedPosition,
    SavedLeg,
    IBKRPositionSnapshot,
    ReconciliationResult,
    ReconciliationStatus,
)

if TYPE_CHECKING:
    from ..data_classes import Position
    from ..broker import IBKRBroker
    from ..utils.logging import TradingLogger

# Schema version for forward compatibility
SCHEMA_VERSION = "1.0"


class StateManager:
    """
    Manages position state persistence for crash recovery.

    Saves position state to JSON file when position is opened,
    loads state on startup, reconciles with IBKR, and resumes
    management if reconciliation succeeds.
    """

    def __init__(
        self,
        state_dir: Path,
        underlying_symbol: str,
        logger: "TradingLogger",
        engine_id: str = "client_engine"
    ):
        self.state_dir = Path(state_dir)
        self.underlying_symbol = underlying_symbol
        self.logger = logger
        self.engine_id = engine_id
        self.state_file = self.state_dir / "position_state.json"

        # Ensure state directory exists
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def save_state(
        self,
        position: "Position",
        broker: Optional["IBKRBroker"] = None,
        bracket_order_info: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Save current position state to disk.

        Args:
            position: The current open position
            broker: Optional broker for capturing IBKR position snapshot
            bracket_order_info: Optional bracket order info (target_order_id, sl_order_id, oca_group)

        Returns:
            True if save succeeded, False otherwise
        """
        from .models import BracketOrderInfo

        try:
            # Convert Position to SavedPosition
            saved_position = self._position_to_saved(position)

            # Capture IBKR position snapshot if broker available
            ibkr_snapshot = []
            if broker:
                ibkr_snapshot = self._capture_ibkr_snapshot(broker)

            # Convert bracket order info if provided
            bracket_info = None
            if bracket_order_info:
                bracket_info = BracketOrderInfo(
                    target_order_id=bracket_order_info.get('target_order_id'),
                    sl_order_id=bracket_order_info.get('sl_order_id'),
                    oca_group=bracket_order_info.get('oca_group'),
                    target_price=bracket_order_info.get('target_price'),
                    sl_trigger_price=bracket_order_info.get('sl_trigger_price'),
                )

            # Build complete state
            state = PositionState(
                version=SCHEMA_VERSION,
                timestamp=datetime.now().isoformat(),
                engine_id=self.engine_id,
                underlying_symbol=self.underlying_symbol,
                position=saved_position,
                ibkr_snapshot=ibkr_snapshot,
                bracket_order_info=bracket_info,
            )

            # Atomic write: write to temp file, then replace
            temp_file = self.state_file.with_suffix(".json.tmp")
            state_dict = state.to_dict()

            # Clean up any stale temp files first (Windows fix)
            self._cleanup_stale_temp_files()

            # Write to temp file
            temp_file.write_text(
                json.dumps(state_dict, indent=2),
                encoding="utf-8"
            )

            # Verify JSON is valid before renaming
            saved_data = json.loads(temp_file.read_text(encoding="utf-8"))

            # ===== VALIDATION: Verify critical fields are present =====
            validation_passed = True
            if not saved_data.get('position', {}).get('legs'):
                self.logger.warning("[STATE] ⚠️ Validation failed: No legs in saved state")
                validation_passed = False
            if not saved_data.get('position', {}).get('entry_time'):
                self.logger.warning("[STATE] ⚠️ Validation failed: No entry_time in saved state")
                validation_passed = False
            if not saved_data.get('position', {}).get('position_id'):
                self.logger.warning("[STATE] ⚠️ Validation failed: No position_id in saved state")
                validation_passed = False

            if not validation_passed:
                self.logger.error("[STATE] State validation failed - not saving corrupted state")
                try:
                    temp_file.unlink()  # Clean up temp file
                except:
                    pass
                return False

            # Atomic replace (works on both Windows and Linux)
            # Use os.replace() instead of Path.rename() for cross-platform compatibility
            # os.replace() atomically overwrites the target file on both platforms
            self._atomic_replace_with_retry(temp_file, self.state_file)

            # Log validation success
            leg_count = len(saved_data.get('position', {}).get('legs', []))
            has_bracket = saved_data.get('bracket_order_info') is not None
            self.logger.info(f"[STATE] ✓ Position state saved: {position.position_id} ({leg_count} legs, bracket_orders={has_bracket})")
            return True

        except (OSError, IOError) as e:
            self.logger.error(f"[STATE] Failed to save state (file error): {e}")
            return False
        except json.JSONDecodeError as e:
            self.logger.error(f"[STATE] Failed to serialize state: {e}")
            return False
        except Exception as e:
            self.logger.error(f"[STATE] Unexpected error saving state: {e}")
            return False

    def load_state(self) -> Optional[PositionState]:
        """
        Load position state from disk.

        Returns:
            PositionState if file exists and is valid, None otherwise
        """
        if not self.state_file.exists():
            self.logger.debug("[STATE] No state file found")
            return None

        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))

            # Validate schema version
            version = data.get("version", "")
            if not version.startswith("1."):
                self.logger.warning(f"[STATE] Unsupported schema version: {version}")
                return None

            # Validate underlying symbol matches
            if data.get("underlying_symbol") != self.underlying_symbol:
                self.logger.warning(
                    f"[STATE] Underlying mismatch: state has {data.get('underlying_symbol')}, "
                    f"engine configured for {self.underlying_symbol}"
                )
                return None

            state = PositionState.from_dict(data)

            # Validate position has legs
            if not state.position.legs:
                self.logger.warning("[STATE] State file has no legs - invalid state")
                return None

            # Validate state is from today (not stale from previous day)
            from datetime import datetime, date
            try:
                # Parse entry_time to check date
                entry_time_str = data.get("position", {}).get("entry_time", "")
                if entry_time_str:
                    # Handle ISO format
                    if "T" in entry_time_str:
                        entry_date = datetime.fromisoformat(entry_time_str.replace("Z", "+00:00")).date()
                    else:
                        entry_date = datetime.strptime(entry_time_str[:10], "%Y-%m-%d").date()

                    today = date.today()
                    if entry_date < today:
                        self.logger.warning("=" * 60)
                        self.logger.warning("[STATE] STALE STATE FILE DETECTED")
                        self.logger.warning("=" * 60)
                        self.logger.warning(f"  State date: {entry_date}")
                        self.logger.warning(f"  Today: {today}")
                        self.logger.warning("  State is from previous trading day - ignoring")
                        self.logger.warning("  If position still open, check TWS manually")
                        self.logger.warning("=" * 60)
                        self._backup_corrupted_state()  # Backup old state
                        return None
            except Exception as date_err:
                self.logger.warning(f"[STATE] Could not validate state date: {date_err}")
                # Continue anyway - date validation is non-critical

            self.logger.info(f"[STATE] Loaded state: {state.position.position_id}")
            return state

        except json.JSONDecodeError as e:
            self.logger.error(f"[STATE] State file is corrupted (invalid JSON): {e}")
            self._backup_corrupted_state()
            return None
        except KeyError as e:
            self.logger.error(f"[STATE] State file missing required field: {e}")
            return None
        except Exception as e:
            self.logger.error(f"[STATE] Failed to load state: {e}")
            return None

    def clear_state(self) -> bool:
        """
        Delete the state file (called when position is closed).

        Returns:
            True if cleared successfully, False otherwise
        """
        try:
            if self.state_file.exists():
                self.state_file.unlink()
                self.logger.info("[STATE] Position state cleared")
            return True
        except (OSError, IOError) as e:
            self.logger.error(f"[STATE] Failed to clear state: {e}")
            return False

    def reconcile_with_ibkr(
        self,
        saved_state: PositionState,
        broker: "IBKRBroker"
    ) -> ReconciliationResult:
        """
        Compare saved state with current IBKR positions.

        Args:
            saved_state: Previously saved position state
            broker: IBKR broker connection

        Returns:
            ReconciliationResult with status and details
        """
        try:
            # Check broker connection
            if not broker or not broker.is_connected():
                return ReconciliationResult(
                    status=ReconciliationStatus.IBKR_UNAVAILABLE,
                    reason="Cannot connect to IBKR for reconciliation"
                )

            # Get current IBKR positions
            positions = broker.request_positions()

            # Filter for option positions on our underlying
            ibkr_options = {}
            for key, pos_info in positions.items():
                if (pos_info.sec_type == "OPT" and
                    pos_info.symbol == self.underlying_symbol and
                    pos_info.position != 0):
                    # Key by strike-right-expiry
                    pos_key = f"{pos_info.strike}-{pos_info.right}-{pos_info.expiry}"
                    ibkr_options[pos_key] = pos_info

            # If no IBKR positions, position was closed
            if not ibkr_options:
                return ReconciliationResult(
                    status=ReconciliationStatus.NO_MATCH,
                    reason="No option positions found in IBKR - position was closed"
                )

            # Build set of expected legs from saved state
            expected_legs = {}
            for leg in saved_state.position.legs:
                leg_key = f"{leg.strike}-{leg.option_type}-{leg.expiry}"
                expected_quantity = leg.quantity * saved_state.position.contracts
                if leg.side == "SELL":
                    expected_quantity = -expected_quantity
                expected_legs[leg_key] = {
                    "strike": leg.strike,
                    "right": leg.option_type,
                    "expiry": leg.expiry,
                    "expected_position": expected_quantity,
                    "leg": leg,
                }

            # Compare
            matched_legs = []
            missing_legs = []
            quantity_mismatches = []

            for leg_key, leg_info in expected_legs.items():
                if leg_key in ibkr_options:
                    ibkr_pos = ibkr_options[leg_key]
                    if ibkr_pos.position == leg_info["expected_position"]:
                        matched_legs.append(leg_info)
                    else:
                        quantity_mismatches.append({
                            **leg_info,
                            "actual_position": ibkr_pos.position,
                        })
                else:
                    missing_legs.append(leg_info)

            # Check for extra positions in IBKR
            expected_keys = set(expected_legs.keys())
            ibkr_keys = set(ibkr_options.keys())
            extra_keys = ibkr_keys - expected_keys
            extra_positions = [
                {
                    "strike": ibkr_options[k].strike,
                    "right": ibkr_options[k].right,
                    "expiry": ibkr_options[k].expiry,
                    "position": ibkr_options[k].position,
                }
                for k in extra_keys
            ]

            # Determine result
            if missing_legs:
                return ReconciliationResult(
                    status=ReconciliationStatus.PARTIAL_MATCH,
                    reason=f"Missing {len(missing_legs)} legs in IBKR - partial close?",
                    matched_legs=[{"strike": m["strike"], "right": m["right"]} for m in matched_legs],
                    missing_legs=[{"strike": m["strike"], "right": m["right"]} for m in missing_legs],
                )

            if quantity_mismatches:
                return ReconciliationResult(
                    status=ReconciliationStatus.QUANTITY_MISMATCH,
                    reason=f"Quantity mismatch on {len(quantity_mismatches)} legs",
                    warnings=[
                        f"Leg {m['strike']}{m['right']}: expected {m['expected_position']}, "
                        f"IBKR has {m['actual_position']}"
                        for m in quantity_mismatches
                    ],
                )

            if extra_positions:
                return ReconciliationResult(
                    status=ReconciliationStatus.EXTRA_POSITIONS,
                    reason=f"IBKR has {len(extra_positions)} additional positions",
                    matched_legs=[{"strike": m["strike"], "right": m["right"]} for m in matched_legs],
                    extra_positions=extra_positions,
                )

            # All legs match exactly
            return ReconciliationResult(
                status=ReconciliationStatus.EXACT_MATCH,
                reason="All legs match IBKR positions",
                matched_legs=[{"strike": m["strike"], "right": m["right"]} for m in matched_legs],
            )

        except Exception as e:
            return ReconciliationResult(
                status=ReconciliationStatus.ERROR,
                reason=f"Unexpected error during reconciliation: {e}"
            )

    def _cleanup_stale_temp_files(self):
        """
        Clean up any stale .tmp files in the state directory.

        This prevents Windows [WinError 183] when a previous temp file wasn't cleaned up.
        Safe to call on both Windows and Linux.
        """
        try:
            for tmp_file in self.state_dir.glob("*.tmp"):
                try:
                    tmp_file.unlink()
                    self.logger.debug(f"[STATE] Cleaned up stale temp file: {tmp_file.name}")
                except Exception as e:
                    self.logger.warning(f"[STATE] Failed to clean up {tmp_file.name}: {e}")
        except Exception as e:
            self.logger.warning(f"[STATE] Failed to scan for temp files: {e}")

    def _atomic_replace_with_retry(
        self,
        temp_file: Path,
        target_file: Path,
        max_retries: int = 3,
        retry_delay: float = 0.1
    ):
        """
        Atomically replace target file with temp file, with retry logic.

        Uses os.replace() which works atomically on both Windows and Linux.
        Retries on transient file lock errors (common on Windows).

        Args:
            temp_file: Path to temp file
            target_file: Path to target file to replace
            max_retries: Maximum number of retry attempts
            retry_delay: Initial delay between retries (exponential backoff)

        Raises:
            OSError: If all retry attempts fail
        """
        last_error = None

        for attempt in range(max_retries):
            try:
                # Use os.replace() for atomic overwrite on both Windows and Linux
                # This is guaranteed to be atomic and will overwrite the target
                os.replace(str(temp_file), str(target_file))

                if attempt > 0:
                    self.logger.info(f"[STATE] File write succeeded on retry {attempt + 1}")

                return  # Success!

            except (OSError, IOError) as e:
                last_error = e

                if attempt < max_retries - 1:
                    # Transient error, retry with exponential backoff
                    wait_time = retry_delay * (2 ** attempt)
                    self.logger.warning(
                        f"[STATE] File replace failed (attempt {attempt + 1}/{max_retries}): {e}"
                    )
                    self.logger.warning(f"[STATE] Retrying in {wait_time:.2f}s...")
                    time.sleep(wait_time)
                else:
                    # Final attempt failed
                    self.logger.error(f"[STATE] File replace failed after {max_retries} attempts")

        # All retries exhausted, raise the last error
        raise last_error

    def _position_to_saved(self, position: "Position") -> SavedPosition:
        """Convert Position object to SavedPosition for serialization."""
        legs = []
        for leg in position.built_strategy.legs:
            saved_leg = SavedLeg(
                leg_index=leg.leg_index,
                option_type=leg.option_type.value,
                side=leg.side.value,
                strike=leg.strike,
                expiry=leg.expiry,
                quantity=leg.quantity,
                entry_price=leg.entry_price,
                target_delta=leg.target_delta,
                actual_delta=leg.actual_delta,
            )
            legs.append(saved_leg)

        return SavedPosition(
            position_id=position.position_id,
            strategy_id=position.strategy_id,
            contracts=position.contracts,
            entry_time=position.entry_time.isoformat() if position.entry_time else "",
            entry_spread=position.entry_spread,
            target_spread=position.target_spread,
            stop_spread=position.stop_spread,
            is_credit=position.is_credit,
            direction_bias=position.direction_bias,
            vol_regime=position.vol_regime,
            trend_regime=position.trend_regime,
            underlying_price_entry=position.underlying_price_entry,
            legs=legs,
        )

    def _capture_ibkr_snapshot(self, broker: "IBKRBroker") -> list:
        """Capture current IBKR positions as snapshot for verification."""
        snapshots = []
        try:
            positions = broker.request_positions()
            for key, pos_info in positions.items():
                if (pos_info.sec_type == "OPT" and
                    pos_info.symbol == self.underlying_symbol and
                    pos_info.position != 0):
                    snapshots.append(IBKRPositionSnapshot(
                        symbol=pos_info.symbol,
                        strike=pos_info.strike,
                        right=pos_info.right,
                        expiry=pos_info.expiry,
                        position=pos_info.position,
                        avg_cost=pos_info.avg_cost,
                    ))
        except Exception as e:
            self.logger.warning(f"[STATE] Failed to capture IBKR snapshot: {e}")
        return snapshots

    def _backup_corrupted_state(self):
        """Backup corrupted state file for debugging."""
        try:
            if self.state_file.exists():
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup_name = f"position_state_corrupted_{timestamp}.json"
                backup_path = self.state_dir / backup_name
                self.state_file.rename(backup_path)
                self.logger.info(f"[STATE] Corrupted state backed up to {backup_name}")
        except Exception as e:
            self.logger.error(f"[STATE] Failed to backup corrupted state: {e}")
