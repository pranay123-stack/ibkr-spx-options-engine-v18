"""
Option Strategy Classes
Each strategy is implemented as a separate class with its own file.
"""

from .base import BaseStrategy, LegDefinition, StrategyType
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
from .factory import StrategyFactory

__all__ = [
    "BaseStrategy",
    "LegDefinition",
    "StrategyType",
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
    "StrategyFactory",
]
