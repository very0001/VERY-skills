"""Small bounded stdio MCP gateway; capture begins only on an explicit watch."""

from __future__ import annotations

import base64
import io
import json
import math
import os
import sys
import threading

from .backend import BackendProtocolError, tool_result_json
from .perception import EventGate
from .session import SessionController
from .upstream_contract import normalize_aliases, validate_arguments
from .keyboard_mcp import keyboard_tools, terminal_command, literal_profile
from .auxiliary_input import AuxInput, aux_tools, auxiliary_call
from .fast_input import run_input_batch
from .app_task import AppTasks

MAX_MESSAGE = 1024 * 1024
MAX_OUTPUT = 16 * 1024 * 1024
MAX_JSON_DEPTH = 64
MAX_JSON_NODES = 200000


def _validate_json(value):
    """Reject non-finite, deeply nested or non-JSON trees iteratively."""
    pending = [(value, 0)]
    seen = 0
    while pending:
        node, depth = pending.pop()
        seen += 1
        if depth > MAX_JSON_DEPTH or seen > MAX_JSON_NODES:
            raise ValueError("JSON structure exceeds limits")
        if isinstance(node, dict):
            if seen + len(pending) + len(node) > MAX_JSON_NODES:
                raise ValueError("JSON structure exceeds limits")
            for key, item in node.items():
                if not isinstance(key, str):
                    raise TypeError("JSON object keys must be strings")
                key.encode("utf-8")
                pending.append((item, depth + 1))
        elif isinstance(node, list):
            if seen + len(pending) + len(node) > MAX_JSON_NODES:
                raise ValueError("JSON structure exceeds limits")
            pending.extend((item, depth + 1) for item in node)
        elif isinstance(node, str):
            node.encode("utf-8")
        elif isinstance(node, float):
            if not math.isfinite(node):
                raise ValueError("JSON numbers must be finite")
        elif node is not None and type(node) not in (int, bool):
            raise ValueError("value is not JSON serializable")


def _encode_response(response):
    _validate_json(response)
    chunks = []
    size = 1
    encoder = json.JSONEncoder(
        ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )
    for chunk in encoder.iterencode(response):
        size += len(chunk.encode("utf-8"))
        if size > MAX_OUTPUT:
            raise ValueError("MCP response exceeds output limit")
        chunks.append(chunk)
    return "".join(chunks) + "\n"


def _internal_error(ident):
    return {
        "jsonrpc": "2.0",
        "id": ident,
        "error": {
            "code": -32603,
            "message": "response could not be produced within limits; an action may have executed, observe before retrying",
        },
    }


def _validate_arguments(args, schema):
    if set(args) - set(schema["properties"]):
        raise ValueError("unknown arguments")
    if set(schema["required"]) - set(args):
        raise ValueError("missing arguments")
    for key, value in args.items():
        rule = schema["properties"][key]
        kind = rule.get("type")
        valid = {
            "object": isinstance(value, dict),
            "string": isinstance(value, str),
            "boolean": type(value) is bool,
            "array": isinstance(value, list),
            "number": type(value) is int
            or (type(value) is float and math.isfinite(value)),
        }.get(kind, True)
        if not valid:
            raise ValueError(f"{key} must be {kind}")
        if kind == "number" and (
            value < rule.get("minimum", -math.inf)
            or value > rule.get("maximum", math.inf)
        ):
            raise ValueError(f"{key} is outside its permitted range")


def tool(name, description, properties=None, required=(), readonly=True):
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties or {},
            "required": list(required),
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": readonly,
            "destructiveHint": not readonly,
            "openWorldHint": not readonly,
        },
    }


WAIT_WINDOW_SCHEMA = {"type": "object", "properties": {
    "window_id": {"type": "integer"}, "pid": {"type": "integer"},
    "app_id": {"type": "string"}, "wm_class": {"type": "string"},
    "title_contains": {"type": "string"},
    "timeout_seconds": {"type": "number", "default": 3, "maximum": 10}},
    "additionalProperties": False,
    "description": "After inputs, wait locally for a unique focused mapped window before the sole final screenshot. Require window_id/app_id/wm_class; optional title_contains. No activation or input retry. Use for app/dialog/navigation transitions."}

