# tests/test_demo_record.py
import json

import demo_record as dr

_HASH = bytes.fromhex("24a553a8" + "00" * 28)
_RESULT = '{"bonds":{"ClassA":{"current_balance":79000000.0}},"period":1}'


def test_render_finalized_includes_parsed_result():
    out = dr.render(7, True, 3, 3, _HASH, _RESULT)
    assert "request id : 7" in out
    assert "finalized  : True  (attestations 3/3 DON quorum)" in out
    assert '"ClassA"' in out and "79000000" in out


def test_render_not_finalized_omits_result_body():
    out = dr.render(7, False, 0, 3, _HASH, "")
    assert "finalized  : False  (attestations 0/3 DON quorum)" in out
    assert "result     :" not in out


def test_archive_writes_record_in_out_dir(tmp_path):
    out_dir = str(tmp_path / "demo-results")
    path = dr.archive(out_dir, 7, True, 3, 3, _HASH, _RESULT, iaf=500000, paf=1000000)
    assert path.startswith(out_dir)
    assert path.endswith("-req7.json")
    record = json.loads(open(path, encoding="utf-8").read())
    assert record["request_id"] == 7
    assert record["finalized"] is True
    assert record["attestations"] == 3
    assert record["inputs"] == {"iaf": 500000, "paf": 1000000}
    assert record["result"]["bonds"]["ClassA"]["current_balance"] == 79000000.0
    assert record["result_hash"].startswith("0x24a553a8")


def test_archive_handles_unfinalized_empty_result(tmp_path):
    out_dir = str(tmp_path / "demo-results")
    path = dr.archive(out_dir, 9, False, 0, 3, _HASH, "")
    record = json.loads(open(path, encoding="utf-8").read())
    assert record["finalized"] is False and record["result"] is None
