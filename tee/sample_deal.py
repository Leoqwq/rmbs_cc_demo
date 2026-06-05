"""Built-in sample RMBS deal for the confidential-compute demo.

Copied verbatim from rmbs_platform/unit_tests/test_waterfall.py
(basic_sequential_deal): 3 sequential tranches A/B/C, $100M collateral,
a servicing fee, fixed-rate interest, sequential principal. No triggers,
no Net WAC, loss allocation defined but not exercised.
"""

SAMPLE_DEAL = {
    "meta": {
        "deal_id": "TEST_SEQ_2024",
        "deal_name": "Sequential Test Deal",
        "asset_type": "RMBS",
        "version": "1.0",
    },
    "dates": {
        "cutoff_date": "2024-01-01",
        "closing_date": "2024-01-30",
        "first_payment_date": "2024-02-25",
        "maturity_date": "2054-01-01",
        "payment_frequency": "MONTHLY",
        "day_count": "30_360",
    },
    "collateral": {
        "original_balance": 100_000_000.0,
        "current_balance": 100_000_000.0,
        "wac": 0.065,
        "wam": 348,
    },
    "funds": [
        {"id": "IAF", "description": "Interest Available Funds"},
        {"id": "PAF", "description": "Principal Available Funds"},
    ],
    "accounts": [
        {"id": "RESERVE", "type": "RESERVE", "target_rule": "500000.0"},
    ],
    "variables": {
        "ServicingFee": "collateral.current_balance * 0.0025 / 12",
        "ClassA_Int": "bonds.ClassA.balance * 0.045 / 12",
        "ClassB_Int": "bonds.ClassB.balance * 0.060 / 12",
        "ClassC_Int": "bonds.ClassC.balance * 0.080 / 12",
    },
    "tests": [],
    "bonds": [
        {
            "id": "ClassA",
            "type": "NOTE",
            "original_balance": 80_000_000.0,
            "priority": {"interest": 1, "principal": 1},
            "coupon": {"kind": "FIXED", "fixed_rate": 0.045},
        },
        {
            "id": "ClassB",
            "type": "NOTE",
            "original_balance": 15_000_000.0,
            "priority": {"interest": 2, "principal": 2},
            "coupon": {"kind": "FIXED", "fixed_rate": 0.060},
        },
        {
            "id": "ClassC",
            "type": "NOTE",
            "original_balance": 5_000_000.0,
            "priority": {"interest": 3, "principal": 3},
            "coupon": {"kind": "FIXED", "fixed_rate": 0.080},
        },
    ],
    "waterfalls": {
        "interest": {
            "steps": [
                {"id": "1", "action": "PAY_FEE", "from_fund": "IAF", "amount_rule": "ServicingFee"},
                {"id": "2", "action": "PAY_BOND_INTEREST", "from_fund": "IAF", "group": "ClassA", "amount_rule": "ClassA_Int"},
                {"id": "3", "action": "PAY_BOND_INTEREST", "from_fund": "IAF", "group": "ClassB", "amount_rule": "ClassB_Int"},
                {"id": "4", "action": "PAY_BOND_INTEREST", "from_fund": "IAF", "group": "ClassC", "amount_rule": "ClassC_Int"},
            ],
        },
        "principal": {
            "steps": [
                {"id": "1", "action": "PAY_BOND_PRINCIPAL", "from_fund": "PAF", "group": "ClassA", "amount_rule": "ALL"},
                {"id": "2", "action": "PAY_BOND_PRINCIPAL", "from_fund": "PAF", "group": "ClassB", "amount_rule": "ALL"},
                {"id": "3", "action": "PAY_BOND_PRINCIPAL", "from_fund": "PAF", "group": "ClassC", "amount_rule": "ALL"},
            ],
        },
        "loss_allocation": {
            "loss_source_rule": "variables.RealizedLoss",
            "write_down_order": ["ClassC", "ClassB", "ClassA"],
        },
    },
}
