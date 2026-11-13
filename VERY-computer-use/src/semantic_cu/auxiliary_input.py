"""Experimental independent X11 input. No physical devices are reattached.
Native calls run in short-lived workers so an X11 error cannot kill the gateway.
"""
import ctypes as C
import json
import os
import subprocess
import sys
import uuid

from .auxiliary_guard import AuxInput

def _probe(twin):
    from . import x11_pair as n
    class Buttons(C.Structure):
        _fields_ = [("length", C.c_int), ("mask", C.c_void_p)]
    class Mods(C.Structure):
        _fields_ = [(key, C.c_int) for key in ("base", "latched", "locked", "effective")]
    query = n.bind(twin.xi, "XIQueryPointer", n.I, n.P, n.I, n.L,
                   C.POINTER(n.L), C.POINTER(n.L),
                   *([C.POINTER(C.c_double)] * 4),
                   C.POINTER(Buttons), C.POINTER(Mods), C.POINTER(Mods))
    get_focus = n.bind(twin.xi, "XIGetFocus", n.I, n.P, n.I, C.POINTER(n.L))
    def point(device):
        root, child = n.L(), n.L()
        values = [C.c_double() for _ in range(4)]
        buttons, mods, group = Buttons(), Mods(), Mods()
        query(twin.d, device, twin.root, C.byref(root), C.byref(child),
              *[C.byref(v) for v in values], C.byref(buttons),
              C.byref(mods), C.byref(group))
        n.bind(twin.x, "XFree", n.I, n.P)(buttons.mask)
        return [v.value for v in values[:2]]
    def focus(device):
        window = n.L()
        get_focus(twin.d, device, C.byref(window))
        return window.value
    core_pointer = int(n.run("list", "--id-only", "Virtual core pointer"))
    core_keyboard = int(n.run("list", "--id-only", "Virtual core keyboard"))
    return dict(active=True, name=n.NAME, pointer_id=twin.pointer,
                keyboard_id=twin.keyboard, pointer=point(twin.pointer),
                core_pointer=point(core_pointer), keyboard_focus=focus(twin.keyboard),
                core_keyboard_focus=focus(core_keyboard))

def _worker(payload):
    from . import x11_pair as n
    n.NAME = payload["name"]
    op, args = payload["operation"], payload["arguments"]
    if op not in {"status", "stop", "release_all"} and os.environ.get("SEMANTIC_CU_ENABLE_AUX") != "1":
        raise PermissionError("Auxiliary input is disabled by default; explicit SEMANTIC_CU_ENABLE_AUX=1 is required")
    present = n.NAME + " pointer" in n.run("list", "--name-only").splitlines()
    if op == "start":
        if os.environ.get("SEMANTIC_CU_ENABLE_AUX") != "1":
            raise PermissionError("Auxiliary live input is disabled")
        if present:
            raise RuntimeError("Name collision; refusing to take over existing devices")
        n.run("create-master", n.NAME, 0)
        present = True
    if op == "stop":
        if present:
            n.stop()
        return {"active": False, "name": n.NAME}
    if not present:
        if op == "status":
            return {"active": False, "name": n.NAME}
        raise RuntimeError("Auxiliary pair not running")
    if op == "batch":
        from .auxiliary_transaction import native
        return native(dict(args, name=n.NAME))
    twin = n.Twin()
    try:
        before = _probe(twin)
        if op in {"key_down", "key_up", "button_down", "button_up", "release_all"}:
            from .auxiliary_events import raw_input
            raw_input(twin, op, args)
            return {"before": before, "after": _probe(twin)}
        if op in {"start", "status"}:
            return before
        if op in {"text", "chord", "scroll", "drag"}:
            from .auxiliary_events import keyboard, pointer, type_text
            if op == "text":
                type_text(twin, args["window_id"], args["text"], args.get("unicode_mode"))
            elif op == "chord":
                keyboard(twin, args["window_id"], keys=args["keys"], hold=args["hold"])
            else:
                pointer(twin, op, args)
            return {"before": before, "after": _probe(twin)}
        if op in {"move", "click"}:
            twin.move(args["x"], args["y"])
        if op == "click":
            dev = twin.open_device(twin.d, n.device(" XTEST pointer"))
            if not dev:
                raise RuntimeError("Virtual pointer unavailable")
            try:
                click = n.bind(twin.xt, "XTestFakeDeviceButtonEvent", n.I,
                               n.P, n.P, n.U, n.I, C.POINTER(n.I), n.I, n.L)
                click(twin.d, dev, args["button"], 1, None, 0, 0)
                click(twin.d, dev, args["button"], 0, None, 0, 0)
                twin.sync(twin.d, 0)
            finally:
                twin.close_device(twin.d, dev)
        elif op == "key":
            twin.key(args["window_id"], args["key"])
        elif op != "move":
            raise ValueError("Unknown auxiliary operation")
        return {"before": before, "after": _probe(twin)}
    finally:
        twin.close()

