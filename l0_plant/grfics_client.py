"""
L0 low-level client for the GRFICS Tennessee-Eastman simulator.

The simulator (simulation/simulation/main.cc) is a C++ process that listens on
TCP 55555 and speaks line-delimited JSON:

    read :  {"request":"read"}\\n
            -> {"process":..., "outputs":{...}, "state":{...}, "measured":{...}}

    write:  {"request":"write","data":{"inputs":{ <name>: <value>, ... }}}\\n
            -> same state object, post-update

Accepted input names include the four valve setpoints (f1_valve_sp, f2_valve_sp,
purge_valve_sp, product_valve_sp), e_stop, and the fault-injection parameters.
Setpoints are in physical percent [0,100] - the simulator does the 0-65535
scaling internally, so this client stays in engineering units.

This client is deliberately thin: it owns the socket and the JSON framing and
nothing else. Safety logic lives one layer up in safety.py so the interlock is
independent of transport.
"""

from __future__ import annotations

import json
import socket
from typing import Optional


class GrficsClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 55555,
                 timeout: float = 2.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: Optional[socket.socket] = None
        self._f = None

    def connect(self) -> "GrficsClient":
        self._sock = socket.create_connection((self.host, self.port),
                                               timeout=self.timeout)
        # makefile gives us readline() framing for the newline-delimited protocol
        self._f = self._sock.makefile(mode="rwb")
        return self

    def close(self) -> None:
        try:
            if self._f:
                self._f.close()
        finally:
            if self._sock:
                self._sock.close()
            self._sock = None
            self._f = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *exc):
        self.close()

    def _txn(self, msg: dict) -> dict:
        if not self._f:
            raise RuntimeError("GrficsClient not connected; call connect()")
        self._f.write((json.dumps(msg) + "\n").encode())
        self._f.flush()
        line = self._f.readline()
        if not line:
            raise ConnectionError("simulator closed the connection")
        return json.loads(line.decode())

    def read(self) -> dict:
        """Return the raw state object from the simulator."""
        return self._txn({"request": "read"})

    def write_inputs(self, inputs: dict) -> dict:
        """Send one or more named inputs; returns the post-update state."""
        return self._txn({"request": "write", "data": {"inputs": inputs}})
