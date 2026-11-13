"""Bounded, fixed shared-input batches with one final observation.

The caller must hold the session's shared-input lock for this whole function.
Authorization remains the host's responsibility. No command execution, virtual
input device, clipboard or keymap modification is introduced. Literal text
temporarily pauses Fcitx5 conversion, then restores it when focus remains bound.
The budget is cooperative: a timeout cannot undo native input already sent.
"""
from __future__ import annotations

import copy
import math

from .backend import tool_result_json
from .upstream_contract import validate_arguments
from .literal_input import LiteralInputGuard

_FIELDS = {
    "click": {"x", "y", "button", "click_count"},
    "press_key": {"key"},
    "type_text": {"text"},
    "scroll": {"direction", "pages", "x", "y"},
}


# Exact names from computer-use-linux 0.7.49 at commit
# 6b288a604283a500a2254dcbe1433491746a76f5, src/server.rs:
# normalize_key, modifier_keycode, keycode and keycode_for_ascii. press_key
# parses this grammar BEFORE any xdotool fallback; xdotool key names alone
# therefore do not prove support. Validate only; preserve the original chord.
_MODIFIERS = frozenset({
    "ctrl", "control", "alt", "option", "shift", "meta", "super", "cmd", "command",
})
_NAMED_KEYS = frozenset({
    "enter", "return", "escape", "esc", "tab", "backspace", "delete", "del",
    "space", "home", "end", "pageup", "page_up", "pagedown", "page_down",
    "arrowleft", "left", "arrowright", "right", "arrowup", "up", "arrowdown", "down",
    *(f"f{index}" for index in range(1, 13)),
})
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def _validate_key(key):
    # The batch deliberately uses one simple chord, excluding whitespace and
    # empty '+' segments even though the upstream parser accepts some of them.
    if (not 0 < len(key) <= 128 or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in key)
            or any(not part for part in key.split("+"))
            or (key.startswith("-") and len(key) > 1)):
        raise ValueError("key must be one simple upstream key or combination")
    parts = [part.translate(_ASCII_LOWER).replace("-", "").replace(" ", "")
             for part in key.split("+")]
    final = parts[-1]
    if len(parts) == 1 and final in _MODIFIERS:
        return
    named = final in _NAMED_KEYS
    ascii_key = len(final) == 1 and final in "abcdefghijklmnopqrstuvwxyz0123456789"
    if not all(part in _MODIFIERS for part in parts[:-1]) or not (named or ascii_key):
        raise ValueError("unsupported key in pinned upstream; use type_text for punctuation")


class _StopBatch(Exception):
    def __init__(self, code):
        self.code = code


def _inside(arguments, bounds):
    if not isinstance(bounds, dict) or any(
        type(bounds.get(k)) is not int for k in ("x", "y", "width", "height")
    ):
        raise ValueError("pointer input requires known window bounds")
    if (bounds["width"] <= 0 or bounds["height"] <= 0
            or not bounds["x"] <= arguments["x"] < bounds["x"] + bounds["width"]
            or not bounds["y"] <= arguments["y"] < bounds["y"] + bounds["height"]):
        raise ValueError("absolute pointer coordinates are outside the observed window")


def _preflight(session, args):
    if not isinstance(args, dict) or set(args) - {
        "actions", "observation_id", "host_approved", "max_seconds", "wait_for_window"
    }:
        raise ValueError("invalid batch fields")
    if args.get("host_approved") is not True or not session.allow_actions:
        raise PermissionError("host authorization and enabled actions are required")
    if session.paused:
        raise PermissionError("session paused")
    session._validate_observation(args.get("observation_id"))
    if args.get("wait_for_window") is not None:
        from .window_ready import validate_wait_spec
        validate_wait_spec(args["wait_for_window"])
    maximum = args.get("max_seconds", 10.0)
    if (type(maximum) not in (int, float) or not math.isfinite(maximum)
            or not 0 < maximum <= 30):
        raise ValueError("max_seconds must be greater than zero and at most 30")
    context = session._report.get("window_context")
    observed = copy.deepcopy(session._observation_arguments)
    if (not isinstance(context, dict)
            or type(context.get("window_id")) is not int
            or not 0 < context["window_id"] <= 2**53 - 1
            or observed.get("window_id") != context["window_id"]
            or session._report.get("window_error") or session._report.get("ok") is False):
        raise ValueError("batch requires an observed concrete window_id")
    normalized = validate_input_actions(args.get("actions"), maximum, context)
    return normalized, observed, copy.deepcopy(context), float(maximum)