TOOLS = [
    tool(
        "desktop_backend_tools",
        "Read supported upstream action schemas before preparing an action. Does not enable setup or shell tools.",
    ),
    tool(
        "desktop_watch_start",
        "Start continuous local capture with bounded memory; no model or network calls. Explicit screen permission is required by capture backend.",
        {"duration_seconds": {"type": "number", "minimum": 0.1, "maximum": 25200}},
        readonly=False,
    ),
    tool(
        "desktop_next_event",
        "Retrieve the next queued keyframe event without blocking.",
    ),
    tool(
        "desktop_watch_stop",
        "Stop local capture and report cleanup status.",
        readonly=False,
    ),
    tool(
        "desktop_observe",
        "Observe screenshot and accessibility tree through computer-use-linux; returns a fresh observation ID.",
        {"target": {"type": "object"}},
    ),
    tool(
        "desktop_watch",
        "Watch for one bounded event and return privacy-masked keyframes. Capture is local; the host may transmit returned images to its model.",
        {"timeout_seconds": {"type": "number", "minimum": 0.1, "maximum": 60}},
    ),
    tool("desktop_stats", "Read local perception counters; no desktop capture."),
    tool(
        "desktop_prepare_action",
        "Bind one exact action to a fresh observation for separate review. Existing applicable user permission may suffice; Host remains responsible for authorization.",
        {
            "tool": {"type": "string"},
            "arguments": {"type": "object"},
            "observation_id": {"type": "string"},
        },
        ("tool", "arguments", "observation_id"),
        False,
    ),
    tool(
        "desktop_execute_action",
        "Execute a one-use prepared action with established Host authorization; return verification. Reuse its image and observation_id when sufficient. Do not blindly retry.",
        {"proposal_token": {"type": "string"}, "host_approved": {"type": "boolean"}},
        ("proposal_token", "host_approved"),
        False,
    ),
    tool(
        "desktop_act",
        "Prepare and execute one already-authorized action, returning verification. For opening an app/dialog, supply wait_for_window to wait locally before the final screenshot. Reuse verification for the next action. Separate prepare/execute tools remain available when review is needed. Do not retry uncertain actions.",
        {
            "tool": {"type": "string"},
            "arguments": {"type": "object"},
            "observation_id": {"type": "string"},
            "host_approved": {"type": "boolean"},
            "wait_for_window": WAIT_WINDOW_SCHEMA,
        },
        ("tool", "arguments", "observation_id", "host_approved"),
        False,
    ),
    tool(
        "desktop_app_task",
        "One call for a fully known keyboard task: resolve a named app, launch its installed desktop entry if absent, focus, run fixed keys/text, and return a final image. No initial observe needed. target fields match exactly; ambiguous windows stop. request_id deduplicates within this process (256 IDs); never reuse for different tasks or replay across restart. For transitions set wait_for_window. Optional expected_text reports matched/mismatch/unavailable; unavailable means inspect the returned image. Gedit new tab: Ctrl+T; VS Code new document: Ctrl+N. Only use when steps and authorization are already known. Coordinates require observe/input_batch. No clipboard, IME changes or virtual devices.",
        {
            "request_id": {"type": "string", "description": "Unique task ID; same ID and same arguments return metadata without repeating input. Ledger covers this server process only."},
            "target": {"type": "object", "properties": {
                "window_id": {"type": "integer"}, "pid": {"type": "integer"},
                "app_id": {"type": "string"}, "wm_class": {"type": "string"}},
                "minProperties": 1, "additionalProperties": False},
            "launch_desktop_id": {"type": "string", "description": "Optional installed desktop file ID, e.g. org.gnome.gedit.desktop or code.desktop; launch once only if target is absent."},
            "actions": {"type": "array", "minItems": 1, "maxItems": 32, "items": {
                "type": "object", "properties": {"tool": {"type": "string", "enum": ["press_key", "type_text"]},
                "arguments": {"type": "object"}}, "required": ["tool", "arguments"], "additionalProperties": False}},
            "expected_text": {"type": "string", "description": "Optional exact final focused editable text. Reports matched/mismatch/unavailable without another capture or retry."},
            "host_approved": {"type": "boolean"},
            "wait_for_window": WAIT_WINDOW_SCHEMA,
            "max_seconds": {"type": "number", "default": 10, "maximum": 30, "exclusiveMinimum": 0},
        },
        ("request_id", "target", "actions", "host_approved"),
        False,
    ),
    tool(
        "desktop_input_batch",
        "Run 1-32 already-authorized fixed inputs in one observed window, with one final image. No intermediate tree/screenshots or repeated activation. Supported: click{x,y,button?,click_count?}, press_key{key}, type_text{text}, scroll{direction,pages?,x,y}. Coordinates are absolute screen pixels; omit per-action window targets. Split at visual decisions. Set wait_for_window for a final window transition. Literal text temporarily pauses/restores Fcitx5 conversion. Partial/uncertain results must not be replayed; dispatch is not task success.",
        {
            "actions": {"type": "array", "minItems": 1, "maxItems": 32,
                        "items": {"type": "object", "properties": {
                            "tool": {"type": "string", "enum": ["click", "press_key", "type_text", "scroll"]},
                            "arguments": {"type": "object"}},
                            "required": ["tool", "arguments"], "additionalProperties": False}},
            "observation_id": {"type": "string"},
            "host_approved": {"type": "boolean"},
            "wait_for_window": WAIT_WINDOW_SCHEMA,
            "max_seconds": {"type": "number", "exclusiveMinimum": 0, "maximum": 30,
                            "default": 10, "description": "Cooperative whole-call budget, including final observation; cannot retract sent native input."},
        },
        ("actions", "observation_id", "host_approved"),
        False,
    ),
    tool(
        "desktop_act_sequence",
        "Compatibility path: 1-3 authorized actions with accessibility verification after each step. First action may be any allowed action; later actions are keyboard-only. Requires editable focus and exact text before Return. Prefer input_batch/app_task when intermediate verification is unnecessary. Partial results must not be replayed.",
        {
            "actions": {"type": "array", "minItems": 1, "maxItems": 3,
                        "items": {"type": "object", "properties": {
                            "tool": {"type": "string"}, "arguments": {"type": "object"}},
                            "required": ["tool", "arguments"], "additionalProperties": False}},
            "observation_id": {"type": "string"},
            "host_approved": {"type": "boolean"},
        },
        ("actions", "observation_id", "host_approved"),
        False,
    ),
    tool(
        "desktop_pause",
        "Pause all desktop actions and clear pending action proposals.",
        readonly=False,
    ),
    tool(
        "desktop_resume",
        "Resume actions subject to launch flag and fresh observation.",
        readonly=False,
    ),
]


