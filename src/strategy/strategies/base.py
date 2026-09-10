"""
Base Strategy Class
Abstract base class for all option strategies
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Any, Optional

from ...enums import OptionType, OrderSide, ExpiryRule


class StrategyType(Enum):
    """Types of option strategies"""
    CREDIT_SPREAD = "credit_spread"
    DEBIT_SPREAD = "debit_spread"
    IRON_CONDOR = "iron_condor"
    IRON_FLY = "iron_fly"
    STRADDLE = "straddle"
    STRANGLE = "strangle"
    RATIO_SPREAD = "ratio_spread"


@dataclass
class LegDefinition:
    """Definition for a single option leg"""
    leg_index: int
    option_type: OptionType
    side: OrderSide
    target_delta: float
    expiry_rule: ExpiryRule = ExpiryRule.SAME_DAY
    quantity_factor: int = 1

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format"""
        return {
            "leg_index": self.leg_index,
            "option_type": self.option_type.value,
            "side": self.side.value,
            "target_delta": self.target_delta,
            "expiry_rule": self.expiry_rule.value,
            "quantity_factor": self.quantity_factor
        }


class BaseStrategy(ABC):
    """
    Abstract base class for all option strategies.

    Each strategy must implement:
    - strategy_id: Unique identifier
    - strategy_type: Type categorization
    - description: Human-readable description
    - is_credit: Whether this is a credit strategy
    - get_legs(): Returns list of LegDefinition
    """

    def __init__(self, **kwargs):
        """
        Initialize strategy with optional parameters.
        Subclasses should override default deltas via kwargs.
        """
        self._params = kwargs

    @property
    @abstractmethod
    def strategy_id(self) -> str:
        """Unique strategy identifier (e.g., 'STRAT_CREDIT_PUT')"""
        pass

    @property
    @abstractmethod
    def strategy_type(self) -> StrategyType:
        """Strategy type categorization"""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description of the strategy"""
        pass

    @property
    @abstractmethod
    def is_credit(self) -> bool:
        """True if this is a credit strategy (receives premium)"""
        pass

    @property
    def num_legs(self) -> int:
        """Number of legs in this strategy"""
        return len(self.get_legs())

    @abstractmethod
    def get_legs(self) -> List[LegDefinition]:
        """
        Get the leg definitions for this strategy.
        Returns list of LegDefinition objects.
        """
        pass

    def get_csv_rows(self) -> List[Dict[str, Any]]:
        """
        Get CSV-format rows for this strategy template.
        Useful for generating configuration files.
        """
        rows = []
        for leg in self.get_legs():
            rows.append({
                "strategy_id": self.strategy_id,
                "leg": leg.leg_index,
                "type": leg.option_type.value,
                "side": leg.side.value,
                "delta": leg.target_delta,
                "expiry": leg.expiry_rule.value,
                "qty": leg.quantity_factor
            })
        return rows

    def validate(self) -> List[str]:
        """
        Validate the strategy configuration.
        Returns list of error messages (empty if valid).
        """
        errors = []
        legs = self.get_legs()

        if not legs:
            errors.append(f"{self.strategy_id}: No legs defined")
            return errors

        # Check leg indices are sequential
        indices = [leg.leg_index for leg in legs]
        expected = list(range(1, len(legs) + 1))
        if sorted(indices) != expected:
            errors.append(f"{self.strategy_id}: Leg indices should be sequential starting from 1")

        # Check delta values are reasonable
        for leg in legs:
            if leg.target_delta < 0 or leg.target_delta > 1:
                errors.append(
                    f"{self.strategy_id}: Leg {leg.leg_index} has invalid delta {leg.target_delta}"
                )

        return errors

    def get_max_profit_description(self) -> str:
        """Description of maximum profit scenario"""
        if self.is_credit:
            return "Premium received when spread expires worthless"
        return "Strike width minus premium paid"

    def get_max_loss_description(self) -> str:
        """Description of maximum loss scenario"""
        if self.is_credit:
            return "Strike width minus premium received"
        return "Premium paid"

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(id={self.strategy_id}, legs={self.num_legs})"

    def __str__(self) -> str:
        return f"{self.description} ({self.strategy_id})"
