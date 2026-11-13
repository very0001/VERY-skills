"""MCP adapters for opt-in keyboard workflows; no standalone shell backend."""
from .keyboard_workflow import KeyboardWorkflow, literal_profile

def keyboard_tools(tool):
    return [
        tool("desktop_keyboard_profile",
             "Return temporary VS Code literal-typing settings only. Does not "
             "write settings. Host must preserve and restore original values.",
             {"original": {"type": "object"}}),
        tool("desktop_terminal_command",
             "Type one explicitly authorized command into the already focused "
             "VS Code terminal in the observed window. Verify focused field and "
             "exact text before Enter; stop on mismatch. Does not select a "
             "terminal, clear existing input, or invoke a shell directly. "
             "Terminal must be known idle at a shell prompt. Reports attempts, "
             "not command success. Never replay a partial or uncertain result.",
             {"command": {"type": "string"},
              "terminal_name": {"type": "string"},
              "observation_id": {"type": "string"},
              "host_approved": {"type": "boolean"}},
             ("command", "terminal_name", "observation_id", "host_approved"),
             False),
    ]

def terminal_command(session, args):
    if args["host_approved"] is not True:
        raise PermissionError("Host must authorize the exact command and terminal")
    if not session.allow_actions or session.paused:
        raise PermissionError("Actions disabled or session paused")
    command, name = args["command"], args["terminal_name"]
    if not isinstance(command, str) or not 1 <= len(command) <= 4096:
        raise ValueError("Command must contain 1 to 4096 characters")
    if any(ord(c) < 32 or ord(c) == 127 for c in command):
        raise ValueError("Command must not contain control characters")
    if not isinstance(name, str) or not 1 <= len(name) <= 256:
        raise ValueError("Exact terminal name is required")
    session._validate_observation(args["observation_id"])
    window = session._observation_arguments.get("window_id")
    if type(window) is not int or window <= 0:
        raise ValueError("A specific observed window is required")
    workflow = KeyboardWorkflow(session.backend, window, host_approved=True)
    # Reuse launch permission, pause state, scope and proposal accounting.
    workflow.session = session
    baseline = session.action_count
    try:
        outcome = workflow.terminal_command(command, name)
    except Exception as exc:
        attempted = session.action_count - baseline
        session._invalidate_observation()
        return {
            "status": "uncertain" if attempted >= 2 else "stopped",
            "task_success": "not_asserted",
            "attempted_actions": attempted,
            "enter_attempted": attempted >= 2,
            "input_may_have_changed": attempted > 0,
            "retry_allowed": False,
            "error": str(exc)[:1024],
            "timings": [list(step) for step in workflow.timings],
        }
    return {
        "status": "dispatched",
        "task_success": "not_asserted",
        "attempted_actions": session.action_count - baseline,
        "enter_attempted": True,
        "retry_allowed": False,
        "timings": [list(step) for step in workflow.timings],
        "outcome": outcome,
    }