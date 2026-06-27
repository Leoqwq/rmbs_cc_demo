# tests/test_run_oracle_agents.py
import pytest
import run_oracle_agents as roa


def test_parse_oracle_keys_splits_and_strips():
    assert roa.parse_oracle_keys({"ORACLE_KEYS": "0x1, 0x2 ,0x3"}) == ["0x1", "0x2", "0x3"]


def test_parse_oracle_keys_empty_raises():
    with pytest.raises(SystemExit):
        roa.parse_oracle_keys({"ORACLE_KEYS": "  "})


def test_build_commands_assigns_ids_and_keys():
    cmds = roa.build_commands(["0xa", "0xb"], python="python3", script="oracle_agent.py")
    assert len(cmds) == 2
    assert cmds[0][0] == {"ORACLE_ID": "1", "ORACLE_KEY": "0xa"}
    assert cmds[1][0] == {"ORACLE_ID": "2", "ORACLE_KEY": "0xb"}
    assert cmds[0][1] == ["python3", "oracle_agent.py"]
