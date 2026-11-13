"""Opt-in VS Code keyboard workflows. Host authorization is still required.
The host applies literal_profile() temporarily through the visible settings UI,
records original settings and restores them afterwards. No settings are written
here. No clipboard, direct editor writes, or silent retries.
"""
import copy
import time
from .backend import tool_result_json
from .session import SessionController

LITERAL_OPTIONS = {
    "editor.autoIndent": "none",
    "editor.autoClosingBrackets": "never",
    "editor.autoClosingQuotes": "never",
    "editor.autoSurround": "never",
    "editor.acceptSuggestionOnEnter": "off",
    "editor.quickSuggestions": False,
    "editor.suggestOnTriggerCharacters": False,
    "editor.tabCompletion": "off",
}

def literal_profile(original):
    result = copy.deepcopy(original)
    result.update(LITERAL_OPTIONS)
    for language in ("[python]", "[plaintext]"):
        settings = result.setdefault(language, {})
        if not isinstance(settings, dict):
            raise ValueError("Language settings must be an object")
        settings.update(LITERAL_OPTIONS)
    return result

def typing_budget(text, baseline=15.0):
    if not isinstance(text, str) or len(text) > 10000:
        raise ValueError("Text must contain at most 10000 characters")
    return max(baseline, 10.0 + len(text) * 0.025)

class KeyboardWorkflow:
    def __init__(self, backend, window_id, *, host_approved=False):
        if host_approved is not True:
            raise PermissionError("Host must authorize the exact planned input")
        self.backend = backend
        self.session = SessionController(backend, allow_actions=True)
        self.target = dict(window_id=window_id, include_screenshot=False,
                           max_nodes=2000, max_depth=64, verbose=True)
        self.observation = None
        self.timings = []

    def observe(self):
        self.observation = self.session.observe(self.target)["observation_id"]

    def focused(self):
        nodes = self.session.verification_tree(self.observation)
        focused = [n for n in nodes if "focused" in n.get("states", [])]
        if len(focused) != 1:
            raise ValueError("A unique focused field is required")
        field = focused[0]
        if "editable" not in field.get("states", []) or "password" in field["role"]:
            raise ValueError("A non-password editable field is required")
        return field

    def act(self, tool, arguments):
        if tool not in {"type_text", "press_key", "click"}:
            raise ValueError("Not a keyboard workflow action")
        token = self.session.prepare(tool, arguments, self.observation)
        old_timeout = self.backend.timeout
        started = time.monotonic()
        try:
            if tool == "type_text":
                self.backend.timeout = typing_budget(arguments["text"], old_timeout)
            result = self.session.execute(token["proposal_token"], host_approved=True)
        finally:
            self.backend.timeout = old_timeout
        self.timings.append((tool, time.monotonic() - started))
        self.observation = None
        report = tool_result_json(result.get("result", {}))
        if result.get("status") != "dispatched" or report.get("ok") is not True:
            raise RuntimeError("Action failed or uncertain; do not replay")
        verify = result.get("verification") or {}
        if verify.get("error") or not verify.get("observation_id"):
            raise RuntimeError("Post-action observation failed; do not replay")
        self.observation = verify["observation_id"]
        return result

    def terminal_command(self, command, terminal_name):
        if not command or "\n" in command or "\r" in command:
            raise ValueError("A single authorized command is required")
        self.observe()
        before = self.focused()
        if before.get("name") != terminal_name or not any(
                word in terminal_name.lower() for word in ("terminal", "终端")):
            raise ValueError("Target terminal is not focused")
        if (before.get("text") or {}).get("character_count") != 0:
            raise ValueError("Terminal input is not empty")
        self.act("type_text", {"text": command})
        after = self.focused()
        text = after.get("text") or {}
        if after.get("object_ref") != before.get("object_ref") or text.get(
                "truncated", False) or text.get("content") != command:
            raise ValueError("Typed command not verified; Enter was NOT sent")
        return self.act("press_key", {"key": "Enter"})
