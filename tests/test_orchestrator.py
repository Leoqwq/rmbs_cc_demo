"""Offline tests for the robustness helpers (#2 RPC failover, #3 persistence).

The live failover/idempotency against a real chain is exercised in the manual
end-to-end run (plan Task 8); here we cover the pure, chain-free logic.
"""
import pytest

import chain
import orchestrator


# --- #2: RPC endpoint selection / failover ---------------------------------

def test_get_rpc_urls_prefers_multi(monkeypatch):
    monkeypatch.setenv("RPC_URLS", "http://a:8545, http://b:8545 ,http://c:8545")
    monkeypatch.setenv("RPC_URL", "http://single:8545")
    assert chain.get_rpc_urls() == ["http://a:8545", "http://b:8545", "http://c:8545"]


def test_get_rpc_urls_falls_back_to_single(monkeypatch):
    monkeypatch.delenv("RPC_URLS", raising=False)
    monkeypatch.setenv("RPC_URL", "http://single:8545")
    assert chain.get_rpc_urls() == ["http://single:8545"]


def test_get_rpc_urls_raises_when_unset(monkeypatch):
    monkeypatch.delenv("RPC_URLS", raising=False)
    monkeypatch.delenv("RPC_URL", raising=False)
    with pytest.raises(RuntimeError):
        chain.get_rpc_urls()


class _FakeW3:
    def __init__(self, ok):
        self._ok = ok

    def is_connected(self):
        return self._ok


def test_connect_web3_returns_first_reachable(monkeypatch):
    tried = []

    def fake_make(url):
        tried.append(url)
        return _FakeW3(url == "u2")  # only u2 is up

    monkeypatch.setattr(chain, "_make_w3", fake_make)
    w3 = chain.connect_web3(["u1", "u2", "u3"])
    assert w3.is_connected()
    assert tried == ["u1", "u2"]  # stops at the first reachable, never tries u3


def test_connect_web3_raises_when_none_reachable(monkeypatch):
    monkeypatch.setattr(chain, "_make_w3", lambda url: _FakeW3(False))
    with pytest.raises(ConnectionError):
        chain.connect_web3(["u1", "u2"])


# --- #3: orchestrator state persistence ------------------------------------

def test_load_state_defaults_when_missing(tmp_path):
    last_block, completed = orchestrator.load_state(str(tmp_path / "nope.json"))
    assert last_block == 0
    assert completed == set()


def test_save_then_load_state_roundtrip(tmp_path):
    path = str(tmp_path / "state.json")
    orchestrator.save_state(42, {3, 1, 2}, path)
    last_block, completed = orchestrator.load_state(path)
    assert last_block == 42
    assert completed == {1, 2, 3}