def validate_input_actions(actions, maximum, context):
    if not isinstance(actions, list) or not 1 <= len(actions) <= 32:
        raise ValueError("actions must contain 1 to 32 fixed inputs")
    normalized = []
    text_chars = 0
    for action in actions:
        if not isinstance(action, dict) or set(action) != {"tool", "arguments"}:
            raise ValueError("each action requires only tool and arguments")
        tool, values = action["tool"], action["arguments"]
        if not isinstance(tool, str) or tool not in _FIELDS:
            raise ValueError("batch supports click, press_key, type_text and scroll")
        if not isinstance(values, dict) or set(values) - _FIELDS[tool]:
            raise ValueError("unsupported batch input fields")
        values = copy.deepcopy(values)
        validate_arguments(tool, values)
        if tool in {"click", "scroll"}:
            if any(type(values.get(k)) is not int for k in ("x", "y")):
                raise ValueError("pointer input requires absolute integer x and y")
            _inside(values, context.get("bounds"))
        if tool == "click":
            if values.get("button", "left") not in {"left", "middle", "right"}:
                raise ValueError("unsupported mouse button")
            if (type(values.get("click_count", 1)) is not int
                    or not 1 <= values.get("click_count", 1) <= 3):
                raise ValueError("click_count must be 1 to 3")
        if tool == "press_key":
            _validate_key(values["key"])
        if tool == "type_text":
            text = values["text"]
            if "\x00" in text:
                raise ValueError("native input cannot contain NUL")
            try:
                text.encode("utf-8")
            except UnicodeEncodeError:
                raise ValueError("native input requires valid Unicode") from None
            text_chars += len(text)
        if tool == "scroll":
            if values["direction"] not in {"up", "down", "left", "right"}:
                raise ValueError("unsupported scroll direction")
            pages = values.get("pages", 1)
            if type(pages) not in (int, float) or not math.isfinite(pages) or not 0 < pages <= 10:
                raise ValueError("pages must be greater than zero and at most 10")
        normalized.append((tool, values))
    if text_chars > 4096:
        raise ValueError("batch text exceeds 4096 characters; split at a task boundary")
    # Pinned X11 backend uses xdotool type --delay 12. This is only a
    # preflight size check, not a prediction or guarantee of desktop latency.
    if text_chars * 0.012 > maximum:
        raise ValueError("text exceeds the declared native typing budget; split this batch")
    return normalized