TOOLS.extend(keyboard_tools(tool))
TOOLS.extend(aux_tools(tool))


def bounded_image(image, max_bytes=512 * 1024):
    """Encode a keyframe within a hard payload budget (PNG, then JPEG)."""
    from PIL import Image

    if type(max_bytes) is not int or max_bytes < 1:
        raise ValueError("image payload budget must be a positive integer")
    current = image.convert("RGB")
    buffer = io.BytesIO()
    current.save(buffer, format="PNG")
    if len(buffer.getvalue()) <= max_bytes:
        return buffer.getvalue(), "image/png", current.size
    for _ in range(12):
        buffer = io.BytesIO()
        current.save(buffer, format="JPEG", quality=80, subsampling=2)
        if len(buffer.getvalue()) <= max_bytes:
            return buffer.getvalue(), "image/jpeg", current.size
        current = current.resize(
            (max(1, int(current.width * 0.75)), max(1, int(current.height * 0.75))),
            Image.Resampling.LANCZOS,
        )
    raise ValueError("cannot encode image within payload budget")


def event_content(event):
    metadata = event.metadata()
    _validate_json(metadata)
    images = []
    for index, keyframe in enumerate(event.keyframes):
        data, mime, size = bounded_image(keyframe.image)
        metadata["keyframes"][index]["sent_width"] = size[0]
        metadata["keyframes"][index]["sent_height"] = size[1]
        metadata["keyframes"][index]["mime_type"] = mime
        images.append(
            {"type": "image", "mimeType": mime, "data": base64.b64encode(data).decode()}
        )
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(metadata, ensure_ascii=False, allow_nan=False),
            }
        ]
        + images
    }


