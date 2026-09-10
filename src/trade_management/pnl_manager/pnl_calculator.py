"""
P&L Calculator - Profit and Loss Calculations

Handles all P&L related calculations:
- Unrealized P&L calculation
- Realized P&L calculation
- Slippage adjustments
- Daily summary statistics

Author: client Options Trading Engine
"""

from typing import Dict, Any, List

from ...utils.config import EngineConfig
from ...data_classes import Position


class PnLCalculator:
    """
    Calculates P&L for trading positions.

    Responsibilities:
    - Calculate unrealized P&L
    - Calculate realized P&L
    - Apply slippage adjustments
    - Generate daily P&L summaries
    """

    def __init__(self, config: EngineConfig):
        self.config = config

    def calculate_unrealized_pnl(
        self,
        position: Position,
        current_spread: float
    ) -> float:
        """
        Calculate unrealized P&L for a position.

        Spread = Buy legs - Sell legs
        Credit spread: entry < 0 (received credit), current < 0 (cost to close)
        Debit spread: entry > 0 (paid debit), current > 0 (value to receive)

        For BOTH cases, P&L = current - entry (how much better/worse the spread is)

        Credit example: entry=-66.77 (got $66.77), current=-74.24 (costs $74.24 to close)
          P&L = -74.24 - (-66.77) = -7.47 -> Loss of $7.47 per contract

        Debit example: entry=+10 (paid $10), current=+15 (worth $15)
          P&L = 15 - 10 = +5 -> Profit of $5 per contract

        Args:
            position: The position to calculate P&L for
            current_spread: Current spread value

        Returns:
            Unrealized P&L in dollars
        """
        spread_change = current_spread - position.entry_spread
        return spread_change * position.contracts * self.config.contract_size

    def calculate_realized_pnl(
        self,
        position: Position,
        exit_spread: float
    ) -> float:
        """
        Calculate realized P&L for a closed position.

        For both credit and debit spreads:
        - spread_change > 0 means profit (spread moved in our favor)
        - spread_change < 0 means loss (spread moved against us)

        Credit spread example:
          Entry: -4.90 (received credit), Exit: -2.00 (cost to close)
          spread_change = -2.00 - (-4.90) = +2.90 (profit - closed cheaper than received)

        Debit spread example:
          Entry: +3.00 (paid debit), Exit: +5.00 (value to receive)
          spread_change = +5.00 - (+3.00) = +2.00 (profit - worth more than paid)

        Args:
            position: The position to calculate P&L for
            exit_spread: Exit spread value (after slippage)

        Returns:
            Realized P&L in dollars
        """
        spread_change = exit_spread - position.entry_spread
        return spread_change * position.contracts * self.config.contract_size

    def apply_exit_slippage(
        self,
        exit_spread: float,
        num_legs: int,
        is_credit: bool
    ) -> float:
        """
        Apply slippage on exit - slippage always reduces profit.

        Credit spread: exit_spread is negative (cost to close)
          Slippage makes it more negative (costs more) = less profit

        Debit spread: exit_spread is positive (value received)
          Slippage makes it less positive (get less) = less profit

        Args:
            exit_spread: Exit spread before slippage
            num_legs: Number of legs in the spread
            is_credit: Whether this is a credit spread

        Returns:
            Exit spread after slippage
        """
        slippage = self.config.slippage_per_leg * num_legs
        # Both credit and debit: subtract slippage reduces our profit
        return exit_spread - slippage

    def calculate_target_spread(
        self,
        entry_spread: float,
        is_credit: bool
    ) -> float:
        """
        Calculate target spread for profit taking.

        Args:
            entry_spread: Entry spread value
            is_credit: Whether this is a credit spread

        Returns:
            Target spread value
        """
        if is_credit:
            return entry_spread * (1 - self.config.credit_target_factor)
        else:
            return entry_spread * self.config.debit_target_factor

    def calculate_stop_spread(
        self,
        entry_spread: float,
        is_credit: bool
    ) -> float:
        """
        Calculate stop spread for loss cutting.

        Args:
            entry_spread: Entry spread value
            is_credit: Whether this is a credit spread

        Returns:
            Stop spread value
        """
        if is_credit:
            return entry_spread * (1 + self.config.credit_stop_factor)
        else:
            return entry_spread * self.config.debit_stop_factor

    def get_daily_summary(
        self,
        closed_positions: List[Position]
    ) -> Dict[str, Any]:
        """
        Get summary of today's trading P&L.

        Args:
            closed_positions: List of closed positions

        Returns:
            Dictionary with P&L statistics
        """
        total_pnl = sum(p.realized_pnl for p in closed_positions)
        winning = [p for p in closed_positions if p.realized_pnl > 0]
        losing = [p for p in closed_positions if p.realized_pnl < 0]

        return {
            "total_trades": len(closed_positions),
            "winning_trades": len(winning),
            "losing_trades": len(losing),
            "total_pnl": total_pnl,
            "avg_win": sum(p.realized_pnl for p in winning) / len(winning) if winning else 0,
            "avg_loss": sum(p.realized_pnl for p in losing) / len(losing) if losing else 0,
            "largest_win": max((p.realized_pnl for p in winning), default=0),
            "largest_loss": min((p.realized_pnl for p in losing), default=0),
            "win_rate": len(winning) / len(closed_positions) * 100 if closed_positions else 0
        }

    def get_pnl_per_contract(self, position: Position) -> float:
        """
        Get P&L per contract for a position.

        Args:
            position: The position

        Returns:
            P&L per contract
        """
        if position.contracts > 0:
            return position.realized_pnl / position.contracts
        return 0.0
