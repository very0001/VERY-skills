"""Bounded local polling for an identified, mapped and focused window.

No activation, input or screenshot is performed. Window readiness does not
establish that a webpage or document has finished loading its semantic content.
The caller must hold its ordinary shared-input lock across action and waiting.
"""
from __future__ import annotations

import math
import time

from .backend import tool_result_json


class WindowNotReady(RuntimeError):
    def __init__(self, code):
        self.code = code
        self.polls = 0
        self.timing_seconds = {"window_wait": 0.0}
        super().__init__(code)


def _duration(value, name, upper=None):
    if (type(value) not in (int, float) or not math.isfinite(value)
            or value <= 0 or (upper is not None and value > upper)):
        raise ValueError(f"{name} must be finite and positive" +
                         (f" and at most {upper}" if upper else ""))
    return float(value)


def validate_wait_spec(spec):
    fields = {"window_id", "app_id", "wm_class", "pid", "title_contains", "timeout_seconds"}
    if not isinstance(spec, dict) or set(spec) - fields:
        raise ValueError("invalid window readiness specification")
    if not set(spec) & {"window_id", "app_id", "wm_class"}:
        raise ValueError("window readiness needs window_id, app_id or wm_class")
    normalized = dict(spec)
    for key, value in spec.items():
        if key in {"window_id", "pid"}:
            if type(value) is not int or not 0 < value <= 2**53 - 1:
                raise ValueError(f"invalid {key}")
        elif key != "timeout_seconds":
            limit = 512 if key == "title_contains" else 160
            if (not isinstance(value, str) or not value.strip() or len(value) > limit
                    or any(ord(character) < 32 for character in value)):
                raise ValueError(f"invalid {key}")
            try:
                value.encode("utf-8")
            except UnicodeEncodeError:
                raise ValueError(f"invalid {key}") from None
    normalized["timeout_seconds"] = _duration(spec.get("timeout_seconds", 3), "timeout_seconds", 10)
    return normalized


def wait_for_window(session, spec, max_seconds=None):
    """Wait for a unique matching visible/focused window; never steal focus.

    String identities and title substrings compare case-insensitively, without
    dropping any supplied constraints. The deadline is cooperative: underlying
    backend timeouts are shortened when supported; late results are rejected.
    """
    spec = validate_wait_spec(spec)
    budget = spec["timeout_seconds"]
    if max_seconds is not None:
        budget = min(budget, _duration(max_seconds, "max_seconds"))
    clock = session.clock
    started = clock()
    deadline = started + budget
    backend = session.backend
    previous_timeout = getattr(backend, "timeout", None)
    adjustable_timeout = (type(previous_timeout) in (int, float)
                          and math.isfinite(previous_timeout) and previous_timeout > 0)
    polls = 0

    def checkpoint():
        if session.paused or not session.allow_actions:
            raise WindowNotReady("session_disabled_or_paused")
        cancel = getattr(session, "batch_cancel", None)
        if cancel is not None and cancel.is_set():
            raise WindowNotReady("cancelled")
        remaining = deadline - clock()
        if remaining <= 0:
            raise WindowNotReady("timeout")
        return remaining

    def matches(window):
        for key in ("window_id", "pid", "app_id", "wm_class"):
            if key not in spec:
                continue
            value = window.get(key)
            if key in {"window_id", "pid"}:
                if type(value) is not int or value != spec[key]:
                    return False
            elif not isinstance(value, str) or value.casefold() != spec[key].casefold():
                return False
        if "title_contains" in spec:
            title = window.get("title")
            if not isinstance(title, str) or spec["title_contains"].casefold() not in title.casefold():
                return False
        return True

    try:
        while True:
            remaining = checkpoint()
            if adjustable_timeout:
                backend.timeout = min(previous_timeout, remaining)
            polls += 1
            try:
                raw = backend.call_tool("list_windows", {})
                checkpoint()
                report = tool_result_json(raw)
                if raw.get("isError") or report.get("error") or not isinstance(report.get("windows"), list):
                    raise WindowNotReady("window_query_failed")
            except WindowNotReady:
                raise
            except Exception:
                raise WindowNotReady("backend_error") from None
            candidates = []
            for window in report["windows"]:
                if not isinstance(window, dict):
                    raise WindowNotReady("malformed_window_list")
                if not matches(window):
                    continue
                if (type(window.get("window_id")) is not int
                        or not 0 < window["window_id"] <= 2**53 - 1
                        or type(window.get("focused")) is not bool
                        or type(window.get("hidden")) is not bool
                        or (window.get("pid") is not None
                            and (type(window["pid"]) is not int or window["pid"] <= 0))):
                    raise WindowNotReady("malformed_window")
                candidates.append(window)
            if len(candidates) > 1:
                focused = [window for window in candidates if window["focused"]]
                if len(focused) != 1:
                    raise WindowNotReady("ambiguous_target")
                candidates = focused
            if candidates:
                window = candidates[0]
                bounds = window.get("bounds")
                sized = (isinstance(bounds, dict)
                         and all(type(bounds.get(key)) is int and bounds[key] > 0
                                 for key in ("width", "height")))
                if window["focused"] and not window["hidden"] and sized:
                    checkpoint()
                    result = {"window_id": window["window_id"], "polls": polls,
                              "timing_seconds": {"window_wait": clock() - started}}
                    if window.get("pid") is not None:
                        result["pid"] = window["pid"]
                    return result
            time.sleep(min(0.05, checkpoint()))
    except WindowNotReady as exc:
        exc.polls = polls
        exc.timing_seconds = {"window_wait": clock() - started}
        raise
    finally:
        if adjustable_timeout:
            backend.timeout = previous_timeout


def observe_ready_window(session, spec, observation_args, max_seconds=None):
    """One final capture after readiness, preserving only capture preferences."""
    started = session.clock()
    ready = wait_for_window(session, spec, max_seconds=max_seconds)
    target = {k: v for k, v in observation_args.items() if k in {
        "include_screenshot", "max_width", "max_height", "max_bytes", "format", "quality",
        "scale", "max_nodes", "max_depth", "interactive_only", "verbose"}}
    target.update({k: ready[k] for k in ("window_id", "pid") if k in ready})
    previous = getattr(session.backend, "timeout", None)
    try:
        if max_seconds is not None:
            remaining = max_seconds - (session.clock() - started)
            if remaining <= 0:
                raise WindowNotReady("timeout")
            if type(previous) in (int, float):
                session.backend.timeout = min(previous, remaining)
        result = session.observe(target)
        report = tool_result_json(result["result"])
        context = report.get("window_context")
        spec = validate_wait_spec(spec)
        if (not isinstance(context, dict) or context.get("window_id") != ready["window_id"]
                or ("pid" in ready and context.get("pid") != ready["pid"])
                or context.get("focused") is not True or context.get("hidden") is not False
                or any(not isinstance(context.get(k), str) or context[k].casefold() != spec[k].casefold()
                       for k in ("app_id", "wm_class") if k in spec)
                or ("title_contains" in spec and (not isinstance(context.get("title"), str)
                    or spec["title_contains"].casefold() not in context["title"].casefold()))):
            session._invalidate_observation()
            raise WindowNotReady("window_changed_after_wait")
        if (report.get("window_error") or report.get("screenshot_error") or report.get("ok") is False
                or (target.get("include_screenshot") is not False and not any(
                    c.get("type") == "image" and c.get("data") for c in result["result"].get("content", [])))):
            session._invalidate_observation()
            raise WindowNotReady("final_observation_incomplete")
        result["readiness"] = ready
        return result
    finally:
        if previous is not None:
            session.backend.timeout = previous
