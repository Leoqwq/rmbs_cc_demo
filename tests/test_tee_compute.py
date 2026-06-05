import json
from tee.compute import compute_waterfall


def test_compute_waterfall_canonical_inputs():
    result = compute_waterfall(iaf=500000.0, paf=1000000.0, period=1)

    assert result["period"] == 1
    assert result["bonds"]["ClassA"]["current_balance"] == 79000000.00
    assert result["bonds"]["ClassB"]["current_balance"] == 15000000.00
    assert result["bonds"]["ClassC"]["current_balance"] == 5000000.00
    assert result["bonds"]["ClassA"]["interest_shortfall"] == 0.0
    assert result["cash_remaining"]["IAF"] == 70833.33
    assert result["cash_remaining"]["PAF"] == 0.0


def test_compute_waterfall_is_deterministic():
    a = compute_waterfall(iaf=500000.0, paf=1000000.0, period=1)
    b = compute_waterfall(iaf=500000.0, paf=1000000.0, period=1)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
