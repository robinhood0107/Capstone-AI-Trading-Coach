from __future__ import annotations

import asyncio
import contextlib


LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 8080
UPSTREAM_HOST = "runtime-netns"
UPSTREAM_PORT = 8080
BUFFER_BYTES = 64 * 1024
MAX_CONNECTIONS = 64
IDLE_TIMEOUT_SECONDS = 30.0
_connections = asyncio.Semaphore(MAX_CONNECTIONS)


async def _copy(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while chunk := await asyncio.wait_for(reader.read(BUFFER_BYTES), IDLE_TIMEOUT_SECONDS):
            writer.write(chunk)
            await writer.drain()
    finally:
        writer.close()
        with contextlib.suppress(ConnectionError):
            await writer.wait_closed()


async def _handle(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> None:
    try:
        await asyncio.wait_for(_connections.acquire(), timeout=0.1)
    except TimeoutError:
        client_writer.close()
        with contextlib.suppress(ConnectionError):
            await client_writer.wait_closed()
        return
    try:
        try:
            upstream_reader, upstream_writer = await asyncio.open_connection(
                UPSTREAM_HOST,
                UPSTREAM_PORT,
                limit=BUFFER_BYTES,
            )
        except OSError:
            client_writer.close()
            with contextlib.suppress(ConnectionError):
                await client_writer.wait_closed()
            return
        with contextlib.suppress(TimeoutError, ConnectionError, BrokenPipeError):
            await asyncio.gather(
                _copy(client_reader, upstream_writer),
                _copy(upstream_reader, client_writer),
            )
    finally:
        _connections.release()


async def serve() -> None:
    server = await asyncio.start_server(
        _handle,
        LISTEN_HOST,
        LISTEN_PORT,
        limit=BUFFER_BYTES,
        reuse_address=True,
    )
    async with server:
        await server.serve_forever()


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
