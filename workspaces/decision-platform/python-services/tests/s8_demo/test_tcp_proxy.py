from __future__ import annotations

import asyncio

import pytest

from app.s8_demo import tcp_proxy


@pytest.mark.asyncio
async def test_proxy_relays_to_the_fixed_upstream(monkeypatch: pytest.MonkeyPatch) -> None:
    async def echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write(await reader.read(1024))
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    upstream = await asyncio.start_server(echo, "127.0.0.1", 0)
    upstream_port = upstream.sockets[0].getsockname()[1]
    monkeypatch.setattr(tcp_proxy, "UPSTREAM_HOST", "127.0.0.1")
    monkeypatch.setattr(tcp_proxy, "UPSTREAM_PORT", upstream_port)
    proxy = await asyncio.start_server(tcp_proxy._handle, "127.0.0.1", 0)
    proxy_port = proxy.sockets[0].getsockname()[1]

    async with upstream, proxy:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
        writer.write(b"bounded-proxy-smoke")
        await writer.drain()
        assert await asyncio.wait_for(reader.read(1024), timeout=2) == b"bounded-proxy-smoke"
        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
async def test_proxy_fails_closed_when_upstream_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    unavailable = await asyncio.start_server(lambda _reader, _writer: None, "127.0.0.1", 0)
    unavailable_port = unavailable.sockets[0].getsockname()[1]
    unavailable.close()
    await unavailable.wait_closed()
    monkeypatch.setattr(tcp_proxy, "UPSTREAM_HOST", "127.0.0.1")
    monkeypatch.setattr(tcp_proxy, "UPSTREAM_PORT", unavailable_port)
    proxy = await asyncio.start_server(tcp_proxy._handle, "127.0.0.1", 0)
    proxy_port = proxy.sockets[0].getsockname()[1]

    async with proxy:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
        assert await asyncio.wait_for(reader.read(1), timeout=2) == b""
        writer.close()
        await writer.wait_closed()
