#!/usr/bin/env python3
import argparse
import json
import os
import select
import sys
import time
from typing import Any, Dict, Optional


class MCPError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class PTYBridge:
    def __init__(self, path: str):
        self.path = path
        self.fd: Optional[int] = None

    def set_path(self, path: str) -> None:
        self.path = path
        self.close()

    def ensure_open(self) -> None:
        if self.fd is not None:
            return
        try:
            self.fd = os.open(self.path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        except OSError as e:
            raise MCPError(-32001, f"failed to open pty {self.path}: {e}") from e

    def close(self) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None

    def read(self, max_bytes: int, timeout_ms: int) -> bytes:
        self.ensure_open()
        assert self.fd is not None

        deadline = time.monotonic() + max(0, timeout_ms) / 1000.0
        chunks = []
        remaining = max(1, max_bytes)

        while remaining > 0:
            now = time.monotonic()
            wait = max(0.0, deadline - now)
            if wait == 0 and chunks:
                break
            if wait == 0 and not chunks:
                break

            rlist, _, _ = select.select([self.fd], [], [], wait)
            if not rlist:
                break

            try:
                data = os.read(self.fd, remaining)
            except BlockingIOError:
                continue
            except OSError as e:
                raise MCPError(-32002, f"pty read failed: {e}") from e

            if not data:
                break
            chunks.append(data)
            remaining -= len(data)

            if len(data) == 0:
                break

            # Drain quickly if more data is ready.
            rlist2, _, _ = select.select([self.fd], [], [], 0)
            if not rlist2:
                break

        return b"".join(chunks)

    def write(self, data: bytes) -> int:
        self.ensure_open()
        assert self.fd is not None
        try:
            return os.write(self.fd, data)
        except OSError as e:
            raise MCPError(-32003, f"pty write failed: {e}") from e


class MCPServer:
    CONTROL_MAP = {
        "c-c": b"\x03",
        "c-z": b"\x1a",
        "c-d": b"\x04",
        "esc": b"\x1b",
        "tab": b"\x09",
        "enter": b"\r",
        "return": b"\r",
        "lf": b"\n",
        "backspace": b"\x7f",
    }

    def __init__(self, tty_path: str):
        self.bridge = PTYBridge(tty_path)
        self.initialized = False
        self.transport_mode: Optional[str] = None  # "content_length" | "json_line"

    def tool_list(self) -> Dict[str, Any]:
        return {
            "tools": [
                {
                    "name": "tty_status",
                    "description": "Return PTY path and open state.",
                    "inputSchema": {"type": "object", "properties": {}},
                },
                {
                    "name": "tty_set_path",
                    "description": "Set PTY path. Existing fd will be closed and reopened on next action.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                },
                {
                    "name": "tty_read",
                    "description": "Read bytes from PTY.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "max_bytes": {"type": "integer", "default": 4096, "minimum": 1},
                            "timeout_ms": {"type": "integer", "default": 120, "minimum": 0},
                            "encoding": {
                                "type": "string",
                                "enum": ["utf-8", "latin-1", "hex"],
                                "default": "utf-8",
                            },
                        },
                    },
                },
                {
                    "name": "tty_write",
                    "description": "Write text/bytes to PTY.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "data": {"type": "string"},
                            "append_newline": {"type": "boolean", "default": False},
                            "encoding": {
                                "type": "string",
                                "enum": ["utf-8", "hex"],
                                "default": "utf-8",
                            },
                        },
                        "required": ["data"],
                    },
                },
                {
                    "name": "tty_control",
                    "description": "Send a control key like c-c/c-z/c-d/esc/enter/tab/backspace.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "key": {"type": "string"},
                            "repeat": {"type": "integer", "default": 1, "minimum": 1, "maximum": 64},
                        },
                        "required": ["key"],
                    },
                },
            ]
        }

    def _tool_text(self, text: str, is_error: bool = False) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"content": [{"type": "text", "text": text}]}
        if is_error:
            payload["isError"] = True
        return payload

    def call_tool(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        if name == "tty_status":
            status = {
                "path": self.bridge.path,
                "open": self.bridge.fd is not None,
            }
            return self._tool_text(json.dumps(status, ensure_ascii=False))

        if name == "tty_set_path":
            path = args.get("path")
            if not isinstance(path, str) or not path:
                raise MCPError(-32602, "path must be a non-empty string")
            self.bridge.set_path(path)
            return self._tool_text(f"ok: path set to {path}")

        if name == "tty_read":
            max_bytes = int(args.get("max_bytes", 4096))
            timeout_ms = int(args.get("timeout_ms", 120))
            encoding = str(args.get("encoding", "utf-8"))
            raw = self.bridge.read(max_bytes=max_bytes, timeout_ms=timeout_ms)

            if encoding == "hex":
                text = raw.hex()
            elif encoding == "latin-1":
                text = raw.decode("latin-1", errors="replace")
            else:
                text = raw.decode("utf-8", errors="replace")

            out = {
                "bytes": len(raw),
                "data": text,
                "encoding": encoding,
            }
            return self._tool_text(json.dumps(out, ensure_ascii=False))

        if name == "tty_write":
            data = args.get("data")
            if not isinstance(data, str):
                raise MCPError(-32602, "data must be a string")

            append_newline = bool(args.get("append_newline", False))
            encoding = str(args.get("encoding", "utf-8"))
            if encoding == "hex":
                try:
                    payload = bytes.fromhex(data)
                except ValueError as e:
                    raise MCPError(-32602, f"invalid hex data: {e}") from e
            else:
                payload = data.encode("utf-8")

            if append_newline:
                payload += b"\n"

            written = self.bridge.write(payload)
            return self._tool_text(f"ok: wrote {written} bytes")

        if name == "tty_control":
            key = str(args.get("key", "")).strip().lower()
            repeat = int(args.get("repeat", 1))
            if key not in self.CONTROL_MAP:
                allowed = ", ".join(sorted(self.CONTROL_MAP.keys()))
                raise MCPError(-32602, f"unsupported key '{key}', allowed: {allowed}")
            if repeat < 1 or repeat > 64:
                raise MCPError(-32602, "repeat must be within [1, 64]")
            payload = self.CONTROL_MAP[key] * repeat
            written = self.bridge.write(payload)
            return self._tool_text(f"ok: sent {key} x{repeat} ({written} bytes)")

        raise MCPError(-32601, f"unknown tool: {name}")

    def run(self) -> None:
        while True:
            msg = self._read_message()
            if msg is None:
                return
            self._handle_message(msg)

    def _read_exact(self, n: int) -> bytes:
        data = b""
        while len(data) < n:
            chunk = sys.stdin.buffer.read(n - len(data))
            if not chunk:
                raise EOFError
            data += chunk
        return data

    def _read_content_length_message(self, first_header_line: bytes) -> Optional[Dict[str, Any]]:
        headers: Dict[str, str] = {}
        line = first_header_line
        while True:
            if not line:
                return None
            if line in (b"\r\n", b"\n"):
                break
            try:
                key, value = line.decode("utf-8").split(":", 1)
            except ValueError:
                line = sys.stdin.buffer.readline()
                continue
            headers[key.strip().lower()] = value.strip()
            line = sys.stdin.buffer.readline()

        length_s = headers.get("content-length")
        if not length_s:
            return None
        length = int(length_s)
        body = self._read_exact(length)
        self.transport_mode = "content_length"
        return json.loads(body.decode("utf-8"))

    def _read_message(self) -> Optional[Dict[str, Any]]:
        line = sys.stdin.buffer.readline()
        if not line:
            return None

        stripped = line.strip()
        # Some MCP servers/clients use newline-delimited JSON instead of Content-Length framing.
        if stripped.startswith(b"{") or stripped.startswith(b"["):
            self.transport_mode = "json_line"
            return json.loads(stripped.decode("utf-8"))

        return self._read_content_length_message(line)

    def _send(self, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if self.transport_mode == "json_line":
            sys.stdout.buffer.write(body + b"\n")
        else:
            header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
            sys.stdout.buffer.write(header)
            sys.stdout.buffer.write(body)
        sys.stdout.buffer.flush()

    def _reply(self, req_id: Any, result: Optional[Dict[str, Any]] = None, error: Optional[Dict[str, Any]] = None) -> None:
        payload: Dict[str, Any] = {"jsonrpc": "2.0", "id": req_id}
        if error is not None:
            payload["error"] = error
        else:
            payload["result"] = result if result is not None else {}
        self._send(payload)

    def _handle_message(self, msg: Dict[str, Any]) -> None:
        method = msg.get("method")
        req_id = msg.get("id")
        params = msg.get("params") or {}

        if method == "initialize":
            self.initialized = True
            result = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "vmconsole-mcp", "version": "0.1.0"},
            }
            if req_id is not None:
                self._reply(req_id, result=result)
            return

        if method == "notifications/initialized":
            return

        if method == "ping":
            if req_id is not None:
                self._reply(req_id, result={})
            return

        if method == "tools/list":
            if req_id is not None:
                self._reply(req_id, result=self.tool_list())
            return

        if method == "tools/call":
            if req_id is None:
                return
            try:
                name = params.get("name")
                if not isinstance(name, str):
                    raise MCPError(-32602, "tools/call requires string params.name")
                arguments = params.get("arguments") or {}
                if not isinstance(arguments, dict):
                    raise MCPError(-32602, "tools/call requires object params.arguments")
                result = self.call_tool(name, arguments)
                self._reply(req_id, result=result)
            except MCPError as e:
                self._reply(req_id, error={"code": e.code, "message": e.message})
            except Exception as e:
                self._reply(req_id, error={"code": -32099, "message": f"internal error: {e}"})
            return

        if req_id is not None:
            self._reply(req_id, error={"code": -32601, "message": f"method not found: {method}"})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MCP server for interacting with /tmp/vmconsole PTY")
    parser.add_argument("--tty-path", default="/tmp/vmconsole", help="PTY path, default: /tmp/vmconsole")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    server = MCPServer(tty_path=args.tty_path)
    try:
        server.run()
    finally:
        server.bridge.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
