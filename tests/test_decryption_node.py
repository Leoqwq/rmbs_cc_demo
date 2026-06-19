import json
import os

from fastapi.testclient import TestClient
from umbral import Capsule, CapsuleFrag

from umbral_io import b64d, load_public_state
from tests._umbral_helpers import setup_encrypted_request


def test_reencrypt_returns_verifiable_cfrag():
    s = setup_encrypted_request({"iaf": 1, "paf": 2, "period": 1, "dealId": "D"},
                                shares=3, threshold=2)
    with open(s["state_path"]) as f:
        kfrag_b64 = json.load(f)["kfrags"][0]
    os.environ["KFRAG"] = kfrag_b64

    from decryption_node import app
    client = TestClient(app)
    resp = client.post("/reencrypt", json={"capsule": s["capsule_b64"]})
    assert resp.status_code == 200
    cfrag_b64 = resp.json()["cfrag"]

    state = load_public_state(s["state_path"])
    capsule = Capsule.from_bytes(b64d(s["capsule_b64"]))
    cfrag = CapsuleFrag.from_bytes(b64d(cfrag_b64))
    # Raises if invalid; passing means the node produced a genuine fragment.
    cfrag.verify(capsule, verifying_pk=state["authority_pk"],
                 delegating_pk=state["master_pk"], receiving_pk=state["enclave_pk"])
    del os.environ["KFRAG"]
