"""
Execution Result Data Class

Author: client Options Trading Engine
"""

from dataclasses import dataclass
from typing import Optional, Any


@dataclass
class ExecutionResult:
    """Result of a spread execution"""
    success: bool
    combo_order: Optional[Any]
    fill_price: float
    error_message: Optional[str] = None
    execution_time: float = 0.0
