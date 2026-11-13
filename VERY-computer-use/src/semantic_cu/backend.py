"""Bounded synchronous stdio MCP transport. Reconstructed after workspace loss.

Transport is not an authorization system or OS sandbox. Mutations are never
retried; timeout/disconnect after dispatch can mean an unknown desktop outcome.
"""

from __future__ import annotations

import json
import math
import os
import select
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


class BackendError(RuntimeError):
    """Transport failure; an already-sent mutation may have occurred."""


class BackendTimeout(BackendError):
    """Deadline expired. Observe state before considering another action."""


class BackendProtocolError(BackendError):
    """The peer violated the bounded JSON-RPC contract."""


class BackendRemoteError(BackendError):
    """JSON-RPC error, distinct from MCP isError tool results."""

    def __init__(self, error: dict[str, Any]):
        self.error = error
        super().__init__(
            f"MCP request failed (code {error.get('code')}): {error.get('message')}"
        )


DESKTOP_TOOLS = frozenset(
    {
        "doctor",
        "list_apps",
        "list_windows",
        "focused_window",
        "get_app_state",
        "get_tree_page",
        "screenshot",
        "click",
        "drag",
        "scroll",
        "press_key",
        "type_text",
        "perform_action",
        "set_value",
        "activate_window",
        "move_window",
        "resize_window",
    }
)
_SESSION_ENV = frozenset(
    {
        "PATH",
        "VERY_CU_UPSTREAM_BIN",
        "HOME",
        "USER",
        "LOGNAME",
        "LANG",
        "LANGUAGE",
        "TZ",
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "DBUS_SESSION_BUS_ADDRESS",
        "XAUTHORITY",
        "XDG_RUNTIME_DIR",
        "XDG_SESSION_TYPE",
        "XDG_CURRENT_DESKTOP",
        "XDG_SESSION_DESKTOP",
        "XDG_DATA_HOME",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "DESKTOP_SESSION",
        "GNOME_SHELL_SESSION_MODE",
        "KDE_FULL_SESSION",
        "HYPRLAND_INSTANCE_SIGNATURE",
        "SWAYSOCK",
        "I3SOCK",
        "NIRI_SOCKET",
        "YDOTOOL_SOCKET",
    }
)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError(f"Non-JSON numeric constant: {value}")


def _decode_json(raw):
    return json.loads(
        raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant
    )


def tool_result_json(result: Mapping[str, Any]) -> dict[str, Any]:
    """Read structuredContent or screenshot's JSON text caption.

    Preserve the original result for image blocks and isError. ok=true confirms
    dispatch, not the intended UI outcome or successful post-action observation.
    """
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    content = result.get("content", [])
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            raw = block.get("text")
            if not isinstance(raw, str):
                continue
            try:
                value = _decode_json(raw)
            except (ValueError, RecursionError):
                continue
            if isinstance(value, dict):
                return value
    raise BackendProtocolError(
        "MCP result has no JSON object report; inspect isError/content"
    )


