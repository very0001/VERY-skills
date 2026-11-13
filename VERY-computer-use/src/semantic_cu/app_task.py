"""One local application/keyboard transaction, independent of the model host.

Caller owns the ordinary shared-input lock. No shell strings, virtual devices,
implicit retry, clipboard or persistent screenshots. Only fixed keyboard plans
may skip model-side initial observation; coordinate plans use input_batch.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time

from .backend import tool_result_json
from .fast_input import run_input_batch, validate_input_actions


class TaskStop(Exception):
    pass


class AppTasks:
    def __init__(self, session):
        self.session = session
        # Metadata only; do not retain screen images or typed contents.
        self.requests = {}

    def _validate_launcher(self, ident):
        if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}\.desktop", ident):
            raise ValueError("launch_desktop_id must be an installed desktop file ID")
        roots = [Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))]
        roots += [Path(p) for p in os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":") if p]
        if not any((root / "applications" / ident).is_file() for root in roots):
            raise ValueError("desktop entry not installed")
        return ident

    def _launch(self, ident, remaining):
        # GTK resolves the installed desktop entry, including D-Bus activation.
        # The caller cannot supply a shell command, file path or launch arguments.
        subprocess.run(["/usr/bin/gtk-launch", ident], stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=max(0.01, remaining), check=True, start_new_session=True)

    def run(self, args):
        s = self.session
        started = s.clock()
        if not isinstance(args, dict) or set(args) - {
            "request_id", "target", "launch_desktop_id", "actions", "host_approved",
            "max_seconds", "expected_text", "wait_for_window"
        }:
            raise ValueError("unknown task arguments")
        if args.get("host_approved") is not True or not s.allow_actions or s.paused:
            raise PermissionError("enabled actions and applicable host authorization required")
        request_id = args.get("request_id")
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,96}", request_id):
            raise ValueError("request_id must be a unique short identifier for this task")
        maximum = args.get("max_seconds", 10.0)
        if type(maximum) not in (int, float) or not math.isfinite(maximum) or not 0 < maximum <= 30:
            raise ValueError("max_seconds must be greater than zero and at most 30")
        target = args.get("target")
        if not isinstance(target, dict) or not target or set(target) - {"window_id", "app_id", "wm_class", "pid"}:
            raise ValueError("target needs an exact window_id, app_id, wm_class or pid")
        for key, value in target.items():
            if key in {"window_id", "pid"}:
                if type(value) is not int or not 0 < value <= 2**53 - 1:
                    raise ValueError("invalid numeric window target")
            elif not isinstance(value, str) or not value.strip() or len(value) > 160:
                raise ValueError("invalid application identity")
        actions = args.get("actions")
        if not isinstance(actions, list) or any(not isinstance(a, dict) or a.get("tool") not in {"press_key", "type_text"} for a in actions):
            raise ValueError("app tasks accept only fully specified keyboard actions; observe before pointer input")
        validate_input_actions(actions, maximum, {})
        if args.get("wait_for_window") is not None:
            from .window_ready import validate_wait_spec
            validate_wait_spec(args["wait_for_window"])
        expected = args.get("expected_text")
        if expected is not None and (not isinstance(expected, str) or len(expected) > 4096):
            raise ValueError("expected_text must contain at most 4096 characters")
        launch_id = args.get("launch_desktop_id")
        if launch_id is not None:
            self._validate_launcher(launch_id)
        digest = hashlib.sha256(json.dumps(args, sort_keys=True, ensure_ascii=True, allow_nan=False).encode()).hexdigest()
        if request_id in self.requests:
            prior = self.requests[request_id]
            if prior["digest"] != digest:
                raise ValueError("request_id was already used for different arguments")
            return {"status": "already_processed", "previous_status": prior["status"],
                    "request_id": request_id, "dispatched_actions": prior["dispatched_actions"],
                    "retry_allowed": False, "next_step": "observe_to_check_current_state"}
        if len(self.requests) >= 256:
            raise ValueError("session request ledger full; start a new session, never replay old tasks")
        entry = {"digest": digest, "status": "uncertain", "dispatched_actions": 0}
        self.requests[request_id] = entry
        deadline = started + maximum
        previous_timeout = getattr(s.backend, "timeout", None)
        timing = {"resolve_window": 0.0, "launch": 0.0, "initial_state": 0.0}
        stage = "resolve_window"

        def remaining():
            if s.paused or not s.allow_actions:
                raise TaskStop("session_disabled_or_paused")
            value = deadline - s.clock()
            if value <= 0:
                raise TaskStop("budget_exhausted")
            if isinstance(previous_timeout, (float, int)):
                s.backend.timeout = min(previous_timeout, value)
            return value

        def select():
            tick = s.clock()
            try:
                remaining()
                raw = s.backend.call_tool("list_windows", {})
                report = tool_result_json(raw)
                if raw.get("isError") or report.get("error") or not isinstance(report.get("windows"), list):
                    raise TaskStop("window_query_failed")
                matches = []
                for w in report["windows"]:
                    if not isinstance(w, dict):
                        raise TaskStop("malformed_window_list")
                    if all((type(w.get(k)) is int and w[k] == v) if k in {"window_id", "pid"}
                           else isinstance(w.get(k), str) and w[k].casefold() == v.casefold()
                           for k, v in target.items()):
                        matches.append(w)
                if len(matches) > 1:
                    active = [w for w in matches if w.get("focused") is True]
                    if len(active) == 1:
                        return active[0]
                    raise TaskStop("ambiguous_target")
                return matches[0] if matches else None
            finally:
                timing["resolve_window"] += s.clock() - tick

        result = None
        try:
            window = select()
            if window is None:
                if launch_id is None:
                    raise TaskStop("app_not_running")
                stage = "launch"
                tick = s.clock()
                self._launch(launch_id, remaining())
                timing["launch"] = s.clock() - tick
                stage = "resolve_window"
                while window is None:
                    window = select()
                    if window is None:
                        time.sleep(min(0.05, remaining()))
            stage = "initial_state"
            tick = s.clock()
            remaining()
            # Bind concrete identity locally; do not upload an intermediate image.
            target_args = {"window_id": window["window_id"], "include_screenshot": False,
                           "max_nodes": 1, "max_depth": 1}
            if type(window.get("pid")) is int:
                target_args["pid"] = window["pid"]
            observed = s.observe(target_args)
            timing["initial_state"] = s.clock() - tick
            # Final image uses the normal upstream dimensions/quality.
            s._observation_arguments.update(include_screenshot=True, max_nodes=500, max_depth=32)
            stage = "input"
            result = run_input_batch(s, {"actions": actions, "host_approved": True,
                                       "observation_id": observed["observation_id"],
                                       "max_seconds": remaining(),
                                       "wait_for_window": args.get("wait_for_window")})
            if expected is not None:
                result["text_check"] = self._check_text(result, expected)
                if result["status"] == "dispatched" and result["text_check"]["status"] == "mismatch":
                    result["status"] = "verification_failed"
                elif result["text_check"]["status"] == "unavailable":
                    result["text_check"]["next_step"] = "inspect_returned_image"
        except Exception as exc:
            s._invalidate_observation()
            result = {"status": "uncertain" if stage in {"launch", "input"} else "stopped",
                      "dispatched_actions": 0, "retry_allowed": False,
                      "failure": {"stage": stage, "code": str(exc) if isinstance(exc, TaskStop) else type(exc).__name__}}
        finally:
            if previous_timeout is not None:
                s.backend.timeout = previous_timeout
        result["request_id"] = request_id
        result.setdefault("timing_seconds", {}).update(timing)
        result["timing_seconds"]["task_total"] = s.clock() - started
        entry.update(status=result["status"], dispatched_actions=result.get("dispatched_actions", 0))
        return result

    @staticmethod
    def _check_text(result, expected):
        verification = result.get("verification", {})
        try:
            report = tool_result_json(verification["result"])
        except Exception:
            return {"status": "unavailable"}
        candidates = [n for n in report.get("accessibility_tree", [])
                      if isinstance(n, dict) and (n.get("focused") is True or "focused" in n.get("states", []))
                      and (n.get("supports_editable_text") is True or "editable" in n.get("states", []))]
        if (len(candidates) != 1 or not report.get("tree_scoped") or report.get("accessibility_error")
                or report.get("accessibility_tree_truncated") is True or report.get("accessibility_tree_spill")
                or "password" in str(candidates[0].get("role", "")).lower()):
            return {"status": "unavailable"}
        value = candidates[0].get("text", {})
        if not isinstance(value, dict) or value.get("truncated") or not isinstance(value.get("content"), str):
            return {"status": "unavailable"}
        return {"status": "matched" if value["content"] == expected else "mismatch",
                "expected_characters": len(expected), "observed_characters": len(value["content"])}
