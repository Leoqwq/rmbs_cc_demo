# tests/test_provision_checks.py
import json
import provision_checks as pc


class _Fn:
    def __init__(self, value):
        self._value = value

    def call(self):
        return self._value


class _Contract:
    """Minimal stand-in for a web3 contract: .functions.NAME().call()."""
    def __init__(self, oracle_count, threshold, tee):
        self._v = {"oracleCount": oracle_count, "threshold": threshold, "teeAddress": tee}

    class _Functions:
        def __init__(self, outer):
            self._outer = outer

        def __getattr__(self, name):
            return lambda: _Fn(self._outer._v[name])

    @property
    def functions(self):
        return _Contract._Functions(self)


def test_contract_provisioned_true_when_all_match():
    c = _Contract(4, 3, "0xAbC0000000000000000000000000000000000001")
    assert pc.contract_provisioned(c, 4, "0xabc0000000000000000000000000000000000001", 3) is True


def test_contract_provisioned_false_on_oracle_count_mismatch():
    c = _Contract(3, 3, "0xAbC0000000000000000000000000000000000001")
    assert pc.contract_provisioned(c, 4, "0xAbC0000000000000000000000000000000000001", 3) is False


def test_contract_provisioned_false_on_tee_mismatch():
    c = _Contract(4, 3, "0xdeadbeef00000000000000000000000000000000")
    assert pc.contract_provisioned(c, 4, "0xabc0000000000000000000000000000000000001", 3) is False


def test_under_funded_lists_only_below_floor():
    class _Eth:
        def get_balance(self, addr):
            return {"0x" + "1" * 40: 10, "0x" + "2" * 40: 0}[addr.lower()]

    class _W3:
        eth = _Eth()

        @staticmethod
        def to_checksum_address(a):
            return a  # identity is fine for the fake

    under = pc.under_funded(_W3(), ["0x" + "1" * 40, "0x" + "2" * 40], floor_wei=5)
    assert under == ["0x" + "2" * 40]


def test_umbral_matches_enclave(tmp_path):
    p = tmp_path / "umbral_state.json"
    p.write_text(json.dumps({"enclave_public_key": "PUBKEY_B64"}))
    assert pc.umbral_matches_enclave(str(p), "PUBKEY_B64") is True
    assert pc.umbral_matches_enclave(str(p), "OTHER") is False
    assert pc.umbral_matches_enclave(str(tmp_path / "missing.json"), "PUBKEY_B64") is False


def test_oracle_keys_present():
    assert pc.oracle_keys_present({"ORACLE_ADDRESSES": "0xa,0xb", "ORACLE_KEYS": "0x1,0x2"}) is True
    assert pc.oracle_keys_present({"ORACLE_ADDRESSES": "0xa,0xb", "ORACLE_KEYS": "0x1"}) is False
    assert pc.oracle_keys_present({"ORACLE_ADDRESSES": "", "ORACLE_KEYS": ""}) is False


def test_contract_provisioned_false_on_threshold_mismatch():
    c = _Contract(4, 2, "0xAbC0000000000000000000000000000000000001")
    assert pc.contract_provisioned(c, 4, "0xAbC0000000000000000000000000000000000001", 3) is False


def test_contract_provisioned_false_on_call_exception():
    class _Broken:
        class functions:
            @staticmethod
            def oracleCount():
                class _R:
                    def call(self_inner):
                        raise OSError("rpc down")
                return _R()
    assert pc.contract_provisioned(_Broken(), 4, "0xabc", 3) is False


def test_oracle_keys_present_false_when_keys_absent():
    assert pc.oracle_keys_present({}) is False