def observation_content(value):
    """Return actual MCP images once, not repeated inside text JSON."""
    _validate_json(value)
    images = []
    image_indexes = {}

    def sanitize(node):
        if isinstance(node, dict):
            if node.get("type") == "image" and "data" in node:
                key = (node.get("mimeType"), node.get("data"))
                if not all(isinstance(part, str) for part in key):
                    raise ValueError("invalid MCP image block")
                index = image_indexes.get(key)
                if index is None:
                    index = len(images)
                    image_indexes[key] = index
                    images.append(node)
                return {"type": "image_reference", "index": index}
            if isinstance(node.get("structuredContent"), dict) and isinstance(
                node.get("content"), list
            ):
                node = dict(node)
                filtered = []
                for block in node["content"]:
                    if isinstance(block, dict) and block.get("type") == "text":
                        try:
                            duplicate = (
                                json.loads(block.get("text", ""))
                                == node["structuredContent"]
                            )
                        except (ValueError, TypeError, RecursionError):
                            duplicate = False
                        if duplicate:
                            continue
                    filtered.append(block)
                node["content"] = filtered
            return {key: sanitize(item) for key, item in node.items()}
        if isinstance(node, list):
            return [sanitize(item) for item in node]
        return node

    cleaned = sanitize(value)
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(cleaned, ensure_ascii=False, allow_nan=False),
            }
        ]
        + images
    }


def text_content(value):
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(value, ensure_ascii=False, allow_nan=False),
            }
        ]
    }


