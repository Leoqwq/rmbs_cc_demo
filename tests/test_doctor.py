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
