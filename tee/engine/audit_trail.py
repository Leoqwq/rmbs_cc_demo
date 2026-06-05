"""
Audit Trail Framework
=====================

Detailed execution logging for waterfall debugging and compliance.

This module captures step-by-step waterfall execution, enabling:
- **Root Cause Analysis**: Trace why a bond received a specific cashflow
- **Compliance Verification**: Audit trail for SEC/rating agency review
- **Model Validation**: Compare execution between tool versions
- **Web3 Transparency**: Publish execution logs on-chain for investor trust

Example
-------
>>> from engine.audit_trail import AuditTrail
>>> trail = AuditTrail(enabled=True, level="detailed")
>>> runner = WaterfallRunner(engine, audit_trail=trail)
>>> runner.run_period(state)
>>> trail.export_to_json("audit_period_1.json")
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional
from datetime import datetime
from pathlib import Path


@dataclass
class WaterfallStepTrace:
    """
    Trace of a single waterfall step execution.
    
    Captures the complete context of one allocation step, including:
    - Step definition (rule, amount, priority)
    - Pre/post fund balances
    - Evaluated conditions
    - Actual amount transferred
    """
    
    step_id: str
    period: int
    waterfall_type: str  # "interest" or "principal"
    priority: int
    
    # Step definition
    target: str
    rule: str
    condition: Optional[str] = None
    
    # Execution context
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    
    # Pre-execution state
    pre_fund_balance: float = 0.0
    pre_target_balance: float = 0.0
    
    # Condition evaluation
    condition_evaluated: Optional[bool] = None
    condition_expression: Optional[str] = None
    
    # Amount calculation
    requested_amount: float = 0.0
    available_amount: float = 0.0
    allocated_amount: float = 0.0
    shortfall: float = 0.0
    
    # Post-execution state
    post_fund_balance: float = 0.0
    post_target_balance: float = 0.0
    
    # Additional metadata
    variables_used: List[str] = field(default_factory=list)
    flags_checked: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    
    def add_note(self, note: str) -> None:
        """Add a note to this step trace."""
        self.notes.append(note)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


@dataclass
class PeriodTrace:
    """
    Complete trace of one period's execution.
    
    Contains all waterfall steps, variable calculations, and test evaluations
    for a single payment period.
    """
    
    period: int
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    
    # Execution steps
    steps: List[WaterfallStepTrace] = field(default_factory=list)
    
    # Variable calculations
    variables_calculated: Dict[str, float] = field(default_factory=dict)
    
    # Test evaluations
    tests_evaluated: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    
    # Solver metadata (if iterative solver used)
    solver_iterations: int = 0
    solver_converged: bool = True
    solver_tolerance: float = 0.0
    
    # Initial and final state
    initial_bond_balances: Dict[str, float] = field(default_factory=dict)
    final_bond_balances: Dict[str, float] = field(default_factory=dict)
    initial_fund_balances: Dict[str, float] = field(default_factory=dict)
    final_fund_balances: Dict[str, float] = field(default_factory=dict)
    
    # Summary statistics
    total_interest_allocated: float = 0.0
    total_principal_allocated: float = 0.0
    total_losses_allocated: float = 0.0
    
    def add_step(self, step: WaterfallStepTrace) -> None:
        """Add a step trace."""
        self.steps.append(step)
    
    def add_variable(self, name: str, value: float) -> None:
        """Record a variable calculation."""
        self.variables_calculated[name] = value
    
    def add_test(self, test_id: str, passed: bool, value: float, threshold: float) -> None:
        """Record a test evaluation."""
        self.tests_evaluated[test_id] = {
            "passed": passed,
            "value": value,
            "threshold": threshold,
            "timestamp": datetime.utcnow().isoformat()
        }
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "period": self.period,
            "timestamp": self.timestamp,
            "steps": [step.to_dict() for step in self.steps],
            "variables_calculated": self.variables_calculated,
            "tests_evaluated": self.tests_evaluated,
            "solver_iterations": self.solver_iterations,
            "solver_converged": self.solver_converged,
            "solver_tolerance": self.solver_tolerance,
            "initial_bond_balances": self.initial_bond_balances,
            "final_bond_balances": self.final_bond_balances,
            "initial_fund_balances": self.initial_fund_balances,
            "final_fund_balances": self.final_fund_balances,
            "total_interest_allocated": self.total_interest_allocated,
            "total_principal_allocated": self.total_principal_allocated,
            "total_losses_allocated": self.total_losses_allocated
        }


class AuditTrail:
    """
    Comprehensive audit trail for waterfall execution.
    
    Captures detailed execution traces for debugging, validation,
    and compliance purposes.
    
    Parameters
    ----------
    enabled : bool
        Whether to capture audit trail (default: True).
    level : str
        Detail level: "summary", "detailed", or "debug" (default: "detailed").
    max_periods : int, optional
        Maximum periods to retain in memory. Older periods are auto-flushed.
    
    Example
    -------
    >>> trail = AuditTrail(enabled=True, level="detailed")
    >>> trail.start_period(1)
    >>> trail.record_step(step_trace)
    >>> trail.end_period()
    >>> trail.export_to_json("audit.json")
    """
    
    def __init__(
        self,
        enabled: bool = True,
        level: str = "detailed",
        max_periods: Optional[int] = None
    ):
        self.enabled = enabled
        self.level = level
        self.max_periods = max_periods
        
        self.periods: List[PeriodTrace] = []
        self.current_period: Optional[PeriodTrace] = None
        
        self.metadata = {
            "created_at": datetime.utcnow().isoformat(),
            "level": level,
            "version": "1.0"
        }
    
    def start_period(self, period: int, initial_state: Optional[Dict] = None) -> None:
        """
        Start tracing a new period.
        
        Parameters
        ----------
        period : int
            Period number.
        initial_state : dict, optional
            Initial state snapshot (bonds, funds, etc.).
        """
        if not self.enabled:
            return
        
        self.current_period = PeriodTrace(period=period)
        
        if initial_state:
            self.current_period.initial_bond_balances = initial_state.get("bonds", {})
            self.current_period.initial_fund_balances = initial_state.get("funds", {})
    
    def record_step(self, step: WaterfallStepTrace) -> None:
        """Record a waterfall step execution."""
        if not self.enabled or self.current_period is None:
            return
        
        self.current_period.add_step(step)
    
    def record_variable(self, name: str, value: float) -> None:
        """Record a variable calculation."""
        if not self.enabled or self.current_period is None:
            return
        
        self.current_period.add_variable(name, value)
    
    def record_test(self, test_id: str, passed: bool, value: float, threshold: float) -> None:
        """Record a test evaluation."""
        if not self.enabled or self.current_period is None:
            return
        
        self.current_period.add_test(test_id, passed, value, threshold)
    
    def record_solver_result(self, iterations: int, converged: bool, tolerance: float) -> None:
        """Record iterative solver result."""
        if not self.enabled or self.current_period is None:
            return
        
        self.current_period.solver_iterations = iterations
        self.current_period.solver_converged = converged
        self.current_period.solver_tolerance = tolerance
    
    def end_period(self, final_state: Optional[Dict] = None) -> None:
        """
        End the current period trace.
        
        Parameters
        ----------
        final_state : dict, optional
            Final state snapshot (bonds, funds, etc.).
        """
        if not self.enabled or self.current_period is None:
            return
        
        if final_state:
            self.current_period.final_bond_balances = final_state.get("bonds", {})
            self.current_period.final_fund_balances = final_state.get("funds", {})
            
            # Calculate summary statistics
            self.current_period.total_interest_allocated = sum(
                step.allocated_amount
                for step in self.current_period.steps
                if step.waterfall_type == "interest"
            )
            self.current_period.total_principal_allocated = sum(
                step.allocated_amount
                for step in self.current_period.steps
                if step.waterfall_type == "principal"
            )
        
        self.periods.append(self.current_period)
        self.current_period = None
        
        # Auto-flush if max_periods exceeded
        if self.max_periods and len(self.periods) > self.max_periods:
            self.periods.pop(0)
    
    def export_to_json(self, output_path: Path) -> None:
        """
        Export audit trail to JSON file.
        
        Parameters
        ----------
        output_path : Path
            Output file path.
        """
        if not self.enabled:
            return
        
        output = {
            "metadata": self.metadata,
            "periods": [period.to_dict() for period in self.periods]
        }
        
        with open(output_path, "w") as f:
            json.dump(output, f, indent=2)
    
    def export_period_to_json(self, period: int, output_path: Path) -> None:
        """Export a single period to JSON."""
        if not self.enabled:
            return
        
        period_trace = next((p for p in self.periods if p.period == period), None)
        if not period_trace:
            raise ValueError(f"Period {period} not found in audit trail")
        
        output = {
            "metadata": self.metadata,
            "period": period_trace.to_dict()
        }
        
        with open(output_path, "w") as f:
            json.dump(output, f, indent=2)
    
    def get_period_summary(self, period: int) -> str:
        """
        Generate human-readable summary of a period.
        
        Parameters
        ----------
        period : int
            Period number.
        
        Returns
        -------
        str
            Formatted summary.
        """
        period_trace = next((p for p in self.periods if p.period == period), None)
        if not period_trace:
            return f"Period {period} not found"
        
        lines = []
        lines.append(f"Period {period} Summary")
        lines.append("=" * 80)
        lines.append(f"Timestamp: {period_trace.timestamp}")
        lines.append(f"Total Steps: {len(period_trace.steps)}")
        lines.append(f"Interest Allocated: ${period_trace.total_interest_allocated:,.2f}")
        lines.append(f"Principal Allocated: ${period_trace.total_principal_allocated:,.2f}")
        lines.append(f"Losses Allocated: ${period_trace.total_losses_allocated:,.2f}")
        lines.append("")
        
        if period_trace.solver_iterations > 1:
            lines.append("Iterative Solver:")
            lines.append(f"  Iterations: {period_trace.solver_iterations}")
            lines.append(f"  Converged: {'✅' if period_trace.solver_converged else '❌'}")
            lines.append(f"  Final Tolerance: ${period_trace.solver_tolerance:.2f}")
            lines.append("")
        
        lines.append("Bond Balances:")
        for bond_id, final_bal in period_trace.final_bond_balances.items():
            initial_bal = period_trace.initial_bond_balances.get(bond_id, 0)
            change = final_bal - initial_bal
            lines.append(f"  {bond_id}: ${initial_bal:,.0f} → ${final_bal:,.0f} (Δ ${change:,.0f})")
        lines.append("")
        
        if period_trace.tests_evaluated:
            lines.append("Tests:")
            for test_id, test_result in period_trace.tests_evaluated.items():
                status = "✅ PASS" if test_result["passed"] else "❌ FAIL"
                lines.append(f"  {test_id}: {status}")
                lines.append(f"    Value: {test_result['value']:.4f}")
                lines.append(f"    Threshold: {test_result['threshold']:.4f}")
        
        return "\n".join(lines)
    
    def get_step_detail(self, period: int, step_id: str) -> str:
        """
        Get detailed information about a specific step.
        
        Parameters
        ----------
        period : int
            Period number.
        step_id : str
            Step ID.
        
        Returns
        -------
        str
            Formatted step details.
        """
        period_trace = next((p for p in self.periods if p.period == period), None)
        if not period_trace:
            return f"Period {period} not found"
        
        step = next((s for s in period_trace.steps if s.step_id == step_id), None)
        if not step:
            return f"Step {step_id} not found in period {period}"
        
        lines = []
        lines.append(f"Step Detail: {step_id}")
        lines.append("=" * 80)
        lines.append(f"Period: {period}")
        lines.append(f"Type: {step.waterfall_type}")
        lines.append(f"Priority: {step.priority}")
        lines.append(f"Target: {step.target}")
        lines.append(f"Rule: {step.rule}")
        lines.append("")
        
        if step.condition:
            status = "✅" if step.condition_evaluated else "❌"
            lines.append(f"Condition: {step.condition} {status}")
            lines.append("")
        
        lines.append("Amounts:")
        lines.append(f"  Requested: ${step.requested_amount:,.2f}")
        lines.append(f"  Available: ${step.available_amount:,.2f}")
        lines.append(f"  Allocated: ${step.allocated_amount:,.2f}")
        if step.shortfall > 0:
            lines.append(f"  Shortfall: ${step.shortfall:,.2f} ⚠️")
        lines.append("")
        
        lines.append("Balances:")
        lines.append(f"  Fund: ${step.pre_fund_balance:,.2f} → ${step.post_fund_balance:,.2f}")
        lines.append(f"  Target: ${step.pre_target_balance:,.2f} → ${step.post_target_balance:,.2f}")
        lines.append("")
        
        if step.notes:
            lines.append("Notes:")
            for note in step.notes:
                lines.append(f"  - {note}")
        
        return "\n".join(lines)
    
    def clear(self) -> None:
        """Clear all stored traces."""
        self.periods.clear()
        self.current_period = None