def run_input_batch(session, args):
    """Run one authorized fixed batch; reports dispatch, never task success.

    All parameters are checked before desktop access. The initial observation is
    consumed before focus/input begins. Reuse only a fresh final observation;
    failure never returns the previous image as a new verification. No retry is
    performed, including when the native input outcome is uncertain.
    """
    clock = session.clock
    started = clock()
    actions, observation_args, target, maximum = _preflight(session, args)
    timings = {"preflight": clock() - started, "focus": 0.0, "actions": 0.0,
               "final_observe": 0.0, "total": 0.0}
    deadline = started + maximum
    result = {"status": "dispatched", "window_id": target["window_id"],
              "requested_actions": len(actions), "attempted_actions": 0,
              "dispatched_actions": 0, "failed_step": 0, "steps": [],
              "retry_allowed": False, "task_success": "not_asserted",
              "verification": {}, "timing_seconds": timings}
    backend = session.backend
    literal_guard = None
    timings["input_method"] = 0.0

    def checkpoint():
        if not session.allow_actions or session.paused:
            raise _StopBatch("session_disabled_or_paused")
        cancel = getattr(session, "batch_cancel", None)
        if cancel is not None and cancel.is_set():
            raise _StopBatch("cancelled")
        if clock() >= deadline:
            raise _StopBatch("budget_exhausted")

    def bounded_call(invoke):
        remaining = deadline - clock()
        if remaining <= 0:
            raise _StopBatch("budget_exhausted")
        previous = getattr(backend, "timeout", None)
        if isinstance(previous, (int, float)) and not isinstance(previous, bool):
            backend.timeout = min(previous, remaining)
        try:
            return invoke()
        finally:
            if previous is not None:
                backend.timeout = previous

    def call(tool, values):
        answer = bounded_call(lambda: backend.call_tool(tool, values))
        if answer.get("isError"):
            raise _StopBatch("upstream_error")
        return tool_result_json(answer)

    def focus():
        tick = clock()
        try:
            report = call("focused_window", {})
            if report.get("error"):
                raise _StopBatch("focus_unavailable")
            window = report.get("focused_window")
            if window is not None:
                if (not isinstance(window, dict)
                        or type(window.get("window_id")) is not int
                        or type(window.get("focused")) is not bool
                        or type(window.get("hidden")) is not bool):
                    raise _StopBatch("malformed_focus_result")
                if (window["window_id"] == target["window_id"]
                        and type(target.get("pid")) is int
                        and (type(window.get("pid")) is not int
                             or window["pid"] != target["pid"])):
                    raise _StopBatch("target_identity_changed")
            return window
        finally:
            timings["focus"] += clock() - tick

    def is_target(window):
        return (isinstance(window, dict)
                and type(window.get("window_id")) is int
                and window["window_id"] == target["window_id"]
                and window.get("focused") is True
                and window.get("hidden") is False
                and (type(target.get("pid")) is not int
                     or (type(window.get("pid")) is int and window["pid"] == target["pid"])))

    # The caller's one-use observation is consumed even if an action times out.
    session._invalidate_observation()
    stage = "focus"
    try:
        checkpoint()
        window = focus()
        if not is_target(window):
            checkpoint()
            tick = clock()
            stage = "activation"
            try:
                activation = call("activate_window", {"window_id": target["window_id"]})
                if activation.get("ok") is not True:
                    raise _StopBatch("activation_not_confirmed")
            finally:
                timings["focus"] += clock() - tick
            stage = "focus"
            window = focus()
        for index, (tool, values) in enumerate(actions):
            result["failed_step"] = index
            step_start = clock()
            step = {"index": index, "tool": tool, "status": "stopped",
                    "timing_seconds": {"focus": 0.0, "action": 0.0, "total": 0.0}}
            result["steps"].append(step)
            try:
                stage = "focus"
                checkpoint()
                if index:
                    tick = clock()
                    try:
                        window = focus()
                    finally:
                        step["timing_seconds"]["focus"] = clock() - tick
                if not is_target(window):
                    raise _StopBatch("focus_changed")
                if tool in {"click", "scroll"}:
                    if window.get("bounds") != target.get("bounds"):
                        raise _StopBatch("pointer_target_changed")
                    try:
                        _inside(values, window.get("bounds"))
                    except ValueError:
                        raise _StopBatch("pointer_target_changed") from None
                checkpoint()
                if tool == "type_text" and literal_guard is None:
                    stage = "literal_input"
                    literal_guard = LiteralInputGuard(backend, target["window_id"], target.get("pid"))
                    tick = clock()
                    try:
                        result["input_method"] = {"start": bounded_call(literal_guard.start)}
                    finally:
                        timings["input_method"] += clock() - tick
                    checkpoint()
                    window = focus()
                    if not is_target(window):
                        raise _StopBatch("focus_changed")
                stage = "action"
                result["attempted_actions"] += 1
                tick = clock()
                try:
                    # Untargeted ordinary input avoids upstream reactivation.
                    report = call(tool, values)
                    if report.get("ok") is not True:
                        raise _StopBatch("dispatch_not_confirmed")
                finally:
                    duration = clock() - tick
                    timings["actions"] += duration
                    step["timing_seconds"]["action"] = duration
                result["dispatched_actions"] += 1
                session.action_count += 1
                step["status"] = "dispatched"
            finally:
                step["timing_seconds"]["total"] = clock() - step_start
        result["failed_step"] = None
    except Exception as exc:
        result["status"] = "uncertain" if stage in {"action", "activation"} else "stopped"
        result["failure"] = {"stage": stage,
            "code": getattr(exc, "code", "backend_exception"),
            "error_type": type(exc).__name__}
        if result["steps"]:
            result["steps"][-1]["status"] = result["status"]
    finally:
        if literal_guard is not None:
            tick = clock()
            # Cleanup must run even when start/input timed out. Never steal focus.
            try:
                restored = literal_guard.restore()
            except Exception:
                restored = {"status": "restoration_failed"}
            result.setdefault("input_method", {})["restore"] = restored
            timings["input_method"] += clock() - tick
    tick = clock()
    try:
        # Exactly one ordinary observation, with the original capture settings.
        if args.get("wait_for_window") is not None and result["status"] == "dispatched":
            from .window_ready import observe_ready_window
            result["verification"] = observe_ready_window(session, args["wait_for_window"], observation_args,
                                                          max_seconds=deadline-clock())
        else:
            result["verification"] = bounded_call(lambda: session.observe(observation_args))
        verification = result["verification"]
        report = tool_result_json(verification["result"])
        content = verification["result"].get("content", [])
        has_image = isinstance(content, list) and any(
            isinstance(block, dict) and block.get("type") == "image"
            and isinstance(block.get("data"), str) and bool(block["data"])
            and isinstance(block.get("mimeType"), str)
            and block["mimeType"].startswith("image/") for block in content
        )
        missing_image = observation_args.get("include_screenshot") is not False and not has_image
        if (report.get("window_error") or report.get("screenshot_error")
                or report.get("ok") is False or missing_image):
            # An incomplete refresh is evidence for inspection, not an input
            # credential. Never let a following batch reuse its observation ID.
            session._invalidate_observation()
            verification.pop("observation_id", None)
            verification["status"] = "incomplete"
            verification["code"] = "missing_requested_image" if missing_image else "observation_incomplete"
            if result["status"] == "dispatched":
                result["status"] = "verification_failed"
    except Exception as exc:
        session._invalidate_observation()
        result["verification"] = {"status": "unavailable",
            "code": getattr(exc, "code", "observation_failed"),
            "error_type": type(exc).__name__}
        if result["status"] == "dispatched":
            result["status"] = "verification_failed"
    finally:
        timings["final_observe"] = clock() - tick
        timings["total"] = clock() - started
    return result