def aux_tools(tool):
    approval = {"host_approved": {"type": "boolean"}}
    observed = dict(approval, observation_id={"type": "string"})
    return [
        tool("desktop_aux_status", "Read this gateway's auxiliary X11 input status."),
        tool("desktop_aux_batch", "Explicit transient-input mode: create a paired pointer/keyboard, execute a bounded batch, remove immediately. Physical app keyboard may be blocked until removal. Requires user acceptance; no automatic retry. Native yellow 10x10 square cursor, unchanged typing speed; text is ASCII and current IME applies. All actions target the observed window. Max 128 actions/4096 characters/120 seconds.",
             dict(observed, acknowledge_keyboard_interruption={"type":"boolean"}, actions={"type":"array","items":{"type":"object"},"minItems":1,"maxItems":128},max_seconds={"type":"number","minimum":5,"maximum":120}),
             ("host_approved","observation_id","acknowledge_keyboard_interruption","actions"),False),
        tool("desktop_aux_start",
             "Create an experimental independent X11 pointer/keyboard pair. "
             "Never reattach physical devices. Requires authorized observed X11 session.",
             observed, ("host_approved", "observation_id"), False),
        tool("desktop_aux_stop", "Remove only this gateway's auxiliary devices. "
             "Allowed while paused. Optional recover_stale=true cleans an unlocked recorded "
             "orphan lease only. Refuses unexpected attached devices.",
             dict(approval, recover_stale={"type": "boolean"}), ("host_approved",), False),
        tool("desktop_aux_action",
             "Experimental independent X11 move, click, or one X keysym. "
             "Target is the observed window; x/y are global coordinates inside it. "
             "Arguments: move{x,y}, click{x,y,button=1}, key{key}. "
             "Also: text{text}, chord{keys,hold=0}, scroll{x,y,direction,steps=1}, "
             "drag{x,y,to_x,to_y,button=1,duration=.3}. Chord keys are X keysyms. "
             "Text supports optional unicode_mode=fcitx in verified Fcitx apps. "
             "Raw: key_down{key,lease_seconds=5}, key_up{key}, "
             "button_down{x,y,button,lease_seconds=5}, button_up{button}, release_all{}. "
             "Leases are .2..10 seconds; buttons 1..9. Requires experimental live opt-in. "
             "App focus/caret isolation is NOT guaranteed. Does not use the core "
             "input path as a fallback. Host must authorize the exact action. "
             "Reports dispatch only; do not replay uncertain results.",
             dict(observed, operation={"type": "string"}, arguments={"type": "object"}),
             ("host_approved", "observation_id", "operation", "arguments"), False),
    ]