class Gateway:
    def __init__(self, backend, source_factory=None, config=None, allow_actions=False):
        self.session = SessionController(backend, allow_actions=allow_actions)
        self.app_tasks = AppTasks(self.session)
        self.environment_error = None
        # Shared desktop input is the default. Freeze explicit opt-in at launch.
        self.aux_enabled = os.environ.get("SEMANTIC_CU_ENABLE_AUX") == "1"
        self.tools = [entry for entry in TOOLS if self.aux_enabled
                      or not entry["name"].startswith("desktop_aux_")
                      or entry["name"] in {"desktop_aux_status", "desktop_aux_stop"}]
        self.aux = AuxInput()
        self.batch_cancel = threading.Event()
        self.batch_done = threading.Event()
        self.batch_done.set()
        self.batch_thread = None
        self.session.batch_cancel = self.batch_cancel
        self.gate = EventGate(config)
        self.source_factory = source_factory
        self.source = None
        self.watcher = None
        self.config = config

    def _act_sequence(self, args):
        from .session import ACTION_TOOLS

        if args["host_approved"] is not True:
            raise PermissionError("host must establish authorization for every action")
        if not self.session.allow_actions or self.session.paused:
            raise PermissionError("actions disabled or session paused")
        actions = args["actions"]
        if not isinstance(actions, list) or not 1 <= len(actions) <= 3:
            raise ValueError("actions must contain 1 to 3 explicit actions")
        self.session._validate_observation(args["observation_id"])
        if type(self.session._observation_arguments.get("window_id")) is not int:
            raise ValueError("sequence needs an observed, specific window_id")
        final_screenshot = self.session._observation_arguments.get("include_screenshot") is not False
        for index, action in enumerate(actions):
            if not isinstance(action, dict) or set(action) != {"tool", "arguments"}:
                raise ValueError("each action requires only tool and arguments")
            tool_name, arguments = action["tool"], action["arguments"]
            if not isinstance(tool_name, str) or tool_name not in ACTION_TOOLS:
                raise ValueError("action is not in the allowlist")
            if index and tool_name not in {"type_text", "press_key"}:
                raise ValueError("subsequent actions must be keyboard actions")
            if not isinstance(arguments, dict):
                raise TypeError("action arguments must be an object")
            normalized = normalize_aliases(tool_name, arguments)
            validate_arguments(tool_name, normalized)
            if tool_name == "type_text" and any(c in normalized.get("text", "") for c in "\r\n"):
                raise ValueError("sequence text must not contain newline or carriage return")
            if (index and tool_name == "press_key" and actions[index - 1]["tool"] == "type_text"
                    and str(normalized.get("key", "")).lower() not in {"enter", "return"}):
                raise ValueError("only Enter or Return may follow typed text in a sequence")
            if len(json.dumps(normalized, ensure_ascii=False, allow_nan=False).encode()) > 65536:
                raise ValueError("arguments too large")

        observation_id = args["observation_id"]
        steps = []
        last_verification = None
        for index, action in enumerate(actions):
            verify_editable = index and action["tool"] == "type_text"
            verify_text = (index and action["tool"] == "press_key"
                           and str(action["arguments"].get("key", "")).lower() in {"enter", "return"}
                           and actions[index - 1]["tool"] == "type_text")
            if verify_editable or verify_text:
                try:
                    nodes = self.session.verification_tree(observation_id)
                    focused = [node for node in nodes
                               if node.get("focused") is True
                               or "focused" in {s.lower() for s in node.get("states", [])}]
                    if len(focused) != 1:
                        raise ValueError("unique focused field could not be verified")
                    node = focused[0]
                    states = {s.lower() for s in node.get("states", [])}
                    if ("password" in node["role"].lower()
                            or not (node.get("supports_editable_text") is True or "editable" in states)):
                        raise ValueError("focused non-password editable field could not be verified")
                    if verify_text:
                        value = node.get("text")
                        if isinstance(value, dict):
                            if value.get("truncated", False) is not False:
                                raise ValueError("focused text readback is truncated")
                            value = value.get("content")
                        expected = actions[index - 1]["arguments"].get("text")
                        if not isinstance(expected, str) or value != expected:
                            raise ValueError("focused text could not be verified")
                except Exception as exc:  # noqa: BLE001 - preserve partial dispatch, never replay
                    return {"status": "verification_failed", "dispatched_actions": len(steps),
                            "failed_step": index, "error": str(exc),
                            "steps": steps, "verification": last_verification,
                            "retry_allowed": False}
            try:
                proposal = self.session.prepare(action["tool"], action["arguments"], observation_id)
            except Exception as exc:  # noqa: BLE001 - return partial results without replay
                return {"status": "stopped", "dispatched_actions": len(steps),
                        "failed_step": index, "error": str(exc), "steps": steps,
                        "verification": last_verification, "retry_allowed": False}
            outcome = self.session.execute(
                proposal["proposal_token"], host_approved=True,
                include_screenshot_override=final_screenshot if index == len(actions) - 1 else False,
            )
            status = outcome.get("status", "uncertain")
            if status != "dispatched":
                return {"status": status, "dispatched_actions": len(steps),
                        "failed_step": index, "error": outcome.get("error"),
                        "steps": steps, "verification": last_verification,
                        "retry_allowed": False}
            steps.append({"tool": action["tool"], "status": status,
                          "timing_seconds": outcome.get("timing_seconds", {})})
            last_verification = outcome.get("verification")
            action_report = outcome.get("result", {}).get("structuredContent", {})
            if isinstance(action_report, dict) and action_report.get("ok") is False:
                self.session._invalidate_observation()
                return {"status": "action_failed", "dispatched_actions": len(steps),
                        "failed_step": index, "steps": steps,
                        "verification": last_verification, "retry_allowed": False}
            try:
                if not isinstance(last_verification, dict) or last_verification.get("error"):
                    raise ValueError("post-action observation failed")
                report = tool_result_json(last_verification["result"])
                if (report.get("ok") is False or report.get("window_error")
                        or report.get("accessibility_error") or report.get("screenshot_error")):
                    raise ValueError("post-action observation is unhealthy")
                if index == len(actions) - 1 and final_screenshot and not any(
                    isinstance(block, dict) and block.get("type") == "image"
                    for block in last_verification["result"].get("content", [])
                ):
                    raise ValueError("requested final image was not returned")
                observation_id = last_verification["observation_id"]
            except (BackendProtocolError, KeyError, TypeError, ValueError) as exc:
                self.session._invalidate_observation()
                return {"status": "verification_failed", "dispatched_actions": len(steps),
                        "failed_step": index, "error": str(exc), "steps": steps,
                        "verification": last_verification, "retry_allowed": False}
        return {"status": "dispatched", "task_success": "not_asserted",
                "dispatched_actions": len(steps), "steps": steps,
                "verification": last_verification}

    def cancel_batch(self):
        if self.batch_done.is_set():
            return
        self.batch_cancel.set()
        try:
            self.aux.cancel()
        finally:
            # Never report stopped or close the backend before batch finally ends.
            self.batch_done.wait()

    def call(self, name, args):
        if self.environment_error and (name in {
                "desktop_act", "desktop_app_task", "desktop_input_batch", "desktop_act_sequence",
                "desktop_prepare_action", "desktop_execute_action", "desktop_terminal_command"}
                or name.startswith("desktop_aux_") and name not in {"desktop_aux_status", "desktop_aux_stop"}):
            raise PermissionError("Desktop session environment unavailable: " + self.environment_error
                                  + "; forward DISPLAY/DBUS_SESSION_BUS_ADDRESS/XDG_RUNTIME_DIR/XDG_SESSION_TYPE from the authorized session")
        if (hasattr(self, "batch_done") and not self.batch_done.is_set()
                and threading.current_thread() is not self.batch_thread):
            if name == "desktop_aux_status":
                return text_content({"batch_running": True, "active": True,
                                     "cancellation_requested": self.batch_cancel.is_set(),
                                     "cleanup_confirmed": False})
            if name == "desktop_aux_stop":
                if args.get("host_approved") is not True or not self.session.allow_actions:
                    raise PermissionError("Host authorization and action launch flag are required")
                self.cancel_batch()
            elif name == "desktop_pause":
                self.session.pause()
                self.cancel_batch()
            else:
                raise RuntimeError("Auxiliary batch is running; stop or pause it before other tools")
        # Serialize participating gateways on the same local X server.
        import os
        if (name in {"desktop_act", "desktop_act_sequence", "desktop_input_batch", "desktop_app_task", "desktop_execute_action",
                     "desktop_terminal_command"}
                and os.environ.get("XDG_SESSION_TYPE") == "x11"):
            from .auxiliary_guard import input_lock
            with input_lock():
                return self._call_impl(name, args)
        return self._call_impl(name, args)

    def _call_impl(self, name, args):
        if name.startswith("desktop_aux_"):
            if not self.aux_enabled and name not in {"desktop_aux_status", "desktop_aux_stop"}:
                raise PermissionError("Auxiliary input is disabled by default; shared desktop input is active. Explicit SEMANTIC_CU_ENABLE_AUX=1 at launch is required.")
            return observation_content(auxiliary_call(self.session, self.aux, name, args))
        if (self.aux.started or getattr(self.aux, "cleanup_pending", False)) and name in {"desktop_act", "desktop_act_sequence", "desktop_input_batch", "desktop_app_task",
                "desktop_execute_action", "desktop_terminal_command"}:
            raise RuntimeError("Stop auxiliary input before using legacy actions")
        if name == "desktop_terminal_command":
            return observation_content(terminal_command(self.session, args))
        if name == "desktop_keyboard_profile":
            return text_content(literal_profile(args.get("original", {})))
        if name == "desktop_backend_tools":
            from .session import ACTION_TOOLS

            specs = self.session.backend.list_tools().get("tools", [])
            return text_content(
                {
                    "tools": [
                        spec
                        for spec in specs
                        if spec.get("name") in ACTION_TOOLS
                        or spec.get("name") == "get_app_state"
                    ]
                }
            )
        if name == "desktop_watch_start":
            if self.source is not None:
                raise RuntimeError(
                    "synchronous watch already owns capture; restart gateway before changing mode"
                )
            if self.source_factory is None:
                raise RuntimeError("no capture source configured")
            from .watch import WatchService

            if self.watcher is None:
                self.watcher = WatchService(self.source_factory, self.config)
            return text_content(
                self.watcher.start(float(args.get("duration_seconds", 3600)))
            )
        if name == "desktop_next_event":
            if self.watcher is None:
                return text_content({"status": "not_started"})
            event = self.watcher.next_event()
            return (
                event_content(event) if event else text_content(self.watcher.status())
            )
        if name == "desktop_watch_stop":
            return text_content(
                self.watcher.stop() if self.watcher else {"status": "not_started"}
            )
        if name == "desktop_observe":
            return observation_content(self.session.observe(args.get("target", {})))
        if name == "desktop_stats":
            stats = dict(self.watcher.status() if self.watcher else self.gate.stats())
            stats["runtime"] = {"build": "20261113-literal-input-v2", "pid": os.getpid(),
                                "literal_input_guard": True, "window_ready": True}
            return text_content(stats)
        if name == "desktop_prepare_action":
            return text_content(
                self.session.prepare(
                    args["tool"], args["arguments"], args["observation_id"]
                )
            )
        if name == "desktop_execute_action":
            return observation_content(
                self.session.execute(
                    args["proposal_token"], host_approved=args["host_approved"]
                )
            )
        if name == "desktop_act":
            if args.get("wait_for_window") is not None:
                from .window_ready import validate_wait_spec
                validate_wait_spec(args["wait_for_window"])
            if args["host_approved"] is not True:
                raise PermissionError("host must establish authorization before acting")
            proposal = self.session.prepare(
                args["tool"], args["arguments"], args["observation_id"]
            )
            return observation_content(
                self.session.execute(proposal["proposal_token"], host_approved=True, wait_for_window=args.get("wait_for_window"))
            )
        if name == "desktop_act_sequence":
            return observation_content(self._act_sequence(args))
        if name == "desktop_input_batch":
            # Previous auxiliary cancellation belongs to that finished batch.
            # The active-batch/auxiliary interlocks have already been checked.
            self.batch_cancel.clear()
            return observation_content(run_input_batch(self.session, args))
        if name == "desktop_app_task":
            self.batch_cancel.clear()
            return observation_content(self.app_tasks.run(args))
        if name == "desktop_pause":
            return text_content(self.session.pause())
        if name == "desktop_resume":
            return text_content(self.session.resume())
        if name == "desktop_watch":
            if self.watcher is not None:
                raise RuntimeError(
                    "continuous watch owns capture; use desktop_next_event"
                )
            import time

            timeout = float(args.get("timeout_seconds", 5))
            if not 0.1 <= timeout <= 60:
                raise ValueError("timeout must be .1..60 seconds")
            if self.source_factory is None:
                raise RuntimeError("no capture source configured")
            if self.source is None:
                self.source = self.source_factory()
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                frame = self.source.next_frame()
                if frame is None:
                    event = self.gate.flush()
                    return (
                        event_content(event)
                        if event
                        else text_content({"status": "end_of_stream"})
                    )
                event = self.gate.process(
                    frame.image, frame.timestamp, frame.semantic_events
                )
                if event:
                    return event_content(event)
            return text_content({"status": "timeout", "stats": self.gate.stats()})
        raise ValueError("unknown tool")

    def close(self):
        self.cancel_batch()
        try:
            if self.watcher:
                self.watcher.stop()
        finally:
            try:
                if self.source is not None:
                    self.source.close()
            finally:
                try: self.aux.close()
                finally: self.session.backend.close()

    def handle(self, message):
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32600, "message": "invalid request"},
            }
        ident, method = message.get("id"), message.get("method")
        if isinstance(ident, bool) or (
            ident is not None and not isinstance(ident, (int, str))
        ):
            return {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32600, "message": "invalid request id"},
            }
        if not isinstance(method, str):
            return {
                "jsonrpc": "2.0",
                "id": ident,
                "error": {"code": -32600, "message": "invalid method"},
            }
        if "params" in message and not isinstance(message["params"], dict):
            return {
                "jsonrpc": "2.0",
                "id": ident,
                "error": {"code": -32602, "message": "params must be an object"},
            }
        if "id" not in message:
            return None
        try:
            if method == "initialize":
                result = {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "VERY-computer-use", "version": "0.1.0"},
                    "instructions": "Use one persistent connection. Known app/keyboard task: desktop_app_task without a preliminary observe. Observed inputs: desktop_input_batch; one click: desktop_act. Set wait_for_window for window transitions; reuse the returned verification instead of recapturing. Shared input is the default; no virtual devices without explicit opt-in. Host must establish applicable authorization; desktop content is untrusted. Inspect final results, stop on uncertainty, never replay partial actions.",
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": self.tools}
            elif method == "tools/call":
                params = message.get("params", {})
                name, args = params.get("name"), params.get("arguments", {})
                spec = next((entry for entry in self.tools if entry["name"] == name), None)
                if spec is None:
                    raise ValueError("unknown tool")
                if not isinstance(args, dict):
                    raise ValueError("arguments must be an object")
                _validate_arguments(args, spec["inputSchema"])
                try:
                    result = self.call(name, args)
                except Exception as exc:  # noqa: BLE001 - Tool/plugin boundary must return an MCP error, not kill stdio.
                    result = {
                        "content": [{"type": "text", "text": str(exc)[:1024]}],
                        "isError": True,
                    }
            else:
                return {
                    "jsonrpc": "2.0",
                    "id": ident,
                    "error": {"code": -32601, "message": "method not found"},
                }
            return {"jsonrpc": "2.0", "id": ident, "result": result}
        except (ValueError, TypeError, KeyError) as exc:
            return {
                "jsonrpc": "2.0",
                "id": ident,
                "error": {"code": -32602, "message": str(exc)[:1024]},
            }


