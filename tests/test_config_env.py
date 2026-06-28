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
    assert not (tmp_path / ce.BACKUP_DIRNAME).exists()  # no backup dir created on no-op


def test_change_writes_backup_and_preserves_other_keys(tmp_path):
    p = write(tmp_path / ".env", "A=1\nB=2\n")
    res = ce.set_keys(p, {"C": "3"})
    assert res["backup"] is not None and os.path.exists(res["backup"])
    assert ce.parse_env(p) == {"A": "1", "B": "2", "C": "3"}


def test_backup_lives_in_backup_dir_with_dated_name(tmp_path):
    import re as _re
    p = write(tmp_path / ".env", "A=1\n")
    res = ce.set_keys(p, {"B": "2"})
    backup = res["backup"]
    # backup is inside <dir>/.env-backups/ and named .env.YYYYMMDD-HHMMSS.bak (local time)
    assert os.path.dirname(backup) == str(tmp_path / ce.BACKUP_DIRNAME)
    assert _re.fullmatch(r"\.env\.\d{8}-\d{6}\.bak", os.path.basename(backup))
    assert (tmp_path / ".env").read_text() != ""  # original captured before overwrite


def test_same_second_backups_do_not_collide(tmp_path, monkeypatch):
    # Freeze the timestamp so two writes in the same second would otherwise collide.
    monkeypatch.setattr(ce.time, "strftime", lambda *_a, **_k: "20260627-120000")
    p = write(tmp_path / ".env", "A=1\n")
    b1 = ce.set_keys(p, {"B": "2"})["backup"]
    b2 = ce.set_keys(p, {"C": "3"})["backup"]
    assert b1 != b2 and os.path.exists(b1) and os.path.exists(b2)
    assert os.path.basename(b2).endswith("-2.bak")



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


def test_value_containing_equals_is_preserved(tmp_path):
    p = write(tmp_path / ".env", "")
    ce.set_keys(p, {"DATABASE_URL": "postgres://u:pass@host/db?ssl=require"})
    assert ce.parse_env(p)["DATABASE_URL"] == "postgres://u:pass@host/db?ssl=require"


def test_append_preserves_comments_and_blank_lines(tmp_path):
    p = write(tmp_path / ".env", "# top\nA=1\n\nB=2\n")
    ce.set_keys(p, {"C": "3"})
    text = (tmp_path / ".env").read_text()
    assert "# top" in text and "\n\n" in text
    assert ce.parse_env(p) == {"A": "1", "B": "2", "C": "3"}


def test_force_replaces_only_last_of_duplicate_keys(tmp_path):
    p = write(tmp_path / ".env", "A=1\nA=2\n")
    ce.set_keys(p, {"A": "9"}, force=True)
    lines = (tmp_path / ".env").read_text().splitlines()
    assert lines == ["A=1", "A=9"]


def test_cli_set_and_merge(tmp_path):
    dst = str(tmp_path / ".env")
    assert ce.main(["set", "--into", dst, "X=1"]) == 0
    assert ce.parse_env(dst)["X"] == "1"
    src = tmp_path / "members.env"
    src.write_text("Y=2\n")
    assert ce.main(["merge", "--from", str(src), "--into", dst]) == 0
    assert ce.parse_env(dst)["Y"] == "2"
