"""
Deal State Management
=====================

This module provides mutable state classes that track the current status
of an RMBS deal during simulation. The state is updated each period as
cashflows are collected, allocated through the waterfall, and paid to bonds.

Key Classes
-----------
- :class:`DealState`: Main state container tracking all deal components.
- :class:`BondState`: Individual tranche balance and shortfall tracking.
- :class:`TriggerState`: Trigger status with cure logic to prevent flickering.
- :class:`Snapshot`: Point-in-time state record for reporting.

The state is initialized from a :class:`DealDefinition` and then mutated
by the :class:`WaterfallRunner` as it processes each period.

Example
-------
>>> from rmbs_platform.engine.loader import DealLoader
>>> from rmbs_platform.engine.state import DealState
>>> loader = DealLoader()
>>> deal_def = loader.load_from_json(deal_json)
>>> state = DealState(deal_def)
>>> print(f"Initial Class A balance: ${state.bonds['A'].current_balance:,.0f}")

See Also
--------
loader.DealDefinition : Immutable deal structure loaded from JSON.
waterfall.WaterfallRunner : Mutates state during waterfall execution.
reporting.ReportGenerator : Creates reports from state snapshots.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Optional

from .loader import DealDefinition

logger = logging.getLogger("RMBS.State")


@dataclass
class BondState:
    """
    Track an individual bond tranche's balances and shortfalls.

    This class maintains the current state of a single tranche including
    its outstanding balance, deferred amounts, and interest shortfalls.
    It is updated by the waterfall engine as payments are made.

    Attributes
    ----------
    original_balance : float
        Par value at deal closing. Used to calculate factor.
    current_balance : float
        Outstanding principal balance. Reduced by principal payments.
    deferred_balance : float
        Principal that has been deferred (for PIK bonds).
    interest_shortfall : float
        Cumulative unpaid interest that may be owed in future periods.

    Example
    -------
    >>> bond = BondState(original_balance=1_000_000, current_balance=950_000)
    >>> print(f"Factor: {bond.factor:.2%}")
    Factor: 95.00%
    """

    original_balance: float
    current_balance: float
    deferred_balance: float = 0.0
    interest_shortfall: float = 0.0

    @property
    def factor(self) -> float:
        """
        Calculate the current factor (current balance / original balance).

        The factor represents what fraction of the original principal
        remains outstanding. A factor of 0.50 means 50% has been paid down.

        Returns
        -------
        float
            Factor between 0.0 (fully paid) and 1.0+ (with deferrals).
            Returns 0.0 if original balance is zero.

        Example
        -------
        >>> bond = BondState(1_000_000, 250_000)
        >>> assert bond.factor == 0.25
        """
        if self.original_balance == 0:
            return 0.0
        return self.current_balance / self.original_balance


@dataclass
class TriggerState:
    """
    Track trigger status with cure logic to prevent flickering.
    
    Real RMBS deals require triggers to remain breached/cured for
    multiple consecutive periods before changing state. This prevents
    "flickering" where a trigger alternates between breached and cured
    due to minor fluctuations in the underlying metrics.
    
    Attributes
    ----------
    trigger_id : str
        Identifier of the trigger being tracked.
    is_breached : bool
        Current breach status. True if trigger is breached.
    months_breached : int
        Consecutive periods the trigger has been breached.
    months_cured : int
        Consecutive periods the trigger has passed while in breached state.
    cure_threshold : int
        Number of consecutive passing periods required to cure a breach.
        Typical value: 3 periods.
    
    Example
    -------
    >>> trigger = TriggerState("DelinqTest", cure_threshold=3)
    >>> trigger.update(test_passed=False)  # Period 1: Fails
    >>> print(f"Breached: {trigger.is_breached}, Months: {trigger.months_breached}")
    Breached: True, Months: 1
    >>> trigger.update(test_passed=True)  # Period 2: Passes (cure count = 1)
    >>> print(f"Breached: {trigger.is_breached}")  # Still breached (need 3 periods)
    Breached: True
    >>> trigger.update(test_passed=True)  # Period 3: Passes (cure count = 2)
    >>> trigger.update(test_passed=True)  # Period 4: Passes (cure count = 3)
    >>> print(f"Breached: {trigger.is_breached}")  # Now cured
    Breached: False
    """
    trigger_id: str
    is_breached: bool = False
    months_breached: int = 0
    months_cured: int = 0
    cure_threshold: int = 3  # Industry standard: 3 consecutive passing periods
    
    def update(self, test_passed: bool) -> None:
        """
        Update trigger state based on current period's test result.
        
        Parameters
        ----------
        test_passed : bool
            Whether the trigger test passed this period.
            
        Notes
        -----
        **Cure Logic**:
        - If test fails: Trigger is breached immediately, cure counter resets.
        - If test passes while breached: Cure counter increments.
        - If cure counter reaches threshold: Trigger is cured, breach counter resets.
        
        **Flickering Prevention**:
        Once breached, a trigger requires `cure_threshold` consecutive
        passing periods to cure. This prevents oscillation between states.
        """
        if test_passed:
            # Test is passing
            if self.is_breached:
                # Currently breached - increment cure counter
                self.months_cured += 1
                if self.months_cured >= self.cure_threshold:
                    # Cure threshold met - trigger is cured
                    self.is_breached = False
                    self.months_breached = 0
                    self.months_cured = 0
                    logger.info(
                        f"Trigger '{self.trigger_id}' cured after "
                        f"{self.cure_threshold} consecutive passing periods"
                    )
            else:
                # Not breached - reset counters
                self.months_cured = 0
        else:
            # Test is failing
            if not self.is_breached:
                # Newly breached
                logger.info(f"Trigger '{self.trigger_id}' breached")
            
            self.is_breached = True
            self.months_breached += 1
            self.months_cured = 0  # Reset cure counter
    
    def to_dict(self) -> Dict[str, Any]:
        """Export trigger state for reporting."""
        return {
            "trigger_id": self.trigger_id,
            "is_breached": self.is_breached,
            "months_breached": self.months_breached,
            "months_cured": self.months_cured,
            "cure_threshold": self.cure_threshold,
        }


@dataclass
class Snapshot:
    """
    Point-in-time record of deal state for reporting.

    A snapshot captures the complete deal state at the end of a period,
    enabling historical analysis and report generation. Snapshots are
    immutable once created.

    Attributes
    ----------
    date : str
        ISO-format date string for the period end.
    period : int
        Period number (1-indexed).
    funds : dict
        Cash bucket balances at period end.
    ledgers : dict
        Ledger values (cumulative loss, shortfalls, etc.).
    bond_balances : dict
        Current balance for each tranche.
    variables : dict
        Computed variables and ML diagnostics.
    flags : dict
        Test pass/fail flags.

    Example
    -------
    >>> snap = state.history[-1]  # Most recent snapshot
    >>> print(f"Period {snap.period}: Class A balance = ${snap.bond_balances['A']:,.0f}")
    """

    date: str
    period: int
    funds: Dict[str, float]
    ledgers: Dict[str, float]
    bond_balances: Dict[str, float]
    variables: Dict[str, Any]
    flags: Dict[str, bool]


class DealState:
    """
    Mutable deal state used by the engine to run waterfalls.

    This class is the central state container for an RMBS simulation.
    It tracks:

    - **Cash balances**: Funds available for distribution (IAF, PAF).
    - **Bond states**: Outstanding balances and shortfalls per tranche.
    - **Ledgers**: Cumulative tracking accounts (losses, DQs).
    - **Variables**: Computed values used by waterfall rules.
    - **Flags**: Test pass/fail indicators for triggers.
    - **History**: Period-by-period snapshots for reporting.

    The state is initialized from a DealDefinition and then mutated
    by depositing cashflows, running waterfalls, and taking snapshots.

    Parameters
    ----------
    definition : DealDefinition
        Validated, immutable deal structure from the loader.

    Attributes
    ----------
    def_ : DealDefinition
        Reference to the immutable deal definition.
    current_date : date or None
        Current simulation date.
    period_index : int
        Current period number (0-indexed during processing).
    cash_balances : dict
        Balance in each cash bucket (fund or account).
    ledgers : dict
        Cumulative tracking values.
    bonds : dict
        BondState for each tranche.
    variables : dict
        Computed and input variables.
    collateral : dict
        Collateral pool attributes (mutable copy).
    flags : dict
        Test pass/fail flags.
    trigger_states : dict
        TriggerState objects with cure logic for each trigger.
    history : list
        List of Snapshot objects, one per completed period.

    Example
    -------
    >>> state = DealState(deal_def)
    >>> state.deposit_funds("IAF", 50000)  # Deposit interest
    >>> state.deposit_funds("PAF", 100000)  # Deposit principal
    >>> runner.run_period(state)  # Execute waterfall
    >>> state.snapshot(current_date)  # Record state
    """

    def __init__(self, definition: DealDefinition) -> None:
        """
        Initialize deal state from a validated deal definition.

        Parameters
        ----------
        definition : DealDefinition
            Immutable deal structure containing bonds, funds, rules.
        """
        self.def_ = definition
        self.current_date: Optional[date] = None
        self.period_index: int = 0
        self.cash_balances: Dict[str, float] = {}
        self.ledgers: Dict[str, float] = {}
        self.bonds: Dict[str, BondState] = {}
        self.variables: Dict[str, Any] = {}
        self.collateral: Dict[str, Any] = dict(definition.collateral)
        self.flags: Dict[str, bool] = {}
        self.trigger_states: Dict[str, TriggerState] = {}
        self.history: List[Snapshot] = []
        self._initialize_t0()

    def _initialize_t0(self) -> None:
        """
        Initialize cash buckets, bond balances, ledgers, and triggers at T=0.

        This method sets up the starting state:
        - All funds start with zero balance
        - All accounts start with zero balance
        - Bonds start at their original balance
        - CumulativeLoss ledger starts at zero
        - Trigger states created with cure logic
        """
        for fund_id in self.def_.funds:
            self.cash_balances[fund_id] = 0.0
        for acc_id, acc_def in self.def_.accounts.items():
            self.cash_balances[acc_id] = acc_def.initial_balance
        for b_id, b_def in self.def_.bonds.items():
            face = b_def.original_balance
            # IO strips: use notional_balance so factor tracks notional amortization
            if b_def.type == "IO" and getattr(b_def, "notional_balance", None):
                face = b_def.notional_balance
            self.bonds[b_id] = BondState(face, face)
        self.ledgers["CumulativeLoss"] = 0.0
        
        # Initialize trigger states with cure logic
        for test in self.def_.tests:
            trigger_id = test["id"]
            # Check for custom cure threshold in test definition
            cure_threshold = test.get("cure_periods", 3)  # Default: 3 periods
            self.trigger_states[trigger_id] = TriggerState(
                trigger_id=trigger_id,
                cure_threshold=cure_threshold
            )

    def deposit_funds(self, fund_id: str, amount: float) -> None:
        """
        Deposit cash into a waterfall fund or account.

        This method adds cashflows to the available balance in a fund.
        Typically called at the start of each period to deposit
        interest and principal collected from the collateral pool.

        Parameters
        ----------
        fund_id : str
            Identifier of the fund or account (e.g., "IAF", "PAF").
        amount : float
            Amount to deposit. Must be non-negative.

        Raises
        ------
        ValueError
            If amount is negative.
        KeyError
            If fund_id does not exist in the deal structure.

        Example
        -------
        >>> state.deposit_funds("IAF", 50000)  # Deposit interest
        >>> state.deposit_funds("PAF", 100000)  # Deposit principal
        """
        if amount < 0:
            raise ValueError(f"Negative deposit: {amount}")
        self._ensure_bucket(fund_id)
        self.cash_balances[fund_id] += amount

    def transfer_cash(self, from_id: str, to_id: str, amount: float) -> None:
        """
        Move cash between internal funds or accounts.

        This method transfers funds within the waterfall, typically
        used for reserve deposits, fee payments, or fund sweeps.

        Parameters
        ----------
        from_id : str
            Source fund or account identifier.
        to_id : str
            Destination fund or account identifier.
        amount : float
            Amount to transfer.

        Raises
        ------
        ValueError
            If source fund has insufficient balance.
        KeyError
            If either fund_id does not exist.

        Example
        -------
        >>> state.transfer_cash("PAF", "Reserve", 10000)
        """
        self._ensure_bucket(from_id)
        self._ensure_bucket(to_id)
        if self.cash_balances[from_id] < (amount - 0.00001):
            raise ValueError(f"Insufficient funds in {from_id}")
        self.cash_balances[from_id] -= amount
        self.cash_balances[to_id] += amount

    def pay_bond_principal(self, bond_id: str, amount: float, source_fund: str) -> None:
        """
        Apply principal cash to a bond balance, capped at remaining balance.

        This method pays down a tranche's principal, reducing its
        outstanding balance. The payment is capped at the bond's
        current balance to prevent over-payment.

        Parameters
        ----------
        bond_id : str
            Tranche identifier (e.g., "A", "B").
        amount : float
            Target payment amount.
        source_fund : str
            Fund to withdraw payment from.

        Notes
        -----
        The actual payment may be less than the requested amount if:
        - The bond's current balance is less than the amount.
        - The payment is capped at the remaining balance.

        Example
        -------
        >>> state.pay_bond_principal("A", 50000, "PAF")
        """
        b_state = self.bonds[bond_id]
        if b_state.current_balance <= 0 or amount <= 0:
            return
        pay_amount = min(amount, b_state.current_balance)
        self.withdraw_cash(source_fund, pay_amount)
        b_state.current_balance = max(0.0, b_state.current_balance - pay_amount)

    def withdraw_cash(self, fund_id: str, amount: float) -> None:
        """
        Withdraw cash from a fund or account.

        This method removes cash from a fund, typically for
        payments to bondholders or fee recipients.

        Parameters
        ----------
        fund_id : str
            Fund or account identifier.
        amount : float
            Amount to withdraw.

        Raises
        ------
        ValueError
            If fund has insufficient balance.
        KeyError
            If fund_id does not exist.
        """
        self._ensure_bucket(fund_id)
        if self.cash_balances[fund_id] < (amount - 0.00001):
            raise ValueError(f"Insufficient funds in {fund_id}")
        self.cash_balances[fund_id] -= amount

    def _ensure_bucket(self, bucket_id: str) -> None:
        """
        Validate that a cash bucket exists in the deal definition.

        Parameters
        ----------
        bucket_id : str
            Fund or account identifier to validate.

        Raises
        ------
        KeyError
            If the bucket does not exist.
        """
        if bucket_id not in self.cash_balances:
            raise KeyError(f"Cash bucket '{bucket_id}' does not exist.")

    def set_variable(self, name: str, value: Any) -> None:
        """
        Set a deal variable used by tests or waterfall formulas.

        Variables are used to pass computed values between different
        parts of the waterfall and to record diagnostic information.

        Parameters
        ----------
        name : str
            Variable name.
        value : Any
            Variable value (typically float, bool, or str).

        Example
        -------
        >>> state.set_variable("RealizedLoss", 5000)
        >>> state.set_variable("DelinqTrigger", True)
        """
        self.variables[name] = value

    def get_variable(self, name: str) -> Any:
        """
        Retrieve a computed deal variable.

        Parameters
        ----------
        name : str
            Variable name to retrieve.

        Returns
        -------
        Any
            Variable value, or None if not set.

        Example
        -------
        >>> loss = state.get_variable("RealizedLoss")
        """
        return self.variables.get(name)

    def set_ledger(self, ledger_id: str, value: float) -> None:
        """
        Set a ledger balance such as cumulative loss or shortfall.

        Ledgers track cumulative values across periods, such as
        total losses or unpaid interest.

        Parameters
        ----------
        ledger_id : str
            Ledger identifier.
        value : float
            New ledger balance.

        Example
        -------
        >>> current_loss = state.ledgers.get("CumulativeLoss", 0)
        >>> state.set_ledger("CumulativeLoss", current_loss + 1000)
        """
        self.ledgers[ledger_id] = value

    def snapshot(self, current_date: date) -> None:
        """
        Record a reporting snapshot after completing a period.

        This method captures the complete deal state at period end
        and appends it to the history. Snapshots are used by the
        ReportGenerator to produce cashflow reports.

        Parameters
        ----------
        current_date : date
            The date for this period's snapshot.

        Notes
        -----
        Calling snapshot increments period_index by 1.

        Example
        -------
        >>> from datetime import date
        >>> state.snapshot(date(2024, 3, 25))
        >>> print(f"Recorded period {state.period_index}")
        """
        self.current_date = current_date
        self.period_index += 1
        snap = Snapshot(
            date=current_date.isoformat(),
            period=self.period_index,
            funds=self.cash_balances.copy(),
            ledgers=self.ledgers.copy(),
            bond_balances={k: v.current_balance for k, v in self.bonds.items()},
            variables=self.variables.copy(),
            flags=self.flags.copy(),
        )
        self.history.append(snap)
