"""
Expression Evaluation Engine
============================

This module provides the rule evaluation engine used to execute deal formulas,
trigger tests, and waterfall conditions. It builds a safe execution context
from the current deal state and evaluates string-based expressions.

The :class:`ExpressionEngine` is central to RMBS deal logic, enabling:

- **Variable Computation**: Calculate derived values like coverage ratios.
- **Trigger Evaluation**: Determine if deal tests pass or fail.
- **Waterfall Conditions**: Gate payment steps based on deal state.

Security Note
-------------
All expressions are evaluated in a restricted namespace with no access
to Python builtins except for a curated set of math functions (MIN, MAX,
ABS, ROUND, SUM, FLOOR, CEIL). This prevents arbitrary code execution
while enabling complex financial calculations.

Example
-------
>>> from rmbs_platform.engine.compute import ExpressionEngine
>>> from rmbs_platform.engine.state import DealState
>>> engine = ExpressionEngine()
>>> # Evaluate a coverage ratio formula
>>> result = engine.evaluate("bonds.A.balance / collateral.current_balance", state)
>>> print(f"Coverage ratio: {result:.2%}")

See Also
--------
state.DealState : Provides the evaluation context.
waterfall.WaterfallRunner : Uses the engine for waterfall execution.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict

from .state import BondState, DealState

logger = logging.getLogger("RMBS.Compute")


class EvaluationError(Exception):
    """
    Exception raised when a waterfall or test expression cannot be evaluated.

    This exception wraps underlying evaluation errors (NameError, TypeError,
    etc.) and provides context about the failing expression.

    Attributes
    ----------
    message : str
        Human-readable error description.

    Example
    -------
    >>> try:
    ...     engine.evaluate("unknown_variable", state)
    ... except EvaluationError as e:
    ...     print(f"Evaluation failed: {e}")
    """

    pass


class ExpressionEngine:
    """
    Evaluate deal formulas against the current deal state.

    The engine builds a business-oriented namespace containing:

    - **funds**: Cash bucket balances (IAF, PAF, reserves).
    - **bonds**: Tranche balances, factors, and shortfalls.
    - **tests**: Trigger test pass/fail status.
    - **ledgers**: Cumulative tracking accounts (losses, shortfalls).
    - **collateral**: Pool attributes (balance, WAC, WAM).
    - **variables**: Deal-defined computed values.

    This namespace allows deal rules to reference business concepts directly:

    - ``funds.IAF`` → Interest Available Fund balance
    - ``bonds.A.balance`` → Class A tranche balance
    - ``tests.OCTest.failed`` → Overcollateralization test failure status
    - ``collateral.current_balance`` → Current pool balance

    Parameters
    ----------
    None

    Attributes
    ----------
    safe_globals : dict
        Restricted global namespace for expression evaluation.
        Contains only safe math functions.

    Notes
    -----
    **Thread Safety**: The engine is stateless and can be safely shared
    across multiple evaluations. The state object provides all context.

    **Performance**: Expression evaluation uses Python's built-in ``eval()``
    with a restricted namespace. For high-frequency evaluation, consider
    pre-compiling expressions or using a dedicated expression library.

    Example
    -------
    >>> engine = ExpressionEngine()
    >>> # Calculate interest payment amount
    >>> amount = engine.evaluate("MIN(funds.IAF, bonds.A.balance * 0.05 / 12)", state)
    """

    def __init__(self) -> None:
        """Initialize the engine with safe built-in functions."""
        self.safe_globals: Dict[str, Any] = {
            "__builtins__": None,
            "MIN": min,
            "MAX": max,
            "ABS": abs,
            "ROUND": round,
            "SUM": sum,
            "FLOOR": math.floor,
            "CEIL": math.ceil,
        }

    def _normalize_expression(self, expression: str) -> str:
        """
        Normalize expression syntax from SQL-like to Python.

        Converts common SQL/Excel-style operators to Python equivalents:
        - AND → and
        - OR → or
        - NOT → not
        - <> → !=
        - TRUE → True
        - FALSE → False

        Parameters
        ----------
        expression : str
            Original rule expression.

        Returns
        -------
        str
            Python-compatible expression.
        """
        import re
        # Replace SQL-style boolean operators with Python equivalents
        # Use word boundaries to avoid replacing substrings
        normalized = expression
        # AND/OR/NOT must be replaced case-insensitively with word boundaries
        normalized = re.sub(r'\bAND\b', 'and', normalized)
        normalized = re.sub(r'\bOR\b', 'or', normalized)
        normalized = re.sub(r'\bNOT\b', 'not', normalized)
        normalized = re.sub(r'\bTRUE\b', 'True', normalized)
        normalized = re.sub(r'\bFALSE\b', 'False', normalized)
        # SQL-style not-equal
        normalized = normalized.replace('<>', '!=')
        return normalized

    def evaluate(self, expression: str, state: DealState) -> Any:
        """
        Evaluate a rule expression in the context of a deal state.

        This method parses and executes a string expression using the
        current deal state to resolve variable references. It supports
        arithmetic operations, comparisons, and the safe built-in functions.

        Parameters
        ----------
        expression : str
            Rule string from the deal definition. Can reference:
            - ``funds.<id>``: Cash bucket balance
            - ``bonds.<id>.balance``: Bond current balance
            - ``bonds.<id>.factor``: Bond factor (current/original)
            - ``bonds.<id>.shortfall``: Bond interest shortfall
            - ``tests.<id>.failed``: Test failure status (boolean)
            - ``ledgers.<id>``: Ledger balance
            - ``collateral.<attr>``: Collateral pool attribute
            - Deal variables by name
        state : DealState
            Current deal state providing evaluation context.

        Returns
        -------
        Any
            The computed value. Type depends on the expression:
            - Numeric expressions return float
            - Comparisons return bool
            - String expressions return str

        Raises
        ------
        EvaluationError
            If the expression references unknown variables or contains
            syntax errors.

        Example
        -------
        >>> # Calculate available funds after reserve
        >>> avail = engine.evaluate("MAX(0, funds.IAF - 10000)", state)
        >>> # Check a trigger condition
        >>> triggered = engine.evaluate("tests.OCTest.failed", state)
        """
        if expression is None or expression == "":
            return 0.0

        # Normalize SQL-like syntax to Python
        expression = self._normalize_expression(expression)

        context = self._build_execution_context(state)

        try:
            result = eval(expression, self.safe_globals, context)
            return result
        except NameError as e:
            logger.error(f"Rule failed: '{expression}'. Unknown variable: {e}")
            raise EvaluationError(f"Unknown variable in rule: {e}")
        except Exception as e:
            logger.error(f"Rule failed: '{expression}'. Error: {e}")
            raise EvaluationError(f"Calculation error: {e}")

    def evaluate_condition(self, rule: str, state: DealState) -> bool:
        """
        Evaluate a boolean condition used in waterfall step gating.

        This is a convenience method for evaluating conditions that gate
        waterfall steps. It handles literal "true"/"false" strings and
        coerces numeric results to boolean.

        Parameters
        ----------
        rule : str
            Condition expression. Can be:
            - "true" / "false": Literal boolean strings
            - Comparison expression: e.g., "funds.IAF > 0"
            - Variable reference: e.g., "DelinqTrigger"
        state : DealState
            Current deal state.

        Returns
        -------
        bool
            True if the condition is satisfied, False otherwise.

        Example
        -------
        >>> # Check if interest fund has balance
        >>> can_pay = engine.evaluate_condition("funds.IAF > 1000", state)
        >>> # Check a named trigger
        >>> triggered = engine.evaluate_condition("tests.DelinqTest.failed", state)
        """
        if str(rule).lower() == "true":
            return True
        if str(rule).lower() == "false":
            return False
        result = self.evaluate(rule, state)
        return bool(result)

    def _build_execution_context(self, state: DealState) -> Dict[str, Any]:
        """
        Construct the evaluation namespace for deal rules and tests.

        This method builds a dictionary of proxy objects that provide
        attribute-style access to deal state components. The proxies
        enable intuitive rule syntax like ``bonds.A.balance``.

        Parameters
        ----------
        state : DealState
            Current deal state to expose in the context.

        Returns
        -------
        dict
            Evaluation namespace containing:
            - ``funds``: FundProxy for cash bucket access
            - ``bonds``: BondProxy for tranche access
            - ``tests``: TestsProxy for trigger status access
            - ``ledgers``: LedgerProxy for ledger access
            - ``collateral``: CollateralProxy for pool access
            - All deal variables by name
            - All fund IDs as top-level names

        Notes
        -----
        The proxy objects use Python's ``__getattr__`` to provide
        dynamic attribute access. Unknown attributes return 0.0
        to prevent evaluation errors on optional fields.
        """
        ctx: Dict[str, Any] = {}

        # 1. Fund Proxy - provides funds.IAF, funds.PAF syntax
        class FundProxy:
            """Proxy for accessing cash bucket balances."""

            def __getattr__(self, key: str) -> float:
                return state.cash_balances.get(key, 0.0)

        ctx["funds"] = FundProxy()
        # Also expose accounts (same backing store as funds)
        ctx["accounts"] = FundProxy()
        # Also expose fund IDs as top-level variables
        for fid, val in state.cash_balances.items():
            ctx[fid] = val

        # 2. Bond Proxy - provides bonds.A.balance, bonds.A.factor syntax
        class BondWrapper:
            """Wrapper exposing bond state attributes."""

            def __init__(self, b_state: BondState) -> None:
                self.balance = b_state.current_balance
                self.factor = b_state.factor
                self.shortfall = b_state.interest_shortfall
                self.original = b_state.original_balance

        class BondProxy:
            """Proxy for accessing bond states by ID."""

            def __getattr__(self, key: str) -> Any:
                if key in state.bonds:
                    return BondWrapper(state.bonds[key])
                return 0.0

        ctx["bonds"] = BondProxy()

        # 2b. Expose bond balances as top-level variables with safe names
        #     so formulas can reference bonds with hyphens (e.g. A-1 → bond_A_1_balance)
        for bond_id, bond_state in state.bonds.items():
            safe = bond_id.replace("-", "_").replace(" ", "_")
            ctx[f"bond_{safe}_balance"] = bond_state.current_balance
            ctx[f"bond_{safe}_original"] = bond_state.original_balance

        # 3. Variables & Ledgers - expose both as top-level AND via variables.X syntax
        for name, val in state.variables.items():
            ctx[name] = val

        class VariablesProxy:
            """Proxy for accessing deal variables via variables.X syntax."""

            def __getattr__(self, key: str) -> Any:
                return state.variables.get(key, 0.0)

        ctx["variables"] = VariablesProxy()

        class LedgerProxy:
            """Proxy for accessing ledger balances."""

            def __getattr__(self, key: str) -> float:
                return state.ledgers.get(key, 0.0)

        ctx["ledgers"] = LedgerProxy()

        # 4. Collateral Proxy - provides collateral.original_balance, etc.
        class CollateralProxy:
            """Proxy for accessing collateral pool attributes."""

            def __getattr__(self, key: str) -> Any:
                return state.collateral.get(key, 0.0)

        ctx["collateral"] = CollateralProxy()

        # 5. Tests Proxy - provides tests.DelinqTest.failed syntax
        class SingleTestWrapper:
            """Wrapper for a single test's pass/fail status."""

            def __init__(self, test_id: str, flags: Dict[str, bool]) -> None:
                self.test_id = test_id
                self.flags = flags

            @property
            def failed(self) -> bool:
                """Return True if the test failed, False if passed or not run."""
                return self.flags.get(self.test_id, False)

        class TestsProxy:
            """Proxy for accessing test pass/fail status by test ID."""

            def __getattr__(self, test_id: str) -> SingleTestWrapper:
                return SingleTestWrapper(test_id, state.flags)

        ctx["tests"] = TestsProxy()

        # 6. Period index — enables date-relative conditions
        #    (e.g. reinvestment period checks)
        ctx["PeriodIndex"] = state.period_index

        return ctx
