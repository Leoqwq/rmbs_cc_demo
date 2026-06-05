"""
Waterfall Execution Engine
==========================

This module provides the waterfall execution logic that allocates collateral
cashflows to bond tranches according to deal rules. The waterfall is the
heart of RMBS deal mechanics, determining how interest and principal are
distributed.

Industry Gap Fixed: Iterative Solver for Circular Dependencies
-------------------------------------------------------------
Real RMBS deals have circular dependencies that a simple top-to-bottom
waterfall cannot handle:

- **Net WAC Cap**: Bond coupon cannot exceed available interest.
  - Formula: Available Interest = Collections - Fees
  - Problem: Some fees are calculated based on Bond Balance.
  - Circularity: Bond Balance → Principal Payment → Interest Payment
    → Net WAC Cap → Fees → Bond Balance

- **Fee Circularity**: Trustee fees based on bond balance, but bond
  balance depends on principal payment, which depends on available
  funds after fees.

The :class:`WaterfallRunner` now includes an **iterative solver** that:
1. Runs the waterfall multiple times until values converge.
2. Handles Net WAC cap calculations.
3. Resolves fee circular dependencies.

Configuration
-------------
- ``max_iterations``: Maximum solver iterations (default: 10)
- ``convergence_tolerance``: Balance difference threshold (default: $0.01)

Example
-------
>>> from rmbs_platform.engine.waterfall import WaterfallRunner
>>> from rmbs_platform.engine.compute import ExpressionEngine
>>> engine = ExpressionEngine()
>>> runner = WaterfallRunner(engine, max_iterations=15, convergence_tol=0.001)
>>> state.deposit_funds("IAF", 50000)
>>> state.deposit_funds("PAF", 100000)
>>> runner.run_period(state)  # Automatically uses iterative solver if needed
>>> print(f"Class A balance: ${state.bonds['A'].current_balance:,.0f}")

See Also
--------
compute.ExpressionEngine : Evaluates waterfall conditions and formulas.
state.DealState : Holds the state mutated by the waterfall.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .compute import ExpressionEngine
from .state import DealState
from .audit_trail import AuditTrail, WaterfallStepTrace

logger = logging.getLogger("RMBS.Waterfall")


@dataclass
class IterationSnapshot:
    """
    Snapshot of deal state for convergence checking.
    
    Attributes
    ----------
    bond_balances : dict
        Mapping of bond_id to current_balance.
    cash_balances : dict
        Mapping of fund_id to balance.
    variables : dict
        Copy of calculated variables.
    """
    bond_balances: Dict[str, float] = field(default_factory=dict)
    cash_balances: Dict[str, float] = field(default_factory=dict)
    variables: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SolverResult:
    """
    Result of iterative waterfall solver.
    
    Attributes
    ----------
    converged : bool
        Whether the solver converged within max_iterations.
    iterations : int
        Number of iterations performed.
    final_tolerance : float
        Maximum balance difference in final iteration.
    circular_dependencies : list
        Detected circular dependencies (for diagnostics).
    """
    converged: bool = False
    iterations: int = 0
    final_tolerance: float = float("inf")
    circular_dependencies: List[str] = field(default_factory=list)


class WaterfallRunner:
    """
    Execute deal tests, variables, and waterfall steps for each period.

    The runner is the main orchestrator of period-by-period deal mechanics.
    It uses the ExpressionEngine to evaluate conditions and formulas,
    and mutates the DealState as cashflows are allocated.

    **NEW: Iterative Solver Support**
    
    For deals with circular dependencies (Net WAC cap, fee circularity),
    the runner can iterate until values converge. Enable with:
    
    - ``use_iterative_solver=True``: Force iterative mode
    - ``max_iterations``: Maximum solver iterations
    - ``convergence_tol``: Balance difference threshold for convergence

    Parameters
    ----------
    engine : ExpressionEngine
        Expression evaluator for waterfall rules and conditions.
    max_iterations : int, default 10
        Maximum iterations for circular dependency resolution.
    convergence_tol : float, default 0.01
        Maximum balance difference to consider converged ($).
    use_iterative_solver : bool, default False
        Force iterative solver even for simple waterfalls.

    Attributes
    ----------
    expr : ExpressionEngine
        Reference to the expression engine.
    max_iterations : int
        Solver iteration limit.
    convergence_tol : float
        Convergence threshold.
    use_iterative_solver : bool
        Whether to use iterative mode.
    last_solver_result : SolverResult or None
        Result from most recent iterative solve.

    Example
    -------
    >>> engine = ExpressionEngine()
    >>> # Simple waterfall (sequential)
    >>> runner = WaterfallRunner(engine)
    >>> runner.run_period(state)
    
    >>> # Complex waterfall with Net WAC cap (iterative)
    >>> runner = WaterfallRunner(engine, use_iterative_solver=True, max_iterations=15)
    >>> runner.run_period(state)
    >>> if runner.last_solver_result and not runner.last_solver_result.converged:
    ...     print("Warning: Waterfall did not converge!")
    """

    def __init__(
        self,
        engine: ExpressionEngine,
        max_iterations: int = 10,
        convergence_tol: float = 0.01,
        use_iterative_solver: bool = False,
        audit_trail: Optional[AuditTrail] = None,
    ) -> None:
        """
        Initialize the waterfall runner with an expression engine.

        Parameters
        ----------
        engine : ExpressionEngine
            Engine for evaluating waterfall rules.
        max_iterations : int, default 10
            Maximum iterations for circular dependency resolution.
        convergence_tol : float, default 0.01
            Maximum balance difference to consider converged.
        use_iterative_solver : bool, default False
            Force iterative solver mode.
        audit_trail : AuditTrail, optional
            Audit trail for capturing execution details.
        """
        self.expr = engine
        self.max_iterations = max_iterations
        self.convergence_tol = convergence_tol
        self.use_iterative_solver = use_iterative_solver
        self.last_solver_result: Optional[SolverResult] = None
        self.audit_trail = audit_trail

    def evaluate_period(self, state: DealState) -> None:
        """
        Evaluate tests and variables for actual periods without paying cashflows.

        This method is used when processing historical (actual) periods where
        we want to evaluate triggers and variables but not execute the
        full waterfall. This preserves tape balances while still computing
        diagnostic values.

        Parameters
        ----------
        state : DealState
            Current deal state to evaluate against.

        Notes
        -----
        This is called when ``apply_waterfall_to_actuals=False`` in the
        simulation configuration. It updates flags and variables but
        does not move cash or pay bonds.

        Example
        -------
        >>> runner.evaluate_period(state)
        >>> triggered = state.flags.get("DelinqTest", False)
        """
        self._run_tests(state)
        self._calculate_variables(state)

    def run_period(self, state: DealState) -> None:
        """
        Run a full period including tests, variables, waterfalls, and losses.

        This is the main entry point for period processing. It executes
        the complete deal mechanics:

        1. Run tests to set trigger flags.
        2. Calculate variables for waterfall conditions.
        3. Execute interest waterfall (with iterative solver if enabled).
        4. Execute principal waterfall.
        5. Allocate losses to subordinate tranches.

        **Iterative Solver**: If ``use_iterative_solver=True`` or the deal
        has Net WAC cap rules, the waterfall runs iteratively until bond
        balances converge. This resolves circular dependencies.

        Parameters
        ----------
        state : DealState
            Current deal state to mutate.

        Notes
        -----
        The method logs the period number for debugging. After execution,
        the state's cash_balances should be mostly depleted (swept to
        bonds or retained per deal rules).

        Example
        -------
        >>> state.deposit_funds("IAF", 50000)
        >>> state.deposit_funds("PAF", 100000)
        >>> runner.run_period(state)
        >>> # Cash has been paid to bonds
        >>> print(f"Remaining IAF: ${state.cash_balances['IAF']:,.2f}")
        >>> # Check if solver converged (if iterative mode)
        >>> if runner.last_solver_result:
        ...     print(f"Converged: {runner.last_solver_result.converged}")
        """
        logger.info(f"--- Running Period {state.period_index + 1} ---")

        # Start audit trail for this period
        if self.audit_trail:
            initial_state = {
                "bonds": {bid: b.current_balance for bid, b in state.bonds.items()},
                "funds": dict(state.cash_balances)
            }
            self.audit_trail.start_period(state.period_index + 1, initial_state)

        # Check if deal has circular dependencies (Net WAC cap, etc.)
        has_circular = self._detect_circular_dependencies(state)
        use_solver = self.use_iterative_solver or has_circular

        if use_solver:
            self._run_period_iterative(state)
        else:
            self._run_period_sequential(state)
        
        # End audit trail for this period
        if self.audit_trail:
            final_state = {
                "bonds": {bid: b.current_balance for bid, b in state.bonds.items()},
                "funds": dict(state.cash_balances)
            }
            self.audit_trail.end_period(final_state)

    def _run_period_sequential(self, state: DealState) -> None:
        """
        Run period with simple top-to-bottom waterfall (no circularity).
        
        This is the original sequential waterfall logic.
        """
        # STEP 1: Run Tests FIRST (So flags are ready for variables)
        self._run_tests(state)

        # STEP 2: Calculate Variables (Now they can read tests.X.failed)
        self._calculate_variables(state)

        # STEP 3: Run Waterfalls
        logger.info("Executing Interest Waterfall")
        self._execute_waterfall(state, "interest")

        logger.info("Executing Principal Waterfall")
        self._execute_waterfall(state, "principal")

        # STEP 4: Allocate Losses
        self._allocate_losses(state)

    def _run_period_iterative(self, state: DealState) -> None:
        """
        Run period with iterative solver for circular dependencies.
        
        This method handles:
        - Net WAC cap calculations
        - Fee circularity (fees based on bond balance)
        - Any variable that depends on downstream values
        
        The solver iterates until bond balances converge within tolerance.
        """
        logger.info("Using iterative solver for circular dependencies")
        
        result = SolverResult()
        prev_snapshot: Optional[IterationSnapshot] = None
        
        for iteration in range(self.max_iterations):
            result.iterations = iteration + 1
            
            # Take snapshot before this iteration
            current_snapshot = self._take_snapshot(state)
            
            # Run standard waterfall logic
            self._run_tests(state)
            self._calculate_variables(state)
            
            # Apply Net WAC cap if configured
            self._apply_net_wac_cap(state)
            
            logger.info(f"Iteration {iteration + 1}: Executing Interest Waterfall")
            self._execute_waterfall(state, "interest")
            
            logger.info(f"Iteration {iteration + 1}: Executing Principal Waterfall")
            self._execute_waterfall(state, "principal")
            
            # Check convergence
            if prev_snapshot is not None:
                max_diff = self._calculate_max_difference(prev_snapshot, current_snapshot)
                result.final_tolerance = max_diff
                
                if max_diff < self.convergence_tol:
                    result.converged = True
                    logger.info(f"Solver converged after {iteration + 1} iterations (diff: ${max_diff:.4f})")
                    break
            
            prev_snapshot = current_snapshot
        
        if not result.converged:
            logger.warning(f"Solver did not converge after {self.max_iterations} iterations (diff: ${result.final_tolerance:.4f})")
        
        # Record solver result in audit trail
        if self.audit_trail:
            self.audit_trail.record_solver_result(
                result.iterations,
                result.converged,
                result.final_tolerance
            )
        
        # Allocate losses (after convergence)
        self._allocate_losses(state)
        
        self.last_solver_result = result

    def _detect_circular_dependencies(self, state: DealState) -> bool:
        """
        Detect if deal has circular dependencies requiring iterative solver.
        
        Checks for:
        - Net WAC cap rules in bond definitions
        - Fee rules that reference bond balances
        - Variables that depend on waterfall outputs
        """
        # Check for Net WAC cap in bonds
        for bond_id, bond in state.bonds.items():
            if hasattr(bond, 'coupon_cap') and bond.coupon_cap:
                return True
            if hasattr(bond, 'net_wac_cap') and bond.net_wac_cap:
                return True
        
        # Check for circular variable definitions
        wf_interest = state.def_.waterfalls.get("interest", {})
        wf_principal = state.def_.waterfalls.get("principal", {})
        
        for step in wf_interest.get("steps", []) + wf_principal.get("steps", []):
            amount_rule = step.get("amount_rule", "")
            # Check if rule references downstream values
            if "bonds." in str(amount_rule) and "balance" in str(amount_rule).lower():
                # Fee or payment depends on bond balance (potential circularity)
                if step.get("action") == "PAY_FEE":
                    return True
        
        # Check for explicit net_wac_cap in deal definition
        if hasattr(state.def_, 'net_wac_cap') and state.def_.net_wac_cap:
            return True
        
        return False

    def _take_snapshot(self, state: DealState) -> IterationSnapshot:
        """Create a snapshot of current state for convergence checking."""
        return IterationSnapshot(
            bond_balances={
                bond_id: bond.current_balance
                for bond_id, bond in state.bonds.items()
            },
            cash_balances=dict(state.cash_balances),
            variables=dict(state.variables),
        )

    def _calculate_max_difference(
        self,
        prev: IterationSnapshot,
        curr: IterationSnapshot,
    ) -> float:
        """Calculate maximum balance difference between snapshots."""
        max_diff = 0.0
        
        # Check bond balances
        for bond_id, prev_bal in prev.bond_balances.items():
            curr_bal = curr.bond_balances.get(bond_id, 0.0)
            diff = abs(curr_bal - prev_bal)
            max_diff = max(max_diff, diff)
        
        # Check cash balances
        for fund_id, prev_bal in prev.cash_balances.items():
            curr_bal = curr.cash_balances.get(fund_id, 0.0)
            diff = abs(curr_bal - prev_bal)
            max_diff = max(max_diff, diff)
        
        return max_diff

    def _apply_net_wac_cap(self, state: DealState) -> None:
        """
        Apply Net WAC cap to bond interest calculations.
        
        The Net WAC cap limits bond coupon to available interest after fees:
        
            Net WAC = (Interest Collections - Senior Fees) / Bond Balance × 12
        
        Where:
            - Interest Collections = Gross interest from collateral
            - Senior Fees = Servicing + Trustee + Other senior fees
            - Bond Balance = Total outstanding bond principal
        
        This prevents bonds from being paid more interest than the
        collateral actually generates after paying senior fees.
        
        The calculated Net WAC is stored in state.variables["NetWAC"] and
        can be referenced by bond interest formulas in the deal spec.
        """
        # Get available interest (gross collections)
        gross_interest = state.cash_balances.get("IAF", 0.0)
        
        if gross_interest <= 0:
            state.set_variable("NetWAC", 0.0)
            return
        
        # Calculate senior fees from variables
        # These are typically calculated in _calculate_variables() before waterfall runs
        senior_fees = 0.0
        
        # Add servicing fee
        servicing_fee = state.get_variable("ServicingFeeAmount") or 0.0
        senior_fees += servicing_fee
        
        # Add trustee fee
        trustee_fee = state.get_variable("TrusteeFeeAmount") or 0.0
        senior_fees += trustee_fee
        
        # Add custodian fee
        custodian_fee = state.get_variable("CustodianFeeAmount") or 0.0
        senior_fees += custodian_fee
        
        # Add any other fees marked as "senior" in the waterfall
        # (fees paid before bond interest)
        for var_name, var_value in state.variables.items():
            if var_name.endswith("FeeAmount") and var_name not in [
                "ServicingFeeAmount", "TrusteeFeeAmount", "CustodianFeeAmount"
            ]:
                # Check if this fee is paid before bond interest in waterfall
                if self._is_senior_fee(var_name, state):
                    senior_fees += (var_value or 0.0)
        
        # Calculate net interest available for bonds
        net_interest = max(0.0, gross_interest - senior_fees)
        
        # Calculate total bond balance
        total_bond_balance = sum(
            bond.current_balance for bond in state.bonds.values()
        )
        
        if total_bond_balance <= 0:
            state.set_variable("NetWAC", 0.0)
            return
        
        # Calculate Net WAC (annualized rate)
        net_wac = (net_interest / total_bond_balance) * 12.0
        
        # Store Net WAC for use by bond interest calculations
        state.set_variable("NetWAC", net_wac)
        
        # Log if Net WAC is significantly different from gross WAC
        if state.get_variable("GrossWAC"):
            gross_wac = state.get_variable("GrossWAC")
            if abs(net_wac - gross_wac) > 0.0010:  # > 10 bps difference
                logger.info(
                    f"Net WAC cap: Gross WAC {gross_wac:.4%} → Net WAC {net_wac:.4%} "
                    f"(Fees: ${senior_fees:,.0f})"
                )
    
    def _is_senior_fee(self, fee_var_name: str, state: DealState) -> bool:
        """
        Determine if a fee is paid before bond interest (i.e., a "senior" fee).
        
        Parameters
        ----------
        fee_var_name : str
            Name of the fee variable (e.g., "AdminFeeAmount")
        state : DealState
            Current deal state
            
        Returns
        -------
        bool
            True if the fee is paid before any PAY_BOND_INTEREST steps
        """
        wf_interest = state.def_.waterfalls.get("interest", {})
        steps = wf_interest.get("steps", [])
        
        # Find position of first PAY_BOND_INTEREST step
        first_bond_interest_pos = None
        for i, step in enumerate(steps):
            if step.get("action") == "PAY_BOND_INTEREST":
                first_bond_interest_pos = i
                break
        
        if first_bond_interest_pos is None:
            # No bond interest steps found, treat as senior
            return True
        
        # Find position of this fee payment
        for i, step in enumerate(steps):
            if step.get("action") == "PAY_FEE":
                amount_rule = step.get("amount_rule", "")
                if amount_rule == fee_var_name:
                    # Fee is senior if it's paid before first bond interest
                    return i < first_bond_interest_pos
        
        # Fee not found in waterfall, conservatively treat as senior
        return True

    def _calculate_variables(self, state: DealState) -> None:
        """
        Compute derived variables used by waterfall steps and tests.

        Variables are defined in the deal specification as expression
        strings. This method evaluates each variable and stores the
        result in state.variables for use by subsequent waterfall steps.

        Parameters
        ----------
        state : DealState
            Current deal state.

        Notes
        -----
        Variables are evaluated in definition order. For deals with
        variable dependencies, the deal spec should order variables
        such that dependencies are calculated first.

        Example
        -------
        >>> # Variables like "OC_Ratio" = "collateral.current_balance / bonds.A.balance"
        >>> runner._calculate_variables(state)
        >>> print(f"OC Ratio: {state.get_variable('OC_Ratio'):.2%}")
        """
        for var_name, rule_str in state.def_.variables.items():
            val = self.expr.evaluate(rule_str, state)
            state.set_variable(var_name, val)
            
            # Record in audit trail
            if self.audit_trail:
                self.audit_trail.record_variable(var_name, val)

    def _run_tests(self, state: DealState) -> None:
        """
        Evaluate deal tests (e.g., triggers) with cure logic to prevent flickering.

        Tests are defined in the deal specification with:
        - A calculation rule for the test value
        - A threshold rule for the comparison
        - A pass condition (VALUE_LT_THRESHOLD, etc.)
        - Optional cure_periods for cure threshold (default: 3)

        The test result is stored in state.flags[test_id] where:
        - True = test FAILED (trigger condition met)
        - False = test PASSED

        **NEW: Cure Logic**
        Instead of directly setting flags, this method now updates TriggerState
        objects that track consecutive periods of breach/cure. This prevents
        "flickering" where triggers alternate between breached and cured.

        Parameters
        ----------
        state : DealState
            Current deal state.

        Notes
        -----
        **Test Types**:
        - ``VALUE_LT_THRESHOLD``: Pass if value < threshold
        - ``VALUE_LEQ_THRESHOLD``: Pass if value <= threshold
        - ``VALUE_GT_THRESHOLD``: Pass if value > threshold
        - ``VALUE_GEQ_THRESHOLD``: Pass if value >= threshold

        **Cure Logic**:
        - A trigger that fails is breached immediately.
        - A breached trigger requires N consecutive passing periods to cure (N=3 default).
        - This prevents oscillation between breach/cure due to minor fluctuations.

        Example
        -------
        >>> # Delinquency test: Pass if delinq < 10%
        >>> runner._run_tests(state)
        >>> if state.flags.get("DelinqTest"):
        ...     print("Delinquency trigger breached!")
        >>> trigger = state.trigger_states.get("DelinqTest")
        >>> print(f"Cure progress: {trigger.months_cured}/{trigger.cure_threshold}")
        """
        for test in state.def_.tests:
            test_id = test["id"]

            # A. Calculate Value & Threshold
            val_rule = test["calc"].get("value_rule", "0")
            thresh_rule = test["threshold"].get("rule", "0")

            val = self.expr.evaluate(val_rule, state)
            thresh = self.expr.evaluate(thresh_rule, state)

            # B. Determine Pass/Fail
            operator = test.get("pass_if", "VALUE_LT_THRESHOLD")
            passed = False

            if operator == "VALUE_LT_THRESHOLD":
                passed = val < thresh
            elif operator == "VALUE_LEQ_THRESHOLD":
                passed = val <= thresh
            elif operator == "VALUE_GT_THRESHOLD":
                passed = val > thresh
            elif operator == "VALUE_GEQ_THRESHOLD":
                passed = val >= thresh

            # C. Update TriggerState with cure logic
            if test_id in state.trigger_states:
                trigger = state.trigger_states[test_id]
                trigger.update(test_passed=passed)
                
                # Set flag based on trigger state (not immediate test result)
                state.flags[test_id] = trigger.is_breached
            else:
                # Fallback: No trigger state (legacy behavior)
                state.flags[test_id] = not passed

            # Record test evaluation in audit trail
            if self.audit_trail:
                self.audit_trail.record_test(test_id, passed, val, thresh)

            # D. Handle explicit effects from schema
            if state.flags[test_id]:  # If trigger is breached
                for effect in test.get("effects", []):
                    if "set_flag" in effect:
                        state.flags[effect["set_flag"]] = True

    def _execute_waterfall(self, state: DealState, waterfall_type: str) -> None:
        """
        Execute one waterfall (interest or principal) for the period.

        The waterfall processes steps sequentially, checking conditions
        and making payments from source funds to targets (bonds, fees,
        or other funds).

        Parameters
        ----------
        state : DealState
            Current deal state.
        waterfall_type : str
            Which waterfall to execute: "interest" or "principal".

        Notes
        -----
        **Step Processing**:

        1. Check condition (skip if False).
        2. Determine available funds in source bucket.
        3. Calculate target payment amount.
        4. Pay the minimum of available and target.
        5. Record any shortfall in the specified ledger.

        **Actions**:
        - ``PAY_BOND_INTEREST``: Pay interest to a bond group.
        - ``PAY_BOND_PRINCIPAL``: Pay principal to a bond group.
        - ``TRANSFER_FUND``: Move cash between funds.
        - ``PAY_FEE``: Pay a fee (cash leaves the structure).

        Example
        -------
        >>> runner._execute_waterfall(state, "interest")
        >>> # Interest has been paid to bonds based on priority
        """
        wf_def = state.def_.waterfalls.get(waterfall_type, {})
        steps = wf_def.get("steps", [])

        for i, step in enumerate(steps):
            # 1. Condition Check
            condition = step.get("condition", "true")
            if not self.expr.evaluate_condition(condition, state):
                continue

            # 2. Source Funds
            source_id = step.get("from_fund")
            available = state.cash_balances.get(source_id, 0.0)

            # 3. Target Amount
            amount_rule = step.get("amount_rule", "0")
            target = 0.0
            if amount_rule in ["ALL", "REMAINING"]:
                target = available
            else:
                target = self.expr.evaluate(amount_rule, state)

            # 4. Payment (capped at available)
            payment = min(available, target)

            if payment > 0.000001:
                action = step.get("action")
                if action == "PAY_BOND_INTEREST":
                    self._pay_bond(state, step, payment, is_prin=False)
                elif action == "PAY_BOND_PRINCIPAL":
                    self._pay_bond(state, step, payment, is_prin=True)
                elif action == "TRANSFER_FUND":
                    state.transfer_cash(source_id, step.get("to"), payment)
                elif action == "PAY_FEE":
                    state.withdraw_cash(source_id, payment)

            # 5. Shortfalls
            shortfall = target - payment
            if shortfall > 0.01 and step.get("unpaid_ledger_id"):
                current = state.ledgers.get(step["unpaid_ledger_id"], 0.0)
                state.set_ledger(step["unpaid_ledger_id"], current + shortfall)

    def _pay_bond(
        self, state: DealState, step: Dict[str, Any], amount: float, is_prin: bool
    ) -> None:
        """
        Route a payment to interest or principal for a bond group.

        Parameters
        ----------
        state : DealState
            Current deal state.
        step : dict
            Waterfall step definition containing group and source.
        amount : float
            Payment amount.
        is_prin : bool
            True for principal payment, False for interest.

        Notes
        -----
        For interest payments, cash is simply withdrawn from the source
        fund (simplified model without detailed interest tracking).
        For principal payments, the bond balance is reduced.
        """
        group = step.get("group")
        source = step.get("from_fund")
        if is_prin:
            state.pay_bond_principal(group, amount, source)
        else:
            # Interest payment - just withdraw cash
            state.withdraw_cash(source, amount)

    def _allocate_losses(self, state: DealState) -> None:
        """
        Allocate realized losses to bonds based on loss allocation rules.

        Losses are typically allocated in reverse seniority order,
        writing down subordinate tranches first before touching
        senior tranches.

        Parameters
        ----------
        state : DealState
            Current deal state with RealizedLoss variable set.

        Notes
        -----
        The write-down order is defined in
        ``state.def_.waterfalls.loss_allocation.write_down_order``.

        Losses are allocated until either:
        - All losses are absorbed.
        - All subordinate tranches are written to zero.

        Example
        -------
        >>> state.set_variable("RealizedLoss", 50000)
        >>> runner._allocate_losses(state)
        >>> # Subordinate tranches have been written down
        """
        loss = state.get_variable("RealizedLoss") or 0.0
        if loss <= 0:
            return

        la_def = state.def_.waterfalls.get("loss_allocation", {})
        for bond_id in la_def.get("write_down_order", []):
            if loss <= 0:
                break
            bond = state.bonds.get(bond_id)
            if bond:
                wd = min(bond.current_balance, loss)
                bond.current_balance -= wd
                loss -= wd
