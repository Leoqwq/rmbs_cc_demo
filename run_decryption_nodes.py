"""Launch N decryption nodes, one per kfrag in kd/umbral_state.json.

  python run_decryption_nodes.py            # all kfrags, ports 5000..
  BASE_PORT=5000 NUM_NODES=3 python run_decryption_nodes.py
"""
import json
import os
import signal
import subprocess

from umbral_io import DEFAULT_STATE

BASE_PORT = int(os.getenv("BASE_PORT", "5000"))


def main():
    with open(DEFAULT_STATE) as f:
        kfrags = json.load(f)["kfrags"]
    n = int(os.getenv("NUM_NODES", len(kfrags)))
    n = min(n, len(kfrags))

    procs = []
    for i in range(n):
        port = BASE_PORT + i
        env = os.environ.copy()
        env["KFRAG"] = kfrags[i]
        cmd = ["uvicorn", "decryption_node:app", "--host", "0.0.0.0", "--port", str(port)]
        print(f"Starting decryption node {i} on port {port}")
        procs.append(subprocess.Popen(cmd, env=env))

    print("PIDs:", [p.pid for p in procs], "— Ctrl+C to stop")

    def _terminate(*_):
        for p in procs:
            p.terminate()

    signal.signal(signal.SIGTERM, _terminate)
    try:
        for p in procs:
            p.wait()
    except KeyboardInterrupt:
        _terminate()


if __name__ == "__main__":
    main()
