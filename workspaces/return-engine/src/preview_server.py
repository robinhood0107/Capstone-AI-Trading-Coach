from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from preview_contract import load_and_verify_preview


class PreviewHandler(BaseHTTPRequestHandler):
    artifact_path: Path

    def _send(self, status: int, payload: object) -> None:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        try:
            artifact = load_and_verify_preview(self.artifact_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self._send(503, {"status": "DOWN", "error": str(exc)})
            return
        if self.path == "/healthz":
            self._send(200, {"status": "UP", "artifact": "LEGACY_RECEIVED_PREVIEW"})
        elif self.path == "/artifact":
            self._send(200, artifact)
        else:
            self._send(404, {"error": "NOT_FOUND"})

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", default="/output/005930.KS.json")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()
    PreviewHandler.artifact_path = Path(args.artifact)
    ThreadingHTTPServer((args.host, args.port), PreviewHandler).serve_forever()


if __name__ == "__main__":
    main()
