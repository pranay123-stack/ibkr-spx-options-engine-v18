"""
Risk Calculator - Position Sizing and Risk Calculations

Handles:
- Target and stop level calculations
- Position sizing based on capital limits
- Risk/reward ratio calculations
- Position validation

Author: client Options Trading Engine
"""

from typing import Optional, Dict, Any, Tuple

from ..entry_management import BuiltStrategy
from ...utils.logging import TradingLogger
from ...utils.config import EngineConfig
from ...data_classes import RiskMetrics


class RiskCalculator:
    """
    Calculates risk parameters for positions.

    Responsibilities:
    - Calculate target and stop levels
    - Determine position size based on capital limits
    - Calculate risk/reward ratios
    - Validate positions against risk limits
    """

    def __init__(
        self,
        config: EngineConfig,
        logger: TradingLogger
    ):
        self.config = config
        self.logger = logger

    def calculate_risk_metrics(
        self,
        built_strategy: BuiltStrategy
    ) -> RiskMetrics:
        """
        Calculate complete risk parameters for a strategy.

        Position sizing is based on risk_per_trade parameter:
        - Contracts = risk_per_trade / max_loss_per_contract
        - Capped at max_contracts and max_cap_to_be_used
        """
        entry_spread = built_strategy.entry_spread
        is_credit = built_strategy.is_credit

        # Calculate target and stop
        target_spread, stop_spread = self._calculate_target_stop(
            entry_spread, is_credit
        )

        # TARGET & STOP explanation is now shown at startup via log_all_explanations()

        # Log actual values for this trade
        self.logger.info("-" * 50)
        self.logger.info("[TARGET & STOP - ACTUAL VALUES]")
        self.logger.info(f"   Type: {'CREDIT' if is_credit else 'DEBIT'} SPREAD")
        self.logger.info(f"   Entry Spread: ${abs(entry_spread):.2f}")
        self.logger.info(f"   ")
        if is_credit:
            self.logger.info(f"   TARGET = {self.config.credit_target_factor * 100:.0f}% of ${abs(entry_spread):.2f} = ${abs(target_spread):.2f}")
            self.logger.info(f"   STOP = {self.config.credit_stop_factor * 100:.0f}% of ${abs(entry_spread):.2f} = ${abs(stop_spread):.2f}")
        else:
            self.logger.info(f"   TARGET = {self.config.debit_target_factor * 100:.0f}% of ${abs(entry_spread):.2f} = ${abs(target_spread):.2f}")
            self.logger.info(f"   STOP = {self.config.debit_stop_factor * 100:.0f}% of ${abs(entry_spread):.2f} = ${abs(stop_spread):.2f}")
        self.logger.info("-" * 50)

        # Calculate max profit/loss per contract
        max_profit, max_loss = self._calculate_max_pnl(
            built_strategy, entry_spread, target_spread, stop_spread, is_credit
        )

        # Calculate risk/reward ratio
        rr_ratio = abs(max_profit / max_loss) if max_loss != 0 else 0

        # Calculate position size based on risk_per_trade
        contracts = self._calculate_position_size(max_loss)

        # Calculate intermediate values for logging (same logic as _calculate_position_size)
        if max_loss > 0:
            contracts_by_risk = int(self.config.risk_per_trade / max_loss)
            contracts_by_capital = int(self.config.max_cap_to_be_used / max_loss)
            min_of_all = min(contracts_by_risk, contracts_by_capital, self.config.max_contracts)
        else:
            contracts_by_risk = 0
            contracts_by_capital = 0
            min_of_all = 0

        # Calculate total risk and reward
        total_risk = max_loss * contracts
        total_reward = max_profit * contracts

        # POSITION SIZING explanation is now shown at startup via log_all_explanations()

        self.logger.info("-" * 50)
        self.logger.info("[POSITION SIZING - ACTUAL VALUES]")
        self.logger.info(f"   Max Loss per Contract: ${max_loss:.2f}")
        self.logger.info(f"   Max Profit per Contract: ${max_profit:.2f}")
        self.logger.info(f"   ")
        self.logger.info(f"   Step 1: By Risk Limit")
        self.logger.info(f"      {self.config.risk_per_trade:.0f} / {max_loss:.2f} = {contracts_by_risk} contracts")
        self.logger.info(f"   Step 2: By Capital Limit")
        self.logger.info(f"      {self.config.max_cap_to_be_used:.0f} / {max_loss:.2f} = {contracts_by_capital} contracts")
        self.logger.info(f"   Step 3: Max Contracts Limit = {self.config.max_contracts}")
        self.logger.info(f"   ")
        self.logger.info(f"   Step 4: MIN({contracts_by_risk}, {contracts_by_capital}, {self.config.max_contracts}) = {min_of_all}")
        self.logger.info(f"   Step 5: MAX({min_of_all}, 1) = {contracts} contracts (minimum 1)")
        self.logger.info(f"   ")
        self.logger.info(f"   Total Position Risk: ${total_risk:.2f}")
        self.logger.info(f"   Total Position Reward: ${total_reward:.2f}")
        self.logger.info(f"   ")
        self.logger.info(f"   Risk/Reward Ratio:")
        self.logger.info(f"      {max_profit:.2f} / {max_loss:.2f} = {rr_ratio:.2f}")
        self.logger.info(f"      Min Required: {self.config.min_risk_reward_ratio:.2f}")
        rr_status = "PASS" if rr_ratio >= self.config.min_risk_reward_ratio else "FAIL"
        self.logger.info(f"      Status: {rr_status} ({rr_ratio:.2f} >= {self.config.min_risk_reward_ratio:.2f})")
        self.logger.info("-" * 50)

        return RiskMetrics(
            entry_spread=entry_spread,
            target_spread=target_spread,
            stop_spread=stop_spread,
            max_loss_per_contract=max_loss,
            max_profit_per_contract=max_profit,
            risk_reward_ratio=rr_ratio,
            contracts=contracts,
            total_risk=total_risk,
            max_risk=total_risk,
            max_reward=total_reward,
            is_credit=is_credit
        )

    def _calculate_target_stop(
        self,
        entry_spread: float,
        is_credit: bool
    ) -> Tuple[float, float]:
        """
        Calculate target and stop spread levels.

        For credit spreads:
        - Entry is negative (receive premium)
        - Target: spread shrinks towards zero (we profit)
        - Stop: spread expands (we lose)

        For debit spreads:
        - Entry is positive (pay premium)
        - Target: spread grows (we profit)
        - Stop: spread shrinks (we lose)
        """
        if is_credit:
            target = entry_spread * (1 - self.config.credit_target_factor)
            stop = entry_spread * (1 + self.config.credit_stop_factor)
        else:
            target = entry_spread * self.config.debit_target_factor
            stop = entry_spread * self.config.debit_stop_factor

        return target, stop

    def _calculate_max_pnl(
        self,
        built_strategy: BuiltStrategy,
        entry_spread: float,
        target_spread: float,
        stop_spread: float,
        is_credit: bool
    ) -> Tuple[float, float]:
        """Calculate maximum profit and loss per contract."""
        if is_credit:
            max_profit = abs(entry_spread) * self.config.contract_size
            max_loss = built_strategy.get_max_loss()

            if max_loss is None:
                max_loss = abs(stop_spread - entry_spread) * self.config.contract_size
            else:
                max_loss = max_loss * self.config.contract_size
        else:
            max_profit = (target_spread - entry_spread) * self.config.contract_size
            max_loss = entry_spread * self.config.contract_size

        return max_profit, max_loss

    def _calculate_position_size(self, max_loss_per_contract: float) -> int:
        """
        Calculate number of contracts based on risk_per_trade.

        Position sizing logic:
        1. contracts = risk_per_trade / max_loss_per_contract
        2. Cap at max_contracts limit
        3. Cap at max_cap_to_be_used / max_loss_per_contract
        4. Minimum 1 contract
        """
        if max_loss_per_contract <= 0:
            return 1

        contracts_by_risk = int(self.config.risk_per_trade / max_loss_per_contract)
        contracts_by_capital = int(self.config.max_cap_to_be_used / max_loss_per_contract)

        contracts = min(
            contracts_by_risk,
            contracts_by_capital,
            self.config.max_contracts
        )

        return max(1, contracts)

    def validate_position(
        self,
        risk_metrics: RiskMetrics,
        account_balance: Optional[float] = None
    ) -> Tuple[bool, str]:
        """Validate position against risk limits."""
        # Validate contracts
        if risk_metrics.contracts <= 0:
            return False, f"Invalid contract count: {risk_metrics.contracts}"

        min_contracts = getattr(self.config, 'min_contracts', 1)
        max_contracts = getattr(self.config, 'max_contracts', 10)

        if risk_metrics.contracts < min_contracts:
            return False, f"Contract count ({risk_metrics.contracts}) below minimum ({min_contracts})"

        if risk_metrics.contracts > max_contracts:
            return False, f"Contract count ({risk_metrics.contracts}) exceeds maximum ({max_contracts})"

        # Validate risk/reward ratio
        min_risk_reward = self.config.min_risk_reward_ratio  # From parameters.csv
        if risk_metrics.risk_reward_ratio < min_risk_reward:
            return False, f"Risk/reward ratio too low: {risk_metrics.risk_reward_ratio:.2f}"

        if risk_metrics.total_risk > self.config.max_cap_to_be_used:
            return False, f"Total risk ({risk_metrics.total_risk:.2f}) exceeds limit"

        if account_balance is not None:
            max_risk_pct = 0.10
            if risk_metrics.total_risk > account_balance * max_risk_pct:
                return False, f"Risk exceeds {max_risk_pct*100}% of account balance"

        if risk_metrics.entry_spread == 0:
            return False, "Entry spread is zero"

        return True, "Position validated"

    def get_margin_estimate(self, built_strategy: BuiltStrategy) -> float:
        """Estimate margin requirement for the strategy."""
        max_loss = built_strategy.get_max_loss()

        if max_loss is None:
            max_loss = abs(built_strategy.entry_spread)

        return max_loss * self.config.contract_size

    def calculate_break_even(self, risk_metrics: RiskMetrics) -> float:
        """Calculate break-even spread value."""
        return risk_metrics.entry_spread

    def get_risk_summary(self, risk_metrics: RiskMetrics) -> Dict[str, Any]:
        """Get formatted risk summary for logging."""
        return {
            "entry_spread": f"{risk_metrics.entry_spread:.4f}",
            "target_spread": f"{risk_metrics.target_spread:.4f}",
            "stop_spread": f"{risk_metrics.stop_spread:.4f}",
            "max_profit_per_contract": f"${risk_metrics.max_profit_per_contract:.2f}",
            "max_loss_per_contract": f"${risk_metrics.max_loss_per_contract:.2f}",
            "risk_reward_ratio": f"{risk_metrics.risk_reward_ratio:.2f}",
            "contracts": risk_metrics.contracts,
            "total_risk": f"${risk_metrics.total_risk:.2f}",
            "is_credit": risk_metrics.is_credit
        }
