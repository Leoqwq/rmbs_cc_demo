"""Pure waterfall computation for the confidential-compute demo.

No I/O, no crypto — given a period's cashflows it loads the built-in deal,
runs one waterfall period, and returns a deterministic result dict. This is
the exact computation the TEE performs and the on-host ground truth for tests.
"""
from typing import Any, Dict

from tee.engine.loader import DealLoader
from tee.engine.state import DealState
from tee.engine.compute import ExpressionEngine
from tee.engine.waterfall import WaterfallRunner
from tee.sample_deal import SAMPLE_DEAL


def compute_waterfall(iaf: float, paf: float, period: int) -> Dict[str, Any]:
    """Run one waterfall period on the built-in deal and return the result.

    Amounts are rounded to 2 decimals so the result serializes to a stable,
    hashable JSON string (the contract signs/stores its keccak hash).
    """
    deal_def = DealLoader().load_from_json(SAMPLE_DEAL)
    state = DealState(deal_def)
    state.deposit_funds("IAF", float(iaf))
    state.deposit_funds("PAF", float(paf))

    WaterfallRunner(ExpressionEngine()).run_period(state)

    bonds = {
        bond_id: {
            "current_balance": round(bond.current_balance, 2),
            "interest_shortfall": round(bond.interest_shortfall, 2),
        }
        for bond_id, bond in sorted(state.bonds.items())
    }
    cash_remaining = {
        fund_id: round(balance, 2)
        for fund_id, balance in sorted(state.cash_balances.items())
    }
    return {"period": int(period), "bonds": bonds, "cash_remaining": cash_remaining}
