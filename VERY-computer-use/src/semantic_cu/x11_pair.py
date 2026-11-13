# EXPERIMENTAL: not a replacement for the current Computer Use backend.
# Verified: independent pointer movement; physical devices unchanged.
# Verified: virtual keyboard emits XI2 RawKeyPress/RawKeyRelease.
# Later observation confirmed three virtual-keyboard a characters arrived.
# Two keyboards do not create two editor carets or independent app state.
# stop removes only this named pair, rejecting attached physical devices.
# Test pair has been removed; run start explicitly for further testing.
# X11 only. No autostart, no system service, no root permission required.
"""Twin Input: reversible X11 second pointer/keyboard prototype.
Uses installed xinput/libXi/libXtst. Never reattaches physical devices.
Independent devices do NOT imply independent editor carets in VS Code.
Usage: python twin_input.py start|status|stop|move X Y|key WINDOW key"""
import argparse
import ctypes as C
import os
import subprocess

NAME = "VERY Twin"
P, I, U, L = C.c_void_p, C.c_int, C.c_uint, C.c_ulong

def run(*args):
    prepare = None
    if os.environ.get("SEMANTIC_CU_WORKER_OWNER_PID"):
        # The native worker is single-threaded. Protect its xinput child too.
        libc = C.CDLL(None, use_errno=True)
        expected_parent = os.getpid()
        def prepare():
            if libc.prctl(1, 9, 0, 0, 0) != 0 or os.getppid() != expected_parent:
                os._exit(1)
    return subprocess.check_output(["xinput", *map(str, args)], text=True,
                                   preexec_fn=prepare).strip()

def device(suffix):
    return int(run("list", "--id-only", NAME + suffix))

def start():
    if os.environ.get("SEMANTIC_CU_ENABLE_AUX") != "1":
        raise PermissionError("Auxiliary input is disabled by default; explicit SEMANTIC_CU_ENABLE_AUX=1 is required")
    names = run("list", "--name-only").splitlines()
    if NAME + " pointer" not in names:
        run("create-master", NAME)
    print(run("list", NAME + " pointer"))
    print(run("list", NAME + " keyboard"))

def stop():
    pointer = device(" pointer")
    listing = run("list")
    slaves = [line for line in listing.splitlines()
              if "slave" in line and ("(" + str(pointer) + ")" in line              or "(" + str(device(" keyboard")) + ")" in line)]
    if any(NAME + " XTEST" not in line for line in slaves):
        raise RuntimeError("Unexpected attached device; refusing to remove pair")
    run("remove-master", pointer, "Floating")

def bind(lib, name, result, *args):
    fn = getattr(lib, name)
    fn.restype, fn.argtypes = result, list(args)
    return fn
class Twin:
    def __init__(self):
        if os.environ.get("XDG_SESSION_TYPE") == "wayland":
            raise RuntimeError("Native X11 required; no Wayland bypass")
        self.x = C.CDLL("libX11.so.6")
        self.xi = C.CDLL("libXi.so.6")
        self.xt = C.CDLL("libXtst.so.6")
        self.d = bind(self.x, "XOpenDisplay", P, C.c_char_p)(None)
        if not self.d:
            raise RuntimeError("Cannot open the authorized X display")
        self.root = bind(self.x, "XDefaultRootWindow", L, P)(self.d)
        self.sync = bind(self.x, "XSync", I, P, I)
        self.pointer, self.keyboard = device(" pointer"), device(" keyboard")
        self.open_device = bind(self.xi, "XOpenDevice", P, P, L)
        self.close_device = bind(self.xi, "XCloseDevice", I, P, P)
        self.warp = bind(self.xi, "XIWarpPointer", I, P, I, L, L,
                         C.c_double, C.c_double, U, U, C.c_double, C.c_double)
        self.focus = bind(self.xi, "XISetFocus", I, P, I, L, L)
        self.fake_key = bind(self.xt, "XTestFakeDeviceKeyEvent", I,
                             P, P, U, I, C.POINTER(I), I, L)
        self.keysym = bind(self.x, "XStringToKeysym", L, C.c_char_p)
        self.keycode = bind(self.x, "XKeysymToKeycode", U, P, L)

    def move(self, x, y):
        if x < 0 or y < 0:
            raise ValueError("Coordinates must be nonnegative")
        self.warp(self.d, self.pointer, 0, self.root, 0, 0, 0, 0, x, y)
        self.sync(self.d, 0)

    def key(self, window, key):
        # Only the virtual keyboard focus is changed, never core focus.
        code = self.keycode(self.d, self.keysym(key.encode("ascii")))
        if not code:
            raise ValueError("Unknown/unmapped X keysym")
        self.focus(self.d, self.keyboard, window, 0)
        dev = self.open_device(self.d, device(" XTEST keyboard"))
        if not dev:
            raise RuntimeError("Virtual keyboard unavailable")
        try:
            self.fake_key(self.d, dev, code, 1, None, 0, 0)
            self.fake_key(self.d, dev, code, 0, None, 0, 0)
            self.sync(self.d, 0)
        finally:
            self.close_device(self.d, dev)

    def close(self):
        bind(self.x, "XCloseDisplay", I, P)(self.d)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["start", "status", "stop", "move", "key"])
    parser.add_argument("values", nargs="*")
    args = parser.parse_args()
    if args.command in {"move", "key"} and os.environ.get("SEMANTIC_CU_ENABLE_AUX") != "1":
        raise PermissionError("Auxiliary input is disabled by default; explicit SEMANTIC_CU_ENABLE_AUX=1 is required")
    if args.command == "start":
        start()
    elif args.command == "stop":
        stop()
    elif args.command == "status":
        print(run("list"))
    else:
        twin = Twin()
        try:
            if args.command == "move":
                twin.move(*map(int, args.values))
            else:
                twin.key(int(args.values[0], 0), args.values[1])
        finally:
            twin.close()

if __name__ == "__main__":
    main()
