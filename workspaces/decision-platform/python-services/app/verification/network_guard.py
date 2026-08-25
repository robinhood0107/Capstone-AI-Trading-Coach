from __future__ import annotations

import socket
from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import patch


class OutboundNetworkDenied(RuntimeError):
    """An offline P1 verification path attempted to open a network connection."""


def _deny(*args: object, **kwargs: object) -> None:
    del args, kwargs
    raise OutboundNetworkDenied("P1 offline verification attempted outbound network access")


@contextmanager
def deny_outbound_network() -> Iterator[None]:
    """Fail an in-process offline verification at the socket boundary."""

    with (
        patch.object(socket.socket, "connect", _deny),
        patch.object(socket.socket, "connect_ex", _deny),
        patch.object(socket, "create_connection", _deny),
    ):
        yield
