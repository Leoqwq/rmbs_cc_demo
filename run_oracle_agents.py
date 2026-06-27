"""Launch one oracle_agent.py per key in ORACLE_KEYS (mirrors run_decryption_nodes.py).

Collapses the runbook's N agent terminals into one background process group. Each child
gets ORACLE_ID=<position, from 1> and ORACLE_KEY=<key> in its environment; on SIGTERM or
Ctrl-C every child is terminated so `make down` cleans up.

  python run_oracle_agents.py            # one agent per ORACLE_KEYS entry
"""
import os
import signal
import subprocess
import sys

from dotenv import load_dotenv


def parse_oracle_keys(env):
    keys = [k.strip() for k in env.get("ORACLE_KEYS", "").split(",") if k.strip()]
    if not keys:
        raise SystemExit("ORACLE_KEYS is empty — set a comma-separated list in .env")
    return keys


def build_commands(keys, python=None, script="oracle_agent.py"):
    python = python or sys.executable
    return [({"ORACLE_ID": str(i), "ORACLE_KEY": key}, [python, script])
            for i, key in enumerate(keys, start=1)]


def main():
    load_dotenv()
    keys = parse_oracle_keys(os.environ)
    procs = []
    for overrides, cmd in build_commands(keys):
        env = os.environ.copy()
        env.update(overrides)
        print(f"Starting oracle agent {overrides['ORACLE_ID']}")
        procs.append(subprocess.Popen(cmd, env=env))

    def _terminate(*_):
        for p in procs:
            p.terminate()

    signal.signal(signal.SIGTERM, _terminate)
    print("PIDs:", [p.pid for p in procs], "— Ctrl+C / SIGTERM to stop")
    try:
        for p in procs:
            p.wait()
    except KeyboardInterrupt:
        _terminate()


if __name__ == "__main__":
    main()