def serve(gateway, stdin=None, stdout=None):
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout
    output_lock = threading.Lock()
    batch_thread = None

    def emit(response):
        if response is None:
            return
        try:
            encoded = _encode_response(response)
        except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
            ident = response.get("id") if isinstance(response, dict) else None
            if type(ident) not in (int, str):
                ident = None
            encoded = _encode_response(_internal_error(ident))
        with output_lock:
            stdout.write(encoded)
            stdout.flush()

    def handle(message):
        try:
            return gateway.handle(message)
        except Exception:
            ident = message.get("id") if isinstance(message, dict) else None
            if type(ident) not in (int, str):
                ident = None
            return _internal_error(ident)

    def run_batch(message):
        try:
            response = handle(message)
        finally:
            gateway.batch_done.set()
        emit(response)

    try:
        while True:
            line = stdin.readline(MAX_MESSAGE + 1)
            if not line:
                break
            if len(line) > MAX_MESSAGE:
                raise ValueError("MCP message exceeds input limit")
            try:
                from .backend import _decode_json
                message = _decode_json(line)
                _validate_json(message)
            except (ValueError, UnicodeError, RecursionError):
                emit({"jsonrpc": "2.0", "id": None,
                      "error": {"code": -32700, "message": "parse error"}})
                continue
            params = message.get("params") if isinstance(message, dict) else None
            is_batch = (isinstance(message, dict) and "id" in message
                        and message.get("method") == "tools/call"
                        and isinstance(params, dict)
                        and params.get("name") == "desktop_aux_batch")
            if is_batch and getattr(gateway, "aux_enabled", False) and hasattr(gateway, "batch_done") and gateway.batch_done.is_set():
                if batch_thread is not None:
                    batch_thread.join()
                gateway.batch_cancel.clear()
                gateway.batch_done.clear()
                batch_thread = threading.Thread(target=run_batch, args=(message,), name="auxiliary-batch")
                gateway.batch_thread = batch_thread
                batch_thread.start()
            else:
                emit(handle(message))
    finally:
        try:
            if batch_thread is not None:
                try:
                    gateway.cancel_batch()
                finally:
                    batch_thread.join()
        finally:
            gateway.close()
