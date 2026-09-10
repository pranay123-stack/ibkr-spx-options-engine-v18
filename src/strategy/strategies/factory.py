"""
Strategy Factory
Central registry and factory for creating strategy instances
"""

from typing import Dict, Type, Optional, List, Any

from .base import BaseStrategy, LegDefinition
from .credit_put_spread import CreditPutSpread
from .credit_call_spread import CreditCallSpread
from .debit_call_spread import DebitCallSpread
from .debit_put_spread import DebitPutSpread
from .iron_condor import IronCondor
from .iron_fly import IronFly
from .long_straddle import LongStraddle
from .short_straddle import ShortStraddle
from .long_strangle import LongStrangle
from .short_strangle import ShortStrangle
from .ratio_spread_calls import RatioSpreadCalls
from .ratio_spread_puts import RatioSpreadPuts


class StrategyFactory:
    """
    Factory class for creating and managing option strategies.

    Provides:
    - Strategy registration and lookup by ID
    - Strategy instantiation with custom parameters
    - CSV template generation
    - Strategy validation
    """

    # Registry of all available strategies
    _registry: Dict[str, Type[BaseStrategy]] = {
        "STRAT_CREDIT_PUT": CreditPutSpread,
        "STRAT_CREDIT_CALL": CreditCallSpread,
        "STRAT_DEBIT_CALL": DebitCallSpread,
        "STRAT_DEBIT_PUT": DebitPutSpread,
        "STRAT_IRON_CONDOR": IronCondor,
        "STRAT_IRON_FLY": IronFly,
        "STRAT_LONG_STRADDLE": LongStraddle,
        "STRAT_SHORT_STRADDLE": ShortStraddle,
        "STRAT_LONG_STRANGLE": LongStrangle,
        "STRAT_SHORT_STRANGLE": ShortStrangle,
        "STRAT_RATIO_CALL": RatioSpreadCalls,
        "STRAT_RATIO_PUT": RatioSpreadPuts,
    }

    @classmethod
    def register(cls, strategy_id: str, strategy_class: Type[BaseStrategy]) -> None:
        """
        Register a new strategy class.

        Args:
            strategy_id: Unique identifier for the strategy
            strategy_class: Strategy class (must inherit from BaseStrategy)
        """
        if not issubclass(strategy_class, BaseStrategy):
            raise ValueError(f"{strategy_class} must inherit from BaseStrategy")
        cls._registry[strategy_id] = strategy_class

    @classmethod
    def create(cls, strategy_id: str, **kwargs) -> Optional[BaseStrategy]:
        """
        Create a strategy instance by ID.

        Args:
            strategy_id: Strategy identifier
            **kwargs: Strategy-specific parameters (deltas, ratios, etc.)

        Returns:
            Strategy instance or None if not found
        """
        strategy_class = cls._registry.get(strategy_id)
        if strategy_class is None:
            return None
        return strategy_class(**kwargs)

    @classmethod
    def get_strategy_class(cls, strategy_id: str) -> Optional[Type[BaseStrategy]]:
        """Get the strategy class for a given ID"""
        return cls._registry.get(strategy_id)

    @classmethod
    def list_strategies(cls) -> List[str]:
        """Get list of all registered strategy IDs"""
        return list(cls._registry.keys())

    @classmethod
    def get_all_strategies(cls) -> Dict[str, BaseStrategy]:
        """Get instances of all registered strategies with default parameters"""
        return {
            strategy_id: strategy_class()
            for strategy_id, strategy_class in cls._registry.items()
        }

    @classmethod
    def get_strategy_info(cls, strategy_id: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed information about a strategy.

        Returns dict with:
        - strategy_id
        - description
        - strategy_type
        - is_credit
        - num_legs
        - legs (list of leg definitions)
        """
        strategy = cls.create(strategy_id)
        if strategy is None:
            return None

        return {
            "strategy_id": strategy.strategy_id,
            "description": strategy.description,
            "strategy_type": strategy.strategy_type.value,
            "is_credit": strategy.is_credit,
            "num_legs": strategy.num_legs,
            "legs": [leg.to_dict() for leg in strategy.get_legs()],
            "max_profit": strategy.get_max_profit_description(),
            "max_loss": strategy.get_max_loss_description(),
        }

    @classmethod
    def generate_csv_templates(cls, strategies: Optional[List[str]] = None) -> str:
        """
        Generate CSV content for strategy templates.

        Args:
            strategies: List of strategy IDs (None = all strategies)

        Returns:
            CSV content as string
        """
        if strategies is None:
            strategies = cls.list_strategies()

        csv_lines = ["strategy_id,leg,type,side,delta,expiry,qty"]

        for strategy_id in strategies:
            strategy = cls.create(strategy_id)
            if strategy is None:
                continue

            for row in strategy.get_csv_rows():
                csv_lines.append(
                    f"{row['strategy_id']},{row['leg']},{row['type']},"
                    f"{row['side']},{row['delta']},{row['expiry']},{row['qty']}"
                )

        return "\n".join(csv_lines)

    @classmethod
    def validate_all(cls) -> Dict[str, List[str]]:
        """
        Validate all registered strategies.

        Returns:
            Dict mapping strategy_id to list of errors (empty list = valid)
        """
        results = {}
        for strategy_id in cls.list_strategies():
            strategy = cls.create(strategy_id)
            if strategy:
                results[strategy_id] = strategy.validate()
        return results

    @classmethod
    def get_strategies_by_type(cls, strategy_type: str) -> List[str]:
        """
        Get all strategies of a specific type.

        Args:
            strategy_type: Type string (e.g., 'credit_spread', 'iron_condor')

        Returns:
            List of matching strategy IDs
        """
        matching = []
        for strategy_id, strategy_class in cls._registry.items():
            strategy = strategy_class()
            if strategy.strategy_type.value == strategy_type:
                matching.append(strategy_id)
        return matching

    @classmethod
    def get_credit_strategies(cls) -> List[str]:
        """Get all credit (premium receiving) strategies"""
        return [
            strategy_id
            for strategy_id, strategy_class in cls._registry.items()
            if strategy_class().is_credit
        ]

    @classmethod
    def get_debit_strategies(cls) -> List[str]:
        """Get all debit (premium paying) strategies"""
        return [
            strategy_id
            for strategy_id, strategy_class in cls._registry.items()
            if not strategy_class().is_credit
        ]

    @classmethod
    def print_strategy_summary(cls, logger=None) -> None:
        """Print a summary of all available strategies using logger"""
        import logging
        if logger is None:
            logger = logging.getLogger("StrategyFactory")

        logger.info("=" * 70)
        logger.info("AVAILABLE OPTION STRATEGIES")
        logger.info("=" * 70)

        for strategy_id in sorted(cls.list_strategies()):
            strategy = cls.create(strategy_id)
            if strategy:
                credit_type = "Credit" if strategy.is_credit else "Debit"
                logger.info(f"{strategy_id}")
                logger.info(f"  Description: {strategy.description}")
                logger.info(f"  Type: {strategy.strategy_type.value} ({credit_type})")
                logger.info(f"  Legs: {strategy.num_legs}")

        logger.info("=" * 70)
