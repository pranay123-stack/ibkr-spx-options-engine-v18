"""Strategy selection and structure building modules"""

from .selector import StrategySelector
from .builder import StrategyBuilder, BuiltStrategy, BuiltLeg
from .templates import StrategyTemplates, LegDefinition

# Import all individual strategy classes
from .strategies import (
    BaseStrategy,
    StrategyType,
    CreditPutSpread,
    CreditCallSpread,
    DebitCallSpread,
    DebitPutSpread,
    IronCondor,
    IronFly,
    LongStraddle,
    ShortStraddle,
    LongStrangle,
    ShortStrangle,
    RatioSpreadCalls,
    RatioSpreadPuts,
    StrategyFactory,
)

__all__ = [
    # Selector and Builder
    "StrategySelector",
    "StrategyBuilder",
    "BuiltStrategy",
    "BuiltLeg",
    # Legacy templates
    "StrategyTemplates",
    "LegDefinition",
    # Base classes
    "BaseStrategy",
    "StrategyType",
    # Individual strategies
    "CreditPutSpread",
    "CreditCallSpread",
    "DebitCallSpread",
    "DebitPutSpread",
    "IronCondor",
    "IronFly",
    "LongStraddle",
    "ShortStraddle",
    "LongStrangle",
    "ShortStrangle",
    "RatioSpreadCalls",
    "RatioSpreadPuts",
    # Factory
    "StrategyFactory",
]
