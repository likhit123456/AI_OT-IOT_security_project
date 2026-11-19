"""Host-wide cooperative writer lease, shared by control room and manual studio.

An exclusive loopback socket is held for the entire writer lifetime. The OS
releases it on process exit, including crashes. No PID files or stale takeover.
This is application coordination, not authentication on the simulator socket;
explicit out-of-band attack scenarios still bypass the cooperative lease.
"""

from __future__ import annotations

import hashlib
import ipaddress
import socket


class PlantOwner:
    def __init__(self, host: str, port: int):
        address = socket.gethostbyname(host)
        if ipaddress.ip_address(address).is_loopback:
            address = "127.0.0.1"
        self.endpoint = f"{address}:{port}"
        digest = hashlib.sha256(self.endpoint.encode()).digest()
        # Hash collisions or unrelated occupied ports fail closed.
        self.lease_port = 20000 + int.from_bytes(digest[:4], "big") % 30000
        self._socket: socket.socket | None = None

    def acquire(self) -> None:
        if self._socket is not None:
            return
        lease = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                lease.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            lease.bind(("127.0.0.1", self.lease_port))
            lease.listen(1)
        except OSError as exc:
            lease.close()
            raise RuntimeError(
                f"Simulator {self.endpoint} already has a writer, or its ownership port "
                f"{self.lease_port} is occupied. Stop the owning application before starting another."
            ) from exc
        self._socket = lease

    def release(self) -> None:
        if self._socket is not None:
            self._socket.close()
            self._socket = None
