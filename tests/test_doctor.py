# tests/test_doctor.py
import doctor


def test_check_env_keys_reports_missing():
    r = doctor.check_env_keys({"CHAIN_ID": "1"}, required=["CHAIN_ID", "TEE_URL"])
    assert r["ok"] is False and "TEE_URL" in r["detail"]


def test_check_env_keys_all_present():
    r = doctor.check_env_keys({"CHAIN_ID": "1", "TEE_URL": "x"}, required=["CHAIN_ID", "TEE_URL"])
    assert r["ok"] is True


def test_check_url_ok_with_injected_getter():
    class _Resp:
        status_code = 200

    r = doctor.check_url("TEE", "http://x/tee_address", get=lambda url, timeout: _Resp())
    assert r["ok"] is True and "200" in r["detail"]


def test_check_url_failure_is_caught():
    def _boom(url, timeout):
        raise OSError("refused")

    r = doctor.check_url("TEE", "http://x", get=_boom)
    assert r["ok"] is False and "unreachable" in r["detail"]


def test_format_report_counts_pass_fail():
    results = [doctor.check("a", True, "ok"), doctor.check("b", False, "nope")]
    out = doctor.format_report(results)
    assert "[OK ] a" in out and "[FAIL] b" in out and "1/2 checks passed" in out


def test_check_url_non_200_is_fail():
    class _Resp:
        status_code = 503

    r = doctor.check_url("TEE", "http://x/tee_address", get=lambda url, timeout: _Resp())
    assert r["ok"] is False and "503" in r["detail"]


def test_check_rpc_configured():
    assert doctor.check_rpc_configured({"RPC_URLS": "http://x"})["ok"] is True
    assert doctor.check_rpc_configured({"RPC_URL": "http://x"})["ok"] is True
    assert doctor.check_rpc_configured({})["ok"] is False


def test_run_all_config_only_skips_runtime_checks():
    env = {"TEE_URL": "http://x", "DECRYPTION_NODE_URLS": "http://y"}
    names = [r["name"] for r in doctor.run_all(env, runtime=False)]
    assert "TEE service" not in names
    assert not any(n.startswith("decryption node") for n in names)
    assert ".env keys" in names and "RPC endpoint" in names


def test_check_python_deps_reports_missing():
    def fake_import(m):
        if m == "umbral":
            raise ImportError("no module umbral")
        return object()
    r = doctor.check_python_deps(["web3", "umbral"], import_fn=fake_import)
    assert r["ok"] is False and "umbral" in r["detail"] and "pip install" in r["detail"]


def test_check_python_deps_all_present():
    r = doctor.check_python_deps(["web3", "requests"], import_fn=lambda m: object())
    assert r["ok"] is True and r["detail"] == "all importable"


class _BlockResp:
    def __init__(self, block_hex):
        self._b = block_hex

    def json(self):
        return {"result": self._b}


def test_check_chain_producing():
    seq = iter(["0x10", "0x12"])  # block number advances
    r = doctor.check_chain("chain", "http://x", post=lambda *a, **k: _BlockResp(next(seq)), delay=0)
    assert r["ok"] is True and "producing" in r["detail"]


def test_check_chain_reachable_but_not_advancing():
    r = doctor.check_chain("chain", "http://x", post=lambda *a, **k: _BlockResp("0x10"), delay=0)
    assert r["ok"] is False and "NOT advancing" in r["detail"]


def test_check_chain_unreachable():
    def boom(*a, **k):
        raise OSError("connection refused")
    r = doctor.check_chain("chain", "http://x", post=boom, delay=0)
    assert r["ok"] is False and "unreachable" in r["detail"]
