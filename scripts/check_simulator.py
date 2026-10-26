"""Exercise a separately launched Linux simulator on an unused port.

Usage (Linux/WSL): python3 scripts/check_simulator.py artifacts/simulator/simulation
Does not connect to or reset the application's simulator.
"""
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path


def main(binary):
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    proc = subprocess.Popen([str(Path(binary).resolve())], env={**os.environ, "SCRAM_SIM_PORT": str(port)},
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 5
        while True:
            try:
                conn = socket.create_connection(("127.0.0.1", port), timeout=1)
                break
            except OSError:
                if proc.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("isolated simulator failed to start")
                time.sleep(.05)
        with conn, conn.makefile("rwb", buffering=0) as stream:
            conn.sendall(b'{"request":')
            time.sleep(.05)
            conn.sendall(b'"read"}\n')
            first = json.loads(stream.readline())
            assert 2690 < first["outputs"]["pressure"] < 2710
            assert .38 < first["state"]["purge_valve_pos"] < .41
            conn.sendall(b'{"request":"read"}\n{"request":"read"}\n')
            assert "outputs" in json.loads(stream.readline())
            assert "outputs" in json.loads(stream.readline())
            for frame in (b'[]\n', b'garbage\n', b'{"request":"read"} junk\n',
                          b'{"request":"write","data":{"inputs":{"f1_valve_sp":999}}}\n'):
                conn.sendall(frame)
                assert "error" in json.loads(stream.readline())
            conn.sendall(b'{"request":"write","data":{"inputs":{"e_stop":1}}}\n')
            stopped = json.loads(stream.readline())
            assert stopped["state"]["e_stop"]
            assert stopped["state"]["f1_valve_pos"] == 0
            conn.sendall(b'x' * 65537)
            try:
                assert conn.recv(1) == b''
            except ConnectionResetError:
                pass
        print(json.dumps({"result": "passed", "checks": ["fragmented frames", "coalesced frames", "malformed JSON",
              "invalid input range", "correct initial purge", "emergency stop", "bounded frame size"],
              "port": port}, indent=2))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


if __name__ == "__main__":
    main(sys.argv[1])
