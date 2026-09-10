"""
State Persistence Models

Data classes for state persistence and crash recovery.

Author: client Options Trading Engine
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Dict, Any, Optional


class ReconciliationStatus(Enum):
    """Status of reconciliation between saved state and IBKR positions"""
    EXACT_MATCH = "exact_match"           # All legs match exactly
    NO_MATCH = "no_match"                 # No IBKR positions found
    PARTIAL_MATCH = "partial_match"       # Some legs missing in IBKR
    QUANTITY_MISMATCH = "quantity_mismatch"  # Leg quantities differ
    EXTRA_POSITIONS = "extra_positions"   # IBKR has additional positions
    IBKR_UNAVAILABLE = "ibkr_unavailable"  # Cannot connect to IBKR
    ERROR = "error"                       # Unexpected error during reconciliation


@dataclass
class ReconciliationResult:
    """Result of reconciliation between saved state and IBKR"""
    status: ReconciliationStatus
    reason: str = ""
    matched_legs: List[Dict[str, Any]] = field(default_factory=list)
    missing_legs: List[Dict[str, Any]] = field(default_factory=list)
    extra_positions: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass
class SavedLeg:
    """Serializable representation of a built leg"""
    leg_index: int
    option_type: str  # "C" or "P"
    side: str         # "BUY" or "SELL"
    strike: float
    expiry: str
    quantity: int
    entry_price: float
    target_delta: float = 0.0
    actual_delta: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "leg_index": self.leg_index,
            "option_type": self.option_type,
            "side": self.side,
            "strike": self.strike,
            "expiry": self.expiry,
            "quantity": self.quantity,
            "entry_price": self.entry_price,
            "target_delta": self.target_delta,
            "actual_delta": self.actual_delta,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SavedLeg":
        return cls(
            leg_index=data["leg_index"],
            option_type=data["option_type"],
            side=data["side"],
            strike=data["strike"],
            expiry=data["expiry"],
            quantity=data["quantity"],
            entry_price=data["entry_price"],
            target_delta=data.get("target_delta", 0.0),
            actual_delta=data.get("actual_delta"),
        )


@dataclass
class SavedPosition:
    """Serializable representation of a position"""
    position_id: str
    strategy_id: str
    contracts: int
    entry_time: str  # ISO format datetime
    entry_spread: float
    target_spread: float
    stop_spread: float
    is_credit: bool
    direction_bias: str
    vol_regime: str
    trend_regime: str
    underlying_price_entry: float
    legs: List[SavedLeg] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "position_id": self.position_id,
            "strategy_id": self.strategy_id,
            "contracts": self.contracts,
            "entry_time": self.entry_time,
            "entry_spread": self.entry_spread,
            "target_spread": self.target_spread,
            "stop_spread": self.stop_spread,
            "is_credit": self.is_credit,
            "direction_bias": self.direction_bias,
            "vol_regime": self.vol_regime,
            "trend_regime": self.trend_regime,
            "underlying_price_entry": self.underlying_price_entry,
            "legs": [leg.to_dict() for leg in self.legs],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SavedPosition":
        legs = [SavedLeg.from_dict(leg_data) for leg_data in data.get("legs", [])]
        return cls(
            position_id=data["position_id"],
            strategy_id=data["strategy_id"],
            contracts=data["contracts"],
            entry_time=data["entry_time"],
            entry_spread=data["entry_spread"],
            target_spread=data["target_spread"],
            stop_spread=data["stop_spread"],
            is_credit=data["is_credit"],
            direction_bias=data.get("direction_bias", ""),
            vol_regime=data.get("vol_regime", ""),
            trend_regime=data.get("trend_regime", ""),
            underlying_price_entry=data.get("underlying_price_entry", 0.0),
            legs=legs,
        )


@dataclass
class IBKRPositionSnapshot:
    """Snapshot of an IBKR position for reconciliation"""
    symbol: str
    strike: float
    right: str  # "C" or "P"
    expiry: str
    position: float  # Positive for long, negative for short
    avg_cost: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "strike": self.strike,
            "right": self.right,
            "expiry": self.expiry,
            "position": self.position,
            "avg_cost": self.avg_cost,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "IBKRPositionSnapshot":
        return cls(
            symbol=data["symbol"],
            strike=data["strike"],
            right=data["right"],
            expiry=data["expiry"],
            position=data["position"],
            avg_cost=data.get("avg_cost", 0.0),
        )


@dataclass
class BracketOrderInfo:
    """Bracket order info for crash recovery"""
    target_order_id: Optional[int] = None
    sl_order_id: Optional[int] = None
    oca_group: Optional[str] = None
    target_price: Optional[float] = None
    sl_trigger_price: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_order_id": self.target_order_id,
            "sl_order_id": self.sl_order_id,
            "oca_group": self.oca_group,
            "target_price": self.target_price,
            "sl_trigger_price": self.sl_trigger_price,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BracketOrderInfo":
        return cls(
            target_order_id=data.get("target_order_id"),
            sl_order_id=data.get("sl_order_id"),
            oca_group=data.get("oca_group"),
            target_price=data.get("target_price"),
            sl_trigger_price=data.get("sl_trigger_price"),
        )


@dataclass
class PositionState:
    """Complete state for crash recovery"""
    version: str
    timestamp: str  # ISO format datetime
    engine_id: str
    underlying_symbol: str
    position: SavedPosition
    ibkr_snapshot: List[IBKRPositionSnapshot] = field(default_factory=list)
    bracket_order_info: Optional[BracketOrderInfo] = None  # Bracket order IDs for recovery

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "version": self.version,
            "timestamp": self.timestamp,
            "engine_id": self.engine_id,
            "underlying_symbol": self.underlying_symbol,
            "position": self.position.to_dict(),
            "ibkr_snapshot": [snap.to_dict() for snap in self.ibkr_snapshot],
        }
        if self.bracket_order_info:
            result["bracket_order_info"] = self.bracket_order_info.to_dict()
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PositionState":
        position = SavedPosition.from_dict(data["position"])
        ibkr_snapshot = [
            IBKRPositionSnapshot.from_dict(snap_data)
            for snap_data in data.get("ibkr_snapshot", [])
        ]
        bracket_order_info = None
        if "bracket_order_info" in data and data["bracket_order_info"]:
            bracket_order_info = BracketOrderInfo.from_dict(data["bracket_order_info"])
        return cls(
            version=data["version"],
            timestamp=data["timestamp"],
            engine_id=data["engine_id"],
            underlying_symbol=data["underlying_symbol"],
            position=position,
            ibkr_snapshot=ibkr_snapshot,
            bracket_order_info=bracket_order_info,
        )
