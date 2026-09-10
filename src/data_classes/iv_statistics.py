"""
IV Statistics

Author: client Options Trading Engine
"""

from typing import Optional
from dataclasses import dataclass


@dataclass
class IVStatistics:
    """IV statistics over a period"""
    count: int = 0
    min_iv: float = 0.0
    max_iv: float = 0.0
    mean_iv: float = 0.0
    median_iv: float = 0.0
    std_iv: float = 0.0
    current_iv: Optional[float] = None
    percentile: float = 50.0
