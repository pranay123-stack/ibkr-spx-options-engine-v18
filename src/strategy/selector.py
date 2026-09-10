"""
Strategy Selector Module
Selects appropriate strategy based on market conditions and configuration
"""

from typing import Optional, List, Dict, Any
from dataclasses import dataclass

from ..utils.logging import TradingLogger
from ..utils.config import (
    ConfigLoader, StrategyMappingRule, StrategyTemplate
)
from ..enums import DirectionBias, VolRegime, TrendRegime
from ..signal import Signal


@dataclass
class StrategySelection:
    """Result of strategy selection"""
    strategy_id: str
    template: Optional[StrategyTemplate]
    matching_rule: Optional[StrategyMappingRule]
    reason: str
    is_valid: bool


class StrategySelector:
    """
    Selects the appropriate strategy template based on market conditions.

    Uses CSV-driven mapping rules to match:
    - Direction bias (BULLISH, BEARISH, NEUTRAL)
    - Volatility regime (vol_1, vol_2, vol_3)
    - Trend regime (UPTREND, DOWNTREND, SIDEWAYS)
    - Custom flags
    """

    def __init__(
        self,
        config_loader: ConfigLoader,
        logger: TradingLogger
    ):
        self.config_loader = config_loader
        self.logger = logger

        # Cache mapping rules and templates
        self.mapping_rules = config_loader.strategy_mapping
        self.templates = config_loader.strategy_templates

    def select_strategy(self, signal: Signal) -> StrategySelection:
        """
        Select a strategy based on the signal conditions.

        Args:
            signal: Signal with direction, vol_regime, and trend_regime

        Returns:
            StrategySelection with selected strategy or NO_TRADE
        """
        if not signal.signal_ok:
            return StrategySelection(
                strategy_id="NO_TRADE",
                template=None,
                matching_rule=None,
                reason=f"Signal not valid: {signal.reason}",
                is_valid=False
            )

        # Find matching rule
        matching_rule = self._find_matching_rule(
            direction=signal.direction_bias,
            vol_regime=signal.vol_regime,
            trend_regime=signal.trend_regime
        )

        if matching_rule is None:
            return StrategySelection(
                strategy_id="NO_TRADE",
                template=None,
                matching_rule=None,
                reason="No matching strategy rule found",
                is_valid=False
            )

        strategy_id = matching_rule.strategy_id

        if strategy_id == "NO_TRADE":
            return StrategySelection(
                strategy_id="NO_TRADE",
                template=None,
                matching_rule=matching_rule,
                reason="Rule indicates NO_TRADE for these conditions",
                is_valid=False
            )

        # Get the template
        template = self.templates.get(strategy_id)

        if template is None:
            self.logger.error(f"Strategy template not found: {strategy_id}")
            return StrategySelection(
                strategy_id=strategy_id,
                template=None,
                matching_rule=matching_rule,
                reason=f"Template not found for {strategy_id}",
                is_valid=False
            )

        self.logger.info(
            f"Strategy selected: {strategy_id} for "
            f"Direction={signal.direction_bias.value}, "
            f"Vol={signal.vol_regime.value if signal.vol_regime else 'N/A'}, "
            f"Trend={signal.trend_regime.value if signal.trend_regime else 'N/A'}"
        )

        return StrategySelection(
            strategy_id=strategy_id,
            template=template,
            matching_rule=matching_rule,
            reason="Strategy matched successfully",
            is_valid=True
        )

    def _find_matching_rule(
        self,
        direction: Optional[DirectionBias],
        vol_regime: Optional[VolRegime],
        trend_regime: Optional[TrendRegime],
        custom_flag_1: Optional[str] = None,
        custom_flag_2: Optional[str] = None
    ) -> Optional[StrategyMappingRule]:
        """
        Find the first matching rule from the mapping rules.

        Rules are evaluated in order (priority order from CSV).
        A rule matches if all non-ANY fields match.
        """
        for rule in self.mapping_rules:
            if self._rule_matches(
                rule, direction, vol_regime, trend_regime,
                custom_flag_1, custom_flag_2
            ):
                return rule

        return None

    def _rule_matches(
        self,
        rule: StrategyMappingRule,
        direction: Optional[DirectionBias],
        vol_regime: Optional[VolRegime],
        trend_regime: Optional[TrendRegime],
        custom_flag_1: Optional[str],
        custom_flag_2: Optional[str]
    ) -> bool:
        """
        Check if a rule matches the given conditions.

        None in rule = ANY (wildcard)
        None in condition = matches ANY
        """
        # Direction check
        if rule.direction is not None and direction is not None:
            if rule.direction != direction:
                return False

        # Vol regime check
        if rule.vol_regime is not None and vol_regime is not None:
            if rule.vol_regime != vol_regime:
                return False

        # Trend regime check
        if rule.trend_regime is not None and trend_regime is not None:
            if rule.trend_regime != trend_regime:
                return False

        # Custom flag 1 check
        if rule.custom_flag_1 is not None and custom_flag_1 is not None:
            if rule.custom_flag_1 != custom_flag_1:
                return False

        # Custom flag 2 check
        if rule.custom_flag_2 is not None and custom_flag_2 is not None:
            if rule.custom_flag_2 != custom_flag_2:
                return False

        return True

    def get_all_strategies(self) -> Dict[str, StrategyTemplate]:
        """Get all available strategy templates"""
        return self.templates

    def get_strategy_template(self, strategy_id: str) -> Optional[StrategyTemplate]:
        """Get a specific strategy template by ID"""
        return self.templates.get(strategy_id)

    def list_strategies_for_conditions(
        self,
        direction: Optional[DirectionBias] = None,
        vol_regime: Optional[VolRegime] = None,
        trend_regime: Optional[TrendRegime] = None
    ) -> List[str]:
        """
        List all strategies that could be selected for given conditions.
        Useful for analysis and debugging.
        """
        strategies = []
        for rule in self.mapping_rules:
            if self._rule_matches(rule, direction, vol_regime, trend_regime, None, None):
                strategies.append(rule.strategy_id)
        return strategies

    def validate_mapping(self) -> List[str]:
        """
        Validate that all strategies in mapping exist in templates.
        Returns list of errors.
        """
        errors = []
        for rule in self.mapping_rules:
            if rule.strategy_id == "NO_TRADE":
                continue
            if rule.strategy_id not in self.templates:
                errors.append(f"Strategy '{rule.strategy_id}' not found in templates")
        return errors

    def get_mapping_summary(self) -> Dict[str, Any]:
        """Get summary of strategy mapping"""
        direction_counts = {}
        vol_counts = {}
        trend_counts = {}
        strategy_counts = {}

        for rule in self.mapping_rules:
            # Direction
            dir_key = rule.direction.value if rule.direction else "ANY"
            direction_counts[dir_key] = direction_counts.get(dir_key, 0) + 1

            # Vol
            vol_key = rule.vol_regime.value if rule.vol_regime else "ANY"
            vol_counts[vol_key] = vol_counts.get(vol_key, 0) + 1

            # Trend
            trend_key = rule.trend_regime.value if rule.trend_regime else "ANY"
            trend_counts[trend_key] = trend_counts.get(trend_key, 0) + 1

            # Strategy
            strategy_counts[rule.strategy_id] = strategy_counts.get(rule.strategy_id, 0) + 1

        return {
            "total_rules": len(self.mapping_rules),
            "total_templates": len(self.templates),
            "by_direction": direction_counts,
            "by_vol_regime": vol_counts,
            "by_trend_regime": trend_counts,
            "by_strategy": strategy_counts
        }
