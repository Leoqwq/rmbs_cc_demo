from tee.engine.loader import DealLoader
from tee.engine.state import DealState
from tee.engine.compute import ExpressionEngine
from tee.engine.waterfall import WaterfallRunner
from tee.sample_deal import SAMPLE_DEAL


def test_sample_deal_runs_and_matches_known_numbers():
    deal_def = DealLoader().load_from_json(SAMPLE_DEAL)
    state = DealState(deal_def)
    state.deposit_funds("IAF", 500000.0)
    state.deposit_funds("PAF", 1000000.0)
    WaterfallRunner(ExpressionEngine()).run_period(state)

    assert round(state.bonds["ClassA"].current_balance, 2) == 79000000.00
    assert round(state.bonds["ClassB"].current_balance, 2) == 15000000.00
    assert round(state.bonds["ClassC"].current_balance, 2) == 5000000.00
    assert state.bonds["ClassA"].interest_shortfall == 0
    assert state.bonds["ClassB"].interest_shortfall == 0
    assert state.bonds["ClassC"].interest_shortfall == 0
    assert round(state.cash_balances["IAF"], 2) == 70833.33
    assert round(state.cash_balances["PAF"], 2) == 0.0
