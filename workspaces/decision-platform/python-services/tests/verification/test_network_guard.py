from __future__ import annotations

import socket

import pytest

from app.verification.network_guard import OutboundNetworkDenied, deny_outbound_network


def test_offline_network_sentinel_denies_connect_and_create_connection() -> None:
    with deny_outbound_network():
        with pytest.raises(OutboundNetworkDenied):
            socket.create_connection(("127.0.0.1", 9), timeout=0.01)
        candidate = socket.socket()
        try:
            with pytest.raises(OutboundNetworkDenied):
                candidate.connect(("127.0.0.1", 9))
            with pytest.raises(OutboundNetworkDenied):
                candidate.connect_ex(("127.0.0.1", 9))
        finally:
            candidate.close()