class StdioMCPBackend:
    """One serialized session, lazily initialized. No automatic restart/retry.

    binary must be an explicit absolute executable path; args is argv, not a
    shell string. env is trusted operator configuration, never webpage content.
    """

    def __init__(
        self,
        binary: str | Path,
        *,
        args: Sequence[str] = ("mcp",),
        timeout: float = 15.0,
        max_message_bytes: int = 8 * 1024 * 1024,
        max_request_bytes: int = 1024 * 1024,
        max_stderr_bytes: int = 16 * 1024,
        env: Mapping[str, str] | None = None,
    ):
        path = Path(binary).expanduser()
        if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
            raise ValueError(
                "binary must be an absolute path to an existing executable"
            )
        if isinstance(args, (str, bytes)) or not all(
            isinstance(arg, str) for arg in args
        ):
            raise ValueError("args must be a sequence of literal strings")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        for label, value in (
            ("max_message_bytes", max_message_bytes),
            ("max_request_bytes", max_request_bytes),
            ("max_stderr_bytes", max_stderr_bytes),
        ):
            if type(value) is not int or value < 1:
                raise ValueError(f"{label} must be a positive integer")
        self.binary = str(path.resolve())
        self.args = tuple(args)
        self.timeout = timeout
        self.max_message_bytes = max_message_bytes
        self.max_request_bytes = max_request_bytes
        self.max_stderr_bytes = max_stderr_bytes
        self._env = {
            k: v
            for k, v in os.environ.items()
            if k in _SESSION_ENV or k.startswith("LC_")
        }
        if env:
            self._env.update(env)
        self._env["VERY_CU_PYTHON_PREFIX"] = sys.prefix
        self._env["COMPUTER_USE_LINUX_ENABLE_SHELL"] = "0"
        self._env["COMPUTER_USE_LINUX_NOTIFY_ON_COMPLETE"] = "0"
        self._process = None
        self._stdout = bytearray()
        self._stderr = bytearray()
        self._stderr_open = True
        self._next_id = 1
        self._closed = False
        self._initialized = None
        self._lock = threading.RLock()

    @property
    def pid(self):
        return self._process.pid if self._process else None

    @property
    def is_running(self):
        return self._process is not None and self._process.poll() is None

    @property
    def stderr_tail(self):
        """Bounded private diagnostic text. Do not automatically log it."""
        return self._stderr.decode("utf-8", errors="replace")

    def __enter__(self):
        self.initialize()
        return self

    def __exit__(self, *_):
        self.close()

    def _start(self):
        if self._closed:
            raise BackendError(
                "Backend is closed; create a new instance and re-observe state"
            )
        if self._process is not None:
            return
        try:
            self._process = subprocess.Popen(
                [self.binary, *self.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                env=self._env,
                shell=False,
                start_new_session=True,
                close_fds=True,
            )
            for stream in (
                self._process.stdin,
                self._process.stdout,
                self._process.stderr,
            ):
                os.set_blocking(stream.fileno(), False)
        except OSError as exc:
            self.close()
            raise BackendError(f"Cannot start MCP executable: {exc}") from exc

    def initialize(self):
        with self._lock:
            self._start()
            if self._initialized is not None:
                return self._initialized
            try:
                result = self._request(
                    "initialize",
                    {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "semantic-cu", "version": "0.1.0"},
                    },
                )
                if result.get("protocolVersion") not in {
                    "2024-11-05",
                    "2025-03-26",
                    "2025-06-18",
                    "2025-11-25",
                }:
                    raise BackendProtocolError(
                        "Server selected an unsupported MCP protocol version"
                    )
                if not isinstance(result.get("serverInfo"), dict):
                    raise BackendProtocolError(
                        "initialize response is missing serverInfo"
                    )
                self._write(
                    {"jsonrpc": "2.0", "method": "notifications/initialized"},
                    time.monotonic() + self.timeout,
                )
                self._initialized = result
                return result
            except BackendError:
                self.close()
                raise

    def list_tools(self, cursor=None):
        with self._lock:
            self.initialize()
            return self._request(
                "tools/list", {} if cursor is None else {"cursor": cursor}
            )

    def call_tool(self, name, arguments=None):
        """Return the raw MCP result including isError and image blocks."""
        if name not in DESKTOP_TOOLS:
            raise ValueError(f"Tool is not allowed by this desktop transport: {name}")
        if arguments is not None and not isinstance(arguments, Mapping):
            raise TypeError("arguments must be a JSON object")
        with self._lock:
            self.initialize()
            return self._request(
                "tools/call", {"name": name, "arguments": dict(arguments or {})}
            )

    def screenshot(self, **arguments):
        return self.call_tool("screenshot", arguments)

    def get_app_state(self, **arguments):
        return self.call_tool("get_app_state", arguments)

    def click(self, **arguments):
        return self.call_tool("click", arguments)

    def _request(self, method, params):
        request_id = self._next_id
        self._next_id += 1
        deadline = time.monotonic() + self.timeout
        try:
            self._write(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params,
                },
                deadline,
            )
            for _ in range(65):
                response = self._read_message(deadline)
                if response.get("jsonrpc") != "2.0":
                    raise BackendProtocolError(
                        "Peer message has no JSON-RPC 2.0 marker"
                    )
                if "method" in response:
                    if not isinstance(response["method"], str):
                        raise BackendProtocolError("Invalid server method")
                    if "result" in response or "error" in response:
                        raise BackendProtocolError(
                            "Server request mixes request and response fields"
                        )
                    if "id" in response:
                        if type(response["id"]) not in (int, str):
                            raise BackendProtocolError("Invalid server request id")
                        reply = {"jsonrpc": "2.0", "id": response["id"]}
                        if response["method"] == "ping":
                            reply["result"] = {}
                        else:
                            reply["error"] = {
                                "code": -32601,
                                "message": "Client method not supported",
                            }
                        self._write(reply, deadline)
                    continue
                if type(response.get("id")) is not int or response["id"] != request_id:
                    raise BackendProtocolError(
                        f"Response id did not match request {request_id}"
                    )
                if ("error" in response) == ("result" in response):
                    raise BackendProtocolError(
                        "Response must contain exactly one of result or error"
                    )
                if "error" in response:
                    if not isinstance(response["error"], dict):
                        raise BackendProtocolError("JSON-RPC error must be an object")
                    raise BackendRemoteError(response["error"])
                if not isinstance(response["result"], dict):
                    raise BackendProtocolError("MCP result must be an object")
                return response["result"]
            raise BackendProtocolError("Too many non-response messages for one request")
        except BackendRemoteError:
            raise
        except BackendError:
            self.close()
            raise

    @staticmethod
    def _remaining(deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise BackendTimeout(
                "MCP deadline expired; outcome may be unknown, do not replay"
            )
        return remaining

    def _readable_streams(self):
        streams = [self._process.stdout]
        if self._stderr_open:
            streams.append(self._process.stderr)
        return streams

    def _receive(self, streams):
        for stream in streams:
            try:
                chunk = os.read(stream.fileno(), 65536)
            except BlockingIOError:
                continue
            except OSError as exc:
                raise BackendError("Cannot read MCP pipe") from exc
            if stream is self._process.stderr:
                if chunk:
                    self._stderr.extend(chunk)
                    del self._stderr[: -self.max_stderr_bytes]
                else:
                    self._stderr_open = False
            else:
                if not chunk:
                    raise BackendError(
                        "MCP server closed stdout; pending action outcome may be unknown"
                    )
                self._stdout.extend(chunk)
                first_newline = self._stdout.find(b"\n")
                if (
                    first_newline > self.max_message_bytes
                    or (
                        first_newline < 0 and len(self._stdout) > self.max_message_bytes
                    )
                    or len(self._stdout) > self.max_message_bytes + 65536
                ):
                    raise BackendProtocolError("MCP response exceeds byte limit")

    def _write(self, message, deadline):
        try:
            payload = (
                json.dumps(
                    message, ensure_ascii=False, allow_nan=False, separators=(",", ":")
                ).encode("utf-8")
                + b"\n"
            )
        except (TypeError, ValueError, RecursionError) as exc:
            raise BackendProtocolError("Request is not finite JSON") from exc
        if len(payload) > self.max_request_bytes:
            raise BackendProtocolError("MCP request exceeds byte limit")
        offset = 0
        while offset < len(payload):
            readable, writable, _ = select.select(
                self._readable_streams(),
                [self._process.stdin],
                [],
                self._remaining(deadline),
            )
            self._receive(readable)
            if writable:
                try:
                    offset += os.write(self._process.stdin.fileno(), payload[offset:])
                except BlockingIOError:
                    continue
                except OSError as exc:
                    raise BackendError(
                        "MCP input pipe closed; action outcome may be unknown"
                    ) from exc

    def _read_message(self, deadline):
        while True:
            self._remaining(deadline)
            newline = self._stdout.find(b"\n")
            if newline >= 0:
                if newline > self.max_message_bytes:
                    raise BackendProtocolError("MCP response exceeds byte limit")
                raw = bytes(self._stdout[:newline])
                del self._stdout[: newline + 1]
                try:
                    message = _decode_json(raw)
                except (ValueError, UnicodeError, RecursionError) as exc:
                    raise BackendProtocolError(
                        "MCP stdout contained invalid JSON"
                    ) from exc
                if not isinstance(message, dict):
                    raise BackendProtocolError("MCP messages must be JSON objects")
                return message
            readable, _, _ = select.select(
                self._readable_streams(), [], [], self._remaining(deadline)
            )
            self._receive(readable)

    def close(self):
        """Idempotent bounded process-group shutdown, with no protocol retry."""
        with self._lock:
            self._closed = True
            process = self._process
            if process is None:
                return
            self._process = None
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(process.pid, sig)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    pass
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
