# tests/test_config_env.py
import os
import config_env as ce


def write(path, text):
    path.write_text(text)
    return str(path)


def test_parse_env_last_wins_and_ignores_comments(tmp_path):
    p = write(tmp_path / ".env", "# c\nA=1\nB=2\nA=3\n")
    assert ce.parse_env(p) == {"A": "3", "B": "2"}


def test_set_absent_key_appends(tmp_path):
    p = write(tmp_path / ".env", "A=1\n")
    res = ce.set_keys(p, {"B": "2"})
    assert res["changed"] == ["B"] and res["skipped"] == []
    assert ce.parse_env(p) == {"A": "1", "B": "2"}


def test_set_present_key_skipped_without_force(tmp_path):
    p = write(tmp_path / ".env", "A=1\n")
    res = ce.set_keys(p, {"A": "9"})
    assert res["changed"] == [] and res["skipped"] == ["A"]
    assert ce.parse_env(p)["A"] == "1"  # unchanged


def test_set_present_key_replaced_with_force(tmp_path):
    p = write(tmp_path / ".env", "A=1\nB=2\n")
    res = ce.set_keys(p, {"A": "9"}, force=True)
    assert res["changed"] == ["A"]
    assert ce.parse_env(p) == {"A": "9", "B": "2"}  # B preserved


def test_no_change_writes_no_backup(tmp_path):
    p = write(tmp_path / ".env", "A=1\n")
    res = ce.set_keys(p, {"A": "9"})  # skipped
    assert res["backup"] is None
    assert not list(tmp_path.glob(".env.bak.*"))


def test_change_writes_backup_and_preserves_other_keys(tmp_path):
    p = write(tmp_path / ".env", "A=1\nB=2\n")
    res = ce.set_keys(p, {"C": "3"})
    assert res["backup"] is not None and os.path.exists(res["backup"])
    assert ce.parse_env(p) == {"A": "1", "B": "2", "C": "3"}


def test_merge_file_pulls_keys(tmp_path):
    src = write(tmp_path / "members.env", "X=10\nY=20\n")
    dst = write(tmp_path / ".env", "X=1\n")
    res = ce.merge_file(src, dst, force=True)
    assert ce.parse_env(dst) == {"X": "10", "Y": "20"}
    assert set(res["changed"]) == {"X", "Y"}


def test_merge_into_missing_target_creates_it(tmp_path):
    src = write(tmp_path / "members.env", "X=10\n")
    dst = str(tmp_path / ".env")  # does not exist
    ce.merge_file(src, dst)
    assert ce.parse_env(dst) == {"X": "10"}
