"""Auxiliary text, chords, held keys, scrolling and dragging on X11."""
import ctypes as C
import time
from . import x11_pair as n

def keyboard(twin, window, *, text=None, keys=None, hold=0):
    lookup = n.bind(twin.x, "XkbKeycodeToKeysym", n.L, n.P, n.U, n.I, n.I)
    def code_for(key):
        code = twin.keycode(twin.d, twin.keysym(key.encode("ascii")))
        if not code:
            raise ValueError("Unknown or unmapped keysym: " + key)
        return code
    plans = []
    if text is not None:
        for char in text:
            if char in "\n\t":
                plans.append((code_for("Return" if char == "\n" else "Tab"), False))
                continue
            found = next(((code, bool(level)) for level in (0, 1)
                for code in range(8, 256)
                if lookup(twin.d, code, 0, level) == ord(char)), None)
            if found is None:
                raise ValueError("Character not mapped in current X11 layout; nothing typed")
            plans.append(found)
    else:
        codes = [code_for(k) for k in keys]
    shift = code_for("Shift_L")
    dev = twin.open_device(twin.d, n.device(" XTEST keyboard"))
    if not dev:
        raise RuntimeError("Virtual keyboard unavailable")
    pressed = []
    def event(code, down):
        twin.fake_key(twin.d, dev, code, int(down), None, 0, 0)
        twin.sync(twin.d, 0)
    def down(code):
        pressed.append(code)
        event(code, True)
    def up(code):
        event(code, False)
        pressed.remove(code)
    try:
        twin.focus(twin.d, twin.keyboard, window, 0)
        if text is None:
            for code in codes:
                down(code)
            time.sleep(hold)
        else:
            for code, shifted in plans:
                if shifted:
                    down(shift)
                down(code)
                up(code)
                if shifted:
                    up(shift)
                time.sleep(.013)
    finally:
        for code in reversed(pressed):
            event(code, False)
        twin.close_device(twin.d, dev)

def pointer(twin, operation, args):
    dev = twin.open_device(twin.d, n.device(" XTEST pointer"))
    if not dev:
        raise RuntimeError("Virtual pointer unavailable")
    button = n.bind(twin.xt, "XTestFakeDeviceButtonEvent", n.I,
                    n.P, n.P, n.U, n.I, C.POINTER(n.I), n.I, n.L)
    held = False
    def event(value, down):
        button(twin.d, dev, value, int(down), None, 0, 0)
        twin.sync(twin.d, 0)
    try:
        twin.move(args["x"], args["y"])
        if operation == "scroll":
            value = {"up": 4, "down": 5, "left": 6, "right": 7}[args["direction"]]
            for _ in range(args["steps"]):
                try: event(value, True)
                finally: event(value, False)
                time.sleep(.013)
        else:
            held = True
            event(args["button"], True)
            steps = max(1, int(args["duration"] / .02))
            for i in range(1, steps + 1):
                fraction = i / steps
                twin.move(args["x"] + (args["to_x"] - args["x"]) * fraction,
                          args["y"] + (args["to_y"] - args["y"]) * fraction)
                time.sleep(args["duration"] / steps)
    finally:
        if held:
            event(args["button"], False)
        twin.close_device(twin.d, dev)

def validate(operation, supplied, context, window):
    values = dict(supplied)
    allowed = {"text": {"text", "unicode_mode"}, "chord": {"keys", "hold"},
               "scroll": {"x", "y", "direction", "steps"},
               "drag": {"x", "y", "to_x", "to_y", "button", "duration"}}
    if set(values) - allowed[operation]:
        raise ValueError("Unknown auxiliary arguments")
    if operation == "text":
        text = values.get("text")
        if not isinstance(text, str) or not 1 <= len(text) <= 4096:
            raise ValueError("Text length must be 1..4096")
        if any(ord(c) < 32 and c not in "\n\t" for c in text):
            raise ValueError("Unsupported control character")
        if values.get("unicode_mode") not in (None, "fcitx"):
            raise ValueError("Unknown Unicode input mode")
        if sum(not c.isascii() for c in text) > 256:
            raise ValueError("At most 256 Unicode characters per call")
        if any(0xD800 <= ord(c) <= 0xDFFF or ord(c) == 127 for c in text):
            raise ValueError("Invalid text character")
    elif operation == "chord":
        keys = values.get("keys")
        if (not isinstance(keys, list) or not 1 <= len(keys) <= 8
                or any(not isinstance(k, str) or not k.isascii()
                       or not k.isprintable() or not 1 <= len(k) <= 64 for k in keys)
                or len(set(keys)) != len(keys)):
            raise ValueError("Expected 1..8 distinct X keysym names")
        values.setdefault("hold", 0)
        if type(values["hold"]) not in (int, float) or not 0 <= values["hold"] <= 2:
            raise ValueError("Hold must be 0..2 seconds")
    else:
        bounds = context.get("bounds") or {}
        pairs = [("x", "x", "width"), ("y", "y", "height")]
        if operation == "drag":
            pairs += [("to_x", "x", "width"), ("to_y", "y", "height")]
        for key, origin, dimension in pairs:
            val, start, size = values.get(key), bounds.get(origin), bounds.get(dimension)
            if (type(val) is not int or type(start) is not int or type(size) is not int
                    or not start <= val < start + size):
                raise ValueError("Pointer coordinates must stay inside observed window")
        if operation == "scroll":
            values.setdefault("steps", 1)
            if values.get("direction") not in ("up", "down", "left", "right"):
                raise ValueError("Invalid scroll direction")
            if type(values["steps"]) is not int or not 1 <= values["steps"] <= 30:
                raise ValueError("Scroll steps must be 1..30")
        else:
            values.setdefault("button", 1)
            values.setdefault("duration", .3)
            if type(values["button"]) is not int or values["button"] not in (1, 2, 3):
                raise ValueError("Invalid drag button")
            if type(values["duration"]) not in (int, float) or not .02 <= values["duration"] <= 2:
                raise ValueError("Drag duration must be .02..2 seconds")
    if operation in ("text", "chord"):
        values["window_id"] = window
    return values

