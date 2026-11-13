"""One-use scoped action proposals; reconstructed after workspace loss.

The host establishes authorization under the applicable permission rules. An
explicit current task or existing permission can cover an action without a new
question for every click; sensitive or high-risk actions remain subject to the
host's policy. Proposal tokens bind exact arguments, observations and expiry, but
host_approved is only a host assertion, not independent authorization.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any

from .backend import BackendProtocolError, tool_result_json
from .upstream_contract import normalize_aliases, validate_arguments

OBSERVATION_TOOLS = frozenset(
    {
        "doctor",
        "list_windows",
        "focused_window",
        "get_app_state",
        "get_tree_page",
        "list_apps",
    }
)
ACTION_TOOLS = frozenset(
    {
        "click",
        "drag",
        "scroll",
        "press_key",
        "type_text",
        "perform_action",
        "set_value",
        "activate_window",
    }
)
_WINDOW_FIELDS = ("window_id", "pid", "app_id", "wm_class", "title")
_TERMINAL_FIELDS = ("tty", "terminal_pid", "terminal_command", "terminal_cwd")
_ELEMENT_TOOLS = frozenset({"click", "scroll", "perform_action", "set_value"})
_WINDOW_TITLE_TOOLS = frozenset({"click", "scroll", "perform_action", "set_value"})


def _present(value):
    return value is not None and value != ""


@dataclass
class Proposal:
    tool: str
    arguments: dict[str, Any]
    observation_id: str
    created_at: float
    expires_at: float
    digest: str
    observation_arguments: dict[str, Any]


class SessionController:
    def __init__(
        self,
        backend,
        *,
        allow_actions=False,
        clock=time.monotonic,
        observation_ttl=30.0,
        proposal_ttl=30.0,
    ):
        self.backend = backend
        self.allow_actions = allow_actions
        self.clock = clock
        self.observation_ttl = observation_ttl
        self.proposal_ttl = proposal_ttl
        self.observation_id = None
        self.observed_at = 0.0
        self.proposals: dict[str, Proposal] = {}
        self.paused = False
        self.action_count = 0
        self._observation_arguments = {}
        self._report = {}

    def _invalidate_observation(self):
        self.observation_id = None
        self.proposals.clear()
        self._observation_arguments = {}
        self._report = {}

    def observe(self, arguments=None):
        if arguments is not None and not isinstance(arguments, dict):
            raise ValueError("observation target must be an object")
        normalized = normalize_aliases("get_app_state", arguments or {})
        validate_arguments("get_app_state", normalized)
        # Even a failed refresh can have replaced/cleared the upstream tree.
        self._invalidate_observation()
        saved = json.loads(json.dumps(normalized, allow_nan=False))
        result = self.backend.call_tool("get_app_state", saved)
        if result.get("isError"):
            raise RuntimeError("upstream observation failed")
        try:
            report = tool_result_json(result)
        except BackendProtocolError:
            report = {}
        self._report = json.loads(json.dumps(report, allow_nan=False))
        self._observation_arguments = saved
        context = self._report.get("window_context")
        if isinstance(context, dict):
            for key in ("window_id", "pid"):
                if (
                    _present(saved.get(key))
                    and _present(context.get(key))
                    and (
                        type(saved[key]) is not type(context[key])
                        or saved[key] != context[key]
                    )
                ):
                    self._invalidate_observation()
                    raise RuntimeError(
                        "upstream observation resolved a different target"
                    )
            if (
                type(context.get("window_id")) is int
                and 0 < context["window_id"] <= 2**53 - 1
            ):
                self._observation_arguments["window_id"] = context["window_id"]
        self.observation_id = secrets.token_hex(12)
        self.observed_at = self.clock()
        self.proposals.clear()
        return {"observation_id": self.observation_id, "result": result}

    def verification_tree(self, observation_id):
        """Read the entire cached snapshot, never a backend-supplied file path.

        Paging is bounded, snapshot-bound and does not refresh observation age.
        A spilled prefix cannot establish unique focus. Traversal truncation is
        distinct from response spilling and cannot be repaired by pagination.
        """
        self._validate_observation(observation_id)
        report = self._report
        snapshot = report.get("snapshot_id")
        context = report.get("window_context")
        window = self._observation_arguments.get("window_id")
        if (report.get("tree_scoped") is not True
                or report.get("accessibility_tree_truncated") is not False
                or any(report.get(k) for k in ("accessibility_error", "window_error"))
                or report.get("ok") is False
                or not isinstance(snapshot, str) or not snapshot.strip()
                or type(window) is not int or not isinstance(context, dict)
                or type(context.get("window_id")) is not int
                or context["window_id"] != window
                or self._observation_arguments.get("interactive_only") is True):
            raise ValueError("verification requires a complete healthy window-scoped tree")
        inline = report.get("accessibility_tree")
        if not isinstance(inline, list):
            raise ValueError("verification tree is malformed")
        spill = report.get("accessibility_tree_spill")
        nodes = inline
        if spill is not None:
            if (not isinstance(spill, dict)
                    or type(spill.get("node_count")) is not int
                    or not len(inline) < spill["node_count"] <= 10000
                    or type(spill.get("inline_node_count")) is not int
                    or spill["inline_node_count"] != len(inline)):
                raise ValueError("verification spill metadata is malformed")
            total = spill["node_count"]
            nodes = []
            for _ in range(128):
                offset = len(nodes)
                result = self.backend.call_tool("get_tree_page", {
                    "snapshot_id": snapshot, "offset": offset})
                if result.get("isError"):
                    raise ValueError("verification tree page failed")
                page = tool_result_json(result)
                chunk = page.get("nodes")
                if (page.get("snapshot_id") != snapshot
                        or type(page.get("offset")) is not int or page["offset"] != offset
                        or type(page.get("node_count")) is not int or page["node_count"] != total
                        or page.get("error") or page.get("ok") is False
                        or not isinstance(chunk, list) or not chunk
                        or offset + len(chunk) > total):
                    raise ValueError("verification tree page is inconsistent")
                nodes.extend(chunk)
                next_offset = page.get("next_offset")
                if len(nodes) == total:
                    if "next_offset" not in page or next_offset is not None:
                        raise ValueError("verification tree page did not terminate")
                    break
                if type(next_offset) is not int or next_offset != len(nodes):
                    raise ValueError("verification tree page is incomplete")
            else:
                raise ValueError("verification tree pagination limit exceeded")
            if nodes[:len(inline)] != inline:
                raise ValueError("verification tree prefix changed")
        indexes = set()
        for node in nodes:
            if (not isinstance(node, dict) or type(node.get("index")) is not int
                    or node["index"] < 0 or node["index"] in indexes
                    or not isinstance(node.get("role"), str)
                    or not isinstance(node.get("states", []), list)
                    or not all(isinstance(s, str) for s in node.get("states", []))
                    or ("focused" in node and type(node["focused"]) is not bool)):
                raise ValueError("verification tree node is malformed")
            indexes.add(node["index"])
        self._validate_observation(observation_id)
        return nodes

    def _bind_arguments(self, tool, arguments):
        args = json.loads(json.dumps(arguments, allow_nan=False))
        report = self._report
        if report.get("window_error") or report.get("ok") is False:
            raise ValueError("observation has a target error; observe the target again")
        if tool == "scroll" and any(
            args.get(k) for k in ("role", "name", "text", "states")
        ):
            raise ValueError(
                "scroll does not support semantic selectors; use an observed element_index"
            )
        if "window_title" in args:
            if "title" in args and args["title"] != args["window_title"]:
                raise ValueError("conflicting title and window_title")
            args["title"] = args.pop("window_title")
        allowed_fields = set(_WINDOW_FIELDS)
        if tool in {"press_key", "type_text", "activate_window"}:
            allowed_fields.update(_TERMINAL_FIELDS)
        if tool == "drag":
            allowed_fields = set()
        context = report.get("window_context")
        if not isinstance(context, dict):
            context = {}
        observed = self._observation_arguments
        for key in (*_WINDOW_FIELDS, *_TERMINAL_FIELDS):
            if not _present(args.get(key)):
                continue
            if key not in allowed_fields:
                raise ValueError(f"{tool} cannot safely use a {key} target")
            known = [
                source[key]
                for source in (observed, context)
                if _present(source.get(key))
            ]
            if not any(
                type(args[key]) is type(value) and args[key] == value for value in known
            ):
                raise ValueError(f"scope mismatch for {key}; observe that target first")
        scoped = any(
            _present(observed.get(k))
            for k in (
                *_WINDOW_FIELDS,
                *_TERMINAL_FIELDS,
                "app_name_or_bundle_identifier",
            )
        )
        if tool == "drag" and scoped:
            raise ValueError(
                "drag has no window targeting; require an explicit full-desktop observation"
            )
        if "window_id" in allowed_fields and _present(observed.get("window_id")):
            args["window_id"] = observed["window_id"]
        elif "pid" in allowed_fields and _present(observed.get("pid")):
            args["pid"] = observed["pid"]
        else:
            for key in allowed_fields:
                if _present(observed.get(key)) and not _present(args.get(key)):
                    args[key] = observed[key]
        if tool in _WINDOW_TITLE_TOOLS and "title" in args:
            args["window_title"] = args.pop("title")
        semantic = tool in {"perform_action", "set_value"} or (
            tool in _ELEMENT_TOOLS
            and (
                args.get("element_index") is not None
                or _present(args.get("element_identifier"))
                or args.get("coordinate_space") == "atspi"
                or (
                    tool != "scroll"
                    and any(args.get(k) for k in ("role", "name", "text", "states"))
                )
            )
        )
        snapshot = report.get("snapshot_id")
        if args.get("snapshot_id") is not None and (
            tool not in _ELEMENT_TOOLS
            or not isinstance(snapshot, str)
            or not snapshot.strip()
            or args["snapshot_id"] != snapshot
        ):
            raise ValueError("snapshot_id conflicts with the current observation")
        if semantic:
            has_coordinates = args.get("x") is not None or args.get("y") is not None
            if (
                tool in {"click", "scroll"}
                and has_coordinates
                and not (
                    tool == "click"
                    and args.get("coordinate_space") == "atspi"
                    and args.get("x") is not None
                    and args.get("y") is not None
                    and not args.get("relative")
                )
            ):
                raise ValueError(
                    "capture coordinates override upstream snapshot checks; do not mix them with semantic targets"
                )
            nodes = report.get("accessibility_tree")
            if (
                report.get("accessibility_error")
                or report.get("tree_scoped") is not True
                or not isinstance(snapshot, str)
                or not snapshot.strip()
                or not isinstance(nodes, list)
                or not nodes
            ):
                raise ValueError(
                    "semantic action requires a healthy scoped tree and snapshot_id; observe again"
                )
            by_index = {
                n["index"]: n
                for n in nodes
                if isinstance(n, dict)
                and type(n.get("index")) is int
                and n["index"] >= 0
            }
            identifier = args.pop("element_identifier", None)
            if identifier is not None:
                if tool not in {"perform_action", "set_value"}:
                    raise ValueError(
                        "element_identifier is unsupported for this action"
                    )
                matches = [
                    n for n in by_index.values() if n.get("object_ref") == identifier
                ]
                if len(matches) != 1:
                    raise ValueError("element_identifier was not uniquely observed")
                index = matches[0]["index"]
                if (
                    args.get("element_index") is not None
                    and args["element_index"] != index
                ):
                    raise ValueError("element_index and element_identifier conflict")
                # Direct object refs bypass upstream snapshot guards. Use index.
                args["element_index"] = index
            if args.get("element_index") is not None:
                index = args["element_index"]
                if type(index) is not int or index not in by_index:
                    raise ValueError(
                        "element_index was not present in the observed tree"
                    )
            elif not any(args.get(k) for k in ("role", "name", "text", "states")):
                raise ValueError(
                    "semantic action requires an observed element or selector"
                )
            args["snapshot_id"] = snapshot
        elif scoped and not any(
            _present(args.get(k))
            for k in (*_WINDOW_FIELDS, *_TERMINAL_FIELDS, "window_title")
        ):
            raise ValueError(
                "observation scope cannot safely target this action; observe by window_id or pid"
            )
        if scoped and args.get("screenshot_after") in (True, "screen"):
            raise ValueError(
                "full-screen post-action capture would widen the observed scope; use window"
            )
        return args

    def _validate_observation(self, observation_id):
        if not observation_id or observation_id != self.observation_id:
            raise ValueError("stale observation: observe again")
        if self.clock() - self.observed_at > self.observation_ttl:
            raise ValueError("observation expired: observe again")

    def prepare(self, tool, arguments, observation_id):
        if not self.allow_actions:
            raise PermissionError(
                "actions disabled; launch with explicit --allow-actions after host authorization"
            )
        if self.paused:
            raise PermissionError("session paused")
        if tool not in ACTION_TOOLS:
            raise PermissionError("tool is not in the action allowlist")
        if not isinstance(arguments, dict):
            # Preserve the existing public validation exception contract.
            raise ValueError("arguments must be an object")  # noqa: TRY004
        self._validate_observation(observation_id)
        normalized = normalize_aliases(tool, arguments)
        validate_arguments(tool, normalized)
        bound = self._bind_arguments(tool, normalized)
        # Binding and alias conversion must not introduce unsupported wire data.
        validate_arguments(tool, bound)
        encoded = json.dumps(bound, sort_keys=True, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode()) > 65536:
            raise ValueError("arguments too large")
        digest = hashlib.sha256((tool + encoded + observation_id).encode()).hexdigest()
        token = secrets.token_urlsafe(24)
        now = self.clock()
        self.proposals = {k: v for k, v in self.proposals.items() if v.expires_at > now}
        if len(self.proposals) >= 32:
            raise ValueError("too many pending proposals")
        verification_args = json.loads(json.dumps(self._observation_arguments))
        self.proposals[token] = Proposal(
            tool,
            json.loads(encoded),
            observation_id,
            now,
            now + self.proposal_ttl,
            digest,
            verification_args,
        )
        return {
            "proposal_token": token,
            "tool": tool,
            "arguments": json.loads(encoded),
            "digest": digest,
            "expires_in_seconds": self.proposal_ttl,
            # Legacy field name: establish authorization, not a mandatory new
            # user-confirmation prompt for every click.
            "requires_host_user_approval": True,
        }

    def execute(self, proposal_token, *, host_approved=False, include_screenshot_override=None, wait_for_window=None):
        if wait_for_window is not None:
            from .window_ready import validate_wait_spec
            wait_for_window = validate_wait_spec(wait_for_window)
        if include_screenshot_override is not None and type(include_screenshot_override) is not bool:
            raise TypeError("screenshot override must be a boolean or None")
        if not self.allow_actions or self.paused:
            raise PermissionError("actions disabled or session paused")
        if host_approved is not True:
            raise PermissionError(
                "host must establish authorization under applicable permission rules before setting host_approved"
            )
        proposal = self.proposals.pop(proposal_token, None)
        if not proposal:
            raise ValueError("unknown or consumed proposal")
        if self.clock() > proposal.expires_at:
            raise ValueError("proposal expired")
        self._validate_observation(proposal.observation_id)
        # Local monotonic durations only: no arguments, text, images or identifiers.
        # The pre-dispatch gap includes host/tool scheduling; it is NOT model time.
        started = self.clock()
        timing = {"observation_to_execute": started - self.observed_at}
        self._invalidate_observation()
        self.action_count += 1
        try:
            result = self.backend.call_tool(proposal.tool, proposal.arguments)
        except Exception as exc:  # noqa: BLE001
            # Arbitrary backend failures can follow dispatch; never imply no effect.
            elapsed = self.clock() - started
            timing.update(action_roundtrip=elapsed, execute_total=elapsed)
            return {
                "timing_seconds": timing,
                "status": "uncertain",
                "retry_allowed": False,
                "error": str(exc),
                "next_step": "observe before deciding what happened",
            }
        action_done = self.clock()
        timing["action_roundtrip"] = action_done - started
        try:
            verification_arguments = dict(proposal.observation_arguments)
            if include_screenshot_override is not None:
                verification_arguments["include_screenshot"] = include_screenshot_override
            if wait_for_window is None:
                verification = self.observe(verification_arguments)
            else:
                from .window_ready import observe_ready_window
                verification = observe_ready_window(self, wait_for_window, verification_arguments)
        except Exception as exc:  # noqa: BLE001
            # Verification failures must not discard the already dispatched result.
            verification = {"error": str(exc)}
        finished = self.clock()
        timing.update(verification_roundtrip=finished - action_done,
                      execute_total=finished - started)
        return {
            "timing_seconds": timing,
            "status": ("upstream_error" if result.get("isError") else
                       "verification_failed" if wait_for_window is not None and verification.get("error") else "dispatched"),
            "task_success": "not_asserted",
            "result": result,
            "verification": verification,
        }

    def pause(self):
        self.paused = True
        self.proposals.clear()
        return {"paused": True}

    def resume(self):
        self.paused = False
        self._invalidate_observation()
        return {"paused": False, "requires_new_observation": True}
