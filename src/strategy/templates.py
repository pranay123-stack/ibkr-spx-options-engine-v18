"""
Strategy Templates Module
Defines common option strategy structures
"""

from typing import List, Dict, Any
from dataclasses import dataclass

from ..enums import OptionType, OrderSide, ExpiryRule


@dataclass
class LegDefinition:
    """Definition for a single option leg"""
    leg_index: int
    option_type: OptionType
    side: OrderSide
    target_delta: float
    expiry_rule: ExpiryRule = ExpiryRule.SAME_DAY
    quantity_factor: int = 1


class StrategyTemplates:
    """
    Pre-defined strategy templates.

    These can be used as defaults or examples for the CSV configuration.
    """

    @staticmethod
    def credit_put_spread(short_delta: float = 0.25, long_delta: float = 0.10) -> List[LegDefinition]:
        """Bull Put Spread (Credit Put Spread)"""
        return [
            LegDefinition(
                leg_index=1,
                option_type=OptionType.PUT,
                side=OrderSide.SELL,
                target_delta=short_delta
            ),
            LegDefinition(
                leg_index=2,
                option_type=OptionType.PUT,
                side=OrderSide.BUY,
                target_delta=long_delta
            )
        ]

    @staticmethod
    def credit_call_spread(short_delta: float = 0.25, long_delta: float = 0.10) -> List[LegDefinition]:
        """Bear Call Spread (Credit Call Spread)"""
        return [
            LegDefinition(
                leg_index=1,
                option_type=OptionType.CALL,
                side=OrderSide.SELL,
                target_delta=short_delta
            ),
            LegDefinition(
                leg_index=2,
                option_type=OptionType.CALL,
                side=OrderSide.BUY,
                target_delta=long_delta
            )
        ]

    @staticmethod
    def debit_call_spread(long_delta: float = 0.50, short_delta: float = 0.25) -> List[LegDefinition]:
        """Bull Call Spread (Debit Call Spread)"""
        return [
            LegDefinition(
                leg_index=1,
                option_type=OptionType.CALL,
                side=OrderSide.BUY,
                target_delta=long_delta
            ),
            LegDefinition(
                leg_index=2,
                option_type=OptionType.CALL,
                side=OrderSide.SELL,
                target_delta=short_delta
            )
        ]

    @staticmethod
    def debit_put_spread(long_delta: float = 0.50, short_delta: float = 0.25) -> List[LegDefinition]:
        """Bear Put Spread (Debit Put Spread)"""
        return [
            LegDefinition(
                leg_index=1,
                option_type=OptionType.PUT,
                side=OrderSide.BUY,
                target_delta=long_delta
            ),
            LegDefinition(
                leg_index=2,
                option_type=OptionType.PUT,
                side=OrderSide.SELL,
                target_delta=short_delta
            )
        ]

    @staticmethod
    def iron_condor(
        put_long_delta: float = 0.10,
        put_short_delta: float = 0.25,
        call_short_delta: float = 0.25,
        call_long_delta: float = 0.10
    ) -> List[LegDefinition]:
        """Iron Condor"""
        return [
            LegDefinition(
                leg_index=1,
                option_type=OptionType.PUT,
                side=OrderSide.BUY,
                target_delta=put_long_delta
            ),
            LegDefinition(
                leg_index=2,
                option_type=OptionType.PUT,
                side=OrderSide.SELL,
                target_delta=put_short_delta
            ),
            LegDefinition(
                leg_index=3,
                option_type=OptionType.CALL,
                side=OrderSide.SELL,
                target_delta=call_short_delta
            ),
            LegDefinition(
                leg_index=4,
                option_type=OptionType.CALL,
                side=OrderSide.BUY,
                target_delta=call_long_delta
            )
        ]

    @staticmethod
    def iron_fly(
        put_long_delta: float = 0.10,
        atm_delta: float = 0.50,
        call_long_delta: float = 0.10
    ) -> List[LegDefinition]:
        """Iron Butterfly (Iron Fly)"""
        return [
            LegDefinition(
                leg_index=1,
                option_type=OptionType.PUT,
                side=OrderSide.BUY,
                target_delta=put_long_delta
            ),
            LegDefinition(
                leg_index=2,
                option_type=OptionType.PUT,
                side=OrderSide.SELL,
                target_delta=atm_delta
            ),
            LegDefinition(
                leg_index=3,
                option_type=OptionType.CALL,
                side=OrderSide.SELL,
                target_delta=atm_delta
            ),
            LegDefinition(
                leg_index=4,
                option_type=OptionType.CALL,
                side=OrderSide.BUY,
                target_delta=call_long_delta
            )
        ]

    @staticmethod
    def long_straddle(atm_delta: float = 0.50) -> List[LegDefinition]:
        """Long Straddle"""
        return [
            LegDefinition(
                leg_index=1,
                option_type=OptionType.PUT,
                side=OrderSide.BUY,
                target_delta=atm_delta
            ),
            LegDefinition(
                leg_index=2,
                option_type=OptionType.CALL,
                side=OrderSide.BUY,
                target_delta=atm_delta
            )
        ]

    @staticmethod
    def short_straddle(atm_delta: float = 0.50) -> List[LegDefinition]:
        """Short Straddle"""
        return [
            LegDefinition(
                leg_index=1,
                option_type=OptionType.PUT,
                side=OrderSide.SELL,
                target_delta=atm_delta
            ),
            LegDefinition(
                leg_index=2,
                option_type=OptionType.CALL,
                side=OrderSide.SELL,
                target_delta=atm_delta
            )
        ]

    @staticmethod
    def long_strangle(put_delta: float = 0.25, call_delta: float = 0.25) -> List[LegDefinition]:
        """Long Strangle"""
        return [
            LegDefinition(
                leg_index=1,
                option_type=OptionType.PUT,
                side=OrderSide.BUY,
                target_delta=put_delta
            ),
            LegDefinition(
                leg_index=2,
                option_type=OptionType.CALL,
                side=OrderSide.BUY,
                target_delta=call_delta
            )
        ]

    @staticmethod
    def short_strangle(put_delta: float = 0.25, call_delta: float = 0.25) -> List[LegDefinition]:
        """Short Strangle"""
        return [
            LegDefinition(
                leg_index=1,
                option_type=OptionType.PUT,
                side=OrderSide.SELL,
                target_delta=put_delta
            ),
            LegDefinition(
                leg_index=2,
                option_type=OptionType.CALL,
                side=OrderSide.SELL,
                target_delta=call_delta
            )
        ]

    @staticmethod
    def ratio_spread_calls(
        atm_delta: float = 0.50,
        otm_delta: float = 0.25,
        ratio: int = 2
    ) -> List[LegDefinition]:
        """Call Ratio Spread (Buy 1 ATM, Sell 2 OTM)"""
        return [
            LegDefinition(
                leg_index=1,
                option_type=OptionType.CALL,
                side=OrderSide.BUY,
                target_delta=atm_delta,
                quantity_factor=1
            ),
            LegDefinition(
                leg_index=2,
                option_type=OptionType.CALL,
                side=OrderSide.SELL,
                target_delta=otm_delta,
                quantity_factor=ratio
            )
        ]

    @staticmethod
    def ratio_spread_puts(
        atm_delta: float = 0.50,
        otm_delta: float = 0.25,
        ratio: int = 2
    ) -> List[LegDefinition]:
        """Put Ratio Spread (Buy 1 ATM, Sell 2 OTM)"""
        return [
            LegDefinition(
                leg_index=1,
                option_type=OptionType.PUT,
                side=OrderSide.BUY,
                target_delta=atm_delta,
                quantity_factor=1
            ),
            LegDefinition(
                leg_index=2,
                option_type=OptionType.PUT,
                side=OrderSide.SELL,
                target_delta=otm_delta,
                quantity_factor=ratio
            )
        ]

    @classmethod
    def get_template_csv_rows(cls, strategy_name: str) -> List[Dict[str, Any]]:
        """
        Get CSV-format rows for a predefined strategy.
        Useful for generating configuration files.
        """
        templates = {
            "STRAT_CREDIT_PUT": cls.credit_put_spread(),
            "STRAT_CREDIT_CALL": cls.credit_call_spread(),
            "STRAT_DEBIT_CALL": cls.debit_call_spread(),
            "STRAT_DEBIT_PUT": cls.debit_put_spread(),
            "STRAT_IRON_CONDOR": cls.iron_condor(),
            "STRAT_IRON_FLY": cls.iron_fly(),
            "STRAT_LONG_STRADDLE": cls.long_straddle(),
            "STRAT_SHORT_STRADDLE": cls.short_straddle(),
            "STRAT_LONG_STRANGLE": cls.long_strangle(),
            "STRAT_SHORT_STRANGLE": cls.short_strangle()
        }

        if strategy_name not in templates:
            return []

        legs = templates[strategy_name]
        rows = []

        for leg in legs:
            rows.append({
                "strategy_id": strategy_name,
                "leg": leg.leg_index,
                "type": leg.option_type.value,
                "side": leg.side.value,
                "delta": leg.target_delta,
                "expiry": leg.expiry_rule.value,
                "qty": leg.quantity_factor
            })

        return rows

    @classmethod
    def generate_all_templates_csv(cls) -> str:
        """Generate CSV content for all predefined templates"""
        all_strategies = [
            "STRAT_CREDIT_PUT",
            "STRAT_CREDIT_CALL",
            "STRAT_DEBIT_CALL",
            "STRAT_DEBIT_PUT",
            "STRAT_IRON_CONDOR",
            "STRAT_IRON_FLY",
            "STRAT_LONG_STRADDLE",
            "STRAT_SHORT_STRADDLE",
            "STRAT_LONG_STRANGLE",
            "STRAT_SHORT_STRANGLE"
        ]

        csv_lines = ["strategy_id,leg,type,side,delta,expiry,qty"]

        for strat in all_strategies:
            rows = cls.get_template_csv_rows(strat)
            for row in rows:
                csv_lines.append(
                    f"{row['strategy_id']},{row['leg']},{row['type']},"
                    f"{row['side']},{row['delta']},{row['expiry']},{row['qty']}"
                )

        return "\n".join(csv_lines)