def type_text(twin, window, text, unicode_mode=None):
    if unicode_mode != "fcitx" or text.isascii():
        return keyboard(twin, window, text=text)
    import subprocess
    check = subprocess.run(["fcitx5-remote"], capture_output=True, text=True, timeout=2)
    if check.returncode or check.stdout.strip() not in ("1", "2"):
        raise RuntimeError("Fcitx Unicode input is unavailable")
    # The host must verify this app supports the Fcitx Unicode input hotkey.
    # No clipboard writes, core keymap changes, or IME setting changes.
    pending = ""
    for char in text:
        if char.isascii():
            pending += char
            continue
        if pending:
            keyboard(twin, window, text=pending)
            pending = ""
        keyboard(twin, window, keys=["Control_L", "Alt_L", "Shift_L", "u"])
        time.sleep(.03)
        keyboard(twin, window, text="0x" + format(ord(char), "x"))
        keyboard(twin, window, keys=["Return"])
        time.sleep(.03)
    if pending:
        keyboard(twin, window, text=pending)
        
def raw_input(twin, operation, args):
    if operation == "release_all":
        for item in reversed(args["held"]):
            raw_input(twin, item["kind"] + "_up",
                      {item["kind"]: item["value"], "window_id": item["window_id"]})
        return
    is_key = operation.startswith("key")
    down = operation.endswith("_down")
    if is_key:
        value = twin.keycode(twin.d, twin.keysym(args["key"].encode("ascii")))
        if not value:
            raise ValueError("Unknown or unmapped X keysym")
        if down:
            twin.focus(twin.d, twin.keyboard, args["window_id"], 0)
    else:
        value = args["button"]
        if down:
            twin.move(args["x"], args["y"])
    dev = twin.open_device(twin.d, n.device(" XTEST keyboard" if is_key else " XTEST pointer"))
    if not dev:
        raise RuntimeError("Owned virtual input device unavailable")
    try:
        emit = twin.fake_key if is_key else n.bind(
            twin.xt, "XTestFakeDeviceButtonEvent", n.I,
            n.P, n.P, n.U, n.I, C.POINTER(n.I), n.I, n.L)
        emit(twin.d, dev, value, int(down), None, 0, 0)
        twin.sync(twin.d, 0)
    finally:
        twin.close_device(twin.d, dev)

def validate_raw(operation, supplied, context, window):
    values = dict(supplied)
    if operation == "release_all":
        if values:
            raise ValueError("release_all takes no arguments")
        return {}
    is_key = operation.startswith("key")
    down = operation.endswith("_down")
    allowed = {"key"} if is_key else {"button"}
    if down:
        allowed.add("lease_seconds")
        if not is_key:
            allowed.update(("x", "y"))
    if set(values) - allowed:
        raise ValueError("Unknown raw input argument")
    if is_key:
        key = values.get("key")
        if not isinstance(key, str) or not 1 <= len(key) <= 64 or not key.isascii() or not key.isprintable():
            raise ValueError("Expected one printable ASCII X keysym name")
    else:
        if type(values.get("button")) is not int or not 1 <= values["button"] <= 9:
            raise ValueError("Button must be 1..9")
        if down:
            validate("scroll", {"x": values.get("x"), "y": values.get("y"),
                "direction": "up"}, context, window)
    if down:
        values.setdefault("lease_seconds", 5)
        if type(values["lease_seconds"]) not in (int, float) or not .2 <= values["lease_seconds"] <= 10:
            raise ValueError("Lease must be .2..10 seconds")
    values["window_id"] = window
    return values