def auxiliary_call(session, aux, name, args):
    if name == "desktop_aux_status":
        from .input_compatibility import local_report
        result = aux.run("status") if aux.started or getattr(aux, "cleanup_pending", False) else {"active": False}
        return dict(result, compatibility=local_report())
    if args["host_approved"] is not True or not session.allow_actions:
        raise PermissionError("Host authorization and action launch flag are required")
    if name == "desktop_aux_stop":
        try:
            result = aux.close()
        except Exception:
            guard = getattr(aux, "guard", None)
            if args.get("recover_stale") is not True or (guard is not None and guard.poll() is None):
                raise
            from .auxiliary_guard import recover_stale_lease
            recovered = recover_stale_lease()
            aux.close()
            return recovered
        if args.get("recover_stale") is True:
            from .auxiliary_guard import recover_stale_lease
            return recover_stale_lease()
        return result
    if session.paused:
        raise PermissionError("Actions paused")
    session._validate_observation(args["observation_id"])
    context = session._report.get("window_context") or {}
    if context.get("backend") != "x11":
        raise ValueError("An observed native X11 window is required")
    if name == "desktop_aux_batch":
        from .auxiliary_transaction import validate, budget, run_batch
        if args.get("acknowledge_keyboard_interruption") is not True:
            raise PermissionError("Explicit temporary keyboard interruption acknowledgement required")
        if aux.started or getattr(aux,"cleanup_pending",False):
            raise ValueError("Close existing auxiliary session before a short transaction")
        actions=args["actions"];window=session._observation_arguments["window_id"]
        validate(actions,window);budget(actions,args.get("max_seconds",120))
        bounds=context.get("bounds") or {}
        for action in actions:
            if action["op"] in {"move","click","scroll"}:
                for coordinate,size in (("x","width"),("y","height")):
                    start,length=bounds.get(coordinate),bounds.get(size)
                    if type(start) is not int or type(length) is not int or not start<=action[coordinate]<start+length:
                        raise ValueError("Coordinates must be inside observed target")
        session._invalidate_observation();session.action_count+=len(actions)
        try:
            result=run_batch(actions,window,acknowledge_keyboard_interruption=True,max_seconds=args.get("max_seconds",120),owner=aux,cancel_event=getattr(session,"batch_cancel",None))
        except Exception as exc:
            return dict(status="uncertain",retry_allowed=False,error=str(exc)[:1024],cleanup=getattr(exc,"cleanup",{"confirmed":False}),transaction=getattr(exc,"transaction",None))
        return dict(status="dispatched",task_success="not_asserted",retry_allowed=False,result=result)
    if name == "desktop_aux_start":
        from .input_compatibility import local_report
        preflight = local_report()
        if preflight["start_blockers"]:
            raise PermissionError("MPX start blocked before device creation: " + "; ".join(preflight["start_blockers"]))
        return aux.start()
    if not aux.started:
        raise ValueError("Start this gateway's auxiliary pair first")
    op, supplied = args["operation"], args["arguments"]
    if op in {"text", "chord", "scroll", "drag", "key_down", "key_up",
              "button_down", "button_up", "release_all"}:
        from .auxiliary_events import validate, validate_raw
        if not isinstance(supplied, dict):
            raise ValueError("Arguments must be an object")
        validator = validate if op in {"text", "chord", "scroll", "drag"} else validate_raw
        values = validator(op, supplied, context, session._observation_arguments["window_id"])
        values.setdefault("window_id", session._observation_arguments["window_id"])
        session._invalidate_observation()
        session.action_count += 1
        try:
            result = aux.run(op, values)
        except Exception as exc:
            try: cleanup = aux.close()
            except Exception as cleanup_error:
                cleanup = {"error": str(cleanup_error)[:512]}
            return dict(status="uncertain", retry_allowed=False, error=str(exc)[:1024], cleanup=cleanup)
        return dict(status="dispatched", task_success="not_asserted",
                    retry_allowed=False, result=result)
    if op not in {"move", "click", "key"} or not isinstance(supplied, dict):
        raise ValueError("Unsupported auxiliary action")
    expected = {"x", "y", "button"} if op == "click" else (
        {"x", "y"} if op == "move" else {"key"})
    if set(supplied) - expected:
        raise ValueError("Unknown auxiliary arguments")
    values = dict(supplied)
    if op in {"move", "click"}:
        bounds = context.get("bounds") or {}
        for coordinate, size in (("x", "width"), ("y", "height")):
            value, start, length = values.get(coordinate), bounds.get(coordinate), bounds.get(size)
            if (type(value) is not int or type(start) is not int
                    or type(length) is not int or not start <= value < start + length):
                raise ValueError("Coordinates must be inside the observed window")
        if op == "click":
            values.setdefault("button", 1)
            if type(values["button"]) is not int or not 1 <= values["button"] <= 9:
                raise ValueError("Button must be 1..9")
    else:
        key = values.get("key")
        if not isinstance(key, str) or not 1 <= len(key) <= 64 or not key.isascii():
            raise ValueError("A single ASCII X keysym name is required")
        values["window_id"] = session._observation_arguments["window_id"]
    values.setdefault("window_id", session._observation_arguments["window_id"])
    session._invalidate_observation()
    session.action_count += 1
    try:
        result = aux.run(op, values)
    except Exception as exc:
        return dict(status="uncertain", retry_allowed=False, error=str(exc)[:1024])
    return dict(status="dispatched", task_success="not_asserted",
                retry_allowed=False, result=result)

if __name__ == "__main__":
    from .auxiliary_guard import arm_parent_death
    expected_parent = os.environ.get("SEMANTIC_CU_WORKER_OWNER_PID")
    if expected_parent is None:
        raise PermissionError("Native workers must be launched by the supervisor")
    arm_parent_death(int(expected_parent))
    print(json.dumps(_worker(json.loads(sys.stdin.read()))))
    