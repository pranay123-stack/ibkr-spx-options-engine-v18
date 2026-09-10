"""
Risk Management Module - Position Sizing and Risk Calculations

Author: client Options Trading Engine
"""

from .risk_calculator import RiskCalculator

# Re-export RiskMetrics from data_classes for backward compatibility
from ...data_classes import RiskMetrics

__all__ = ["RiskCalculator", "RiskMetrics"]
