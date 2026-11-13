"""Per-display input lease and supervised auxiliary device lifetime."""
import contextlib
import ctypes
import fcntl
import hashlib
import json
import os
import select
import re
import stat
import signal
import subprocess
import sys
import time
import uuid

def lock_path():
    display = os.environ.get("DISPLAY", "").removeprefix("unix/")
    match = re.fullmatch(r"(.*):([0-9]+)(?:\.[0-9]+)?", display)
    if not match:
        raise RuntimeError("Unrecognized X11 display identity")
    host, number = match.groups()
    if host in ("", "unix", "localhost", "127.0.0.1", os.uname().nodename):
        host = ""
    if host:
        raise RuntimeError("Only a canonical local X11 display is supported")
    digest = hashlib.sha256((":" + str(int(number))).encode()).hexdigest()[:16]
    return "/tmp/semantic-cu-input-" + str(os.getuid()) + "-" + digest + ".lock"

@contextlib.contextmanager
def input_lock(allow_owner=None, recover_stale=False):
    fd = os.open(lock_path(), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if os.fstat(fd).st_uid != os.getuid() or not stat.S_ISREG(os.fstat(fd).st_mode):
            raise RuntimeError("Input lock is owned by another user")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another gateway owns this display's input lease")
        os.lseek(fd, 0, os.SEEK_SET)
        data = os.read(fd, 4096)
        if data:
            owner = json.loads(data).get("owner")
            if owner != allow_owner and not recover_stale:
                raise RuntimeError("Unclean auxiliary lease requires recovery before input")
        yield fd
    finally:
        os.close(fd)

def recover_stale_lease():
    """Explicit host-approved cleanup only; never creates or reattaches input."""
    with input_lock(recover_stale=True) as lease:
        os.lseek(lease, 0, os.SEEK_SET)
        data = os.read(lease, 4096)
        if not data:
            return {"active": False, "recovered": False}
        owner = json.loads(data).get("owner", "")
        if not re.fullmatch(r"SemanticCU-[0-9]+-[0-9a-f]{8}", owner):
            raise RuntimeError("Unrecognized lease owner; refusing recovery")
        result = worker(owner, "stop")
        if result.get("active") is not False:
            raise RuntimeError("Recovery was not confirmed")
        os.ftruncate(lease, 0)
        os.fsync(lease)
        return dict(result, recovered=True)

def arm_parent_death(expected_parent):
    """Linux worker safeguard, armed before any X11 operations."""
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "Cannot arm parent-death safeguard")
    if os.getppid() != expected_parent:
        raise RuntimeError("Input worker owner already exited")

def worker(name, operation, arguments=None, watch=None):
    args = arguments or {}
    payload = json.dumps(dict(name=name, operation=operation, arguments=args))
    process = subprocess.Popen(
        [sys.executable, "-m", "semantic_cu.auxiliary_input", "--worker"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, start_new_session=True,
        env=dict(os.environ, SEMANTIC_CU_WORKER_OWNER_PID=str(os.getpid())))
    deadline = time.monotonic() + (args["max_seconds"] if operation == "batch" else 5 + len(args.get("text", "")) * .38)
    try:
        while True:
            if watch is not None:
                watch()
            if time.monotonic() >= deadline:
                raise TimeoutError("Native input worker timed out")
            try:
                output, error = process.communicate(payload, timeout=.05)
                break
            except subprocess.TimeoutExpired:
                payload = None
        if process.returncode:
            raise RuntimeError(error[-1000:])
        return json.loads(output)
    except BaseException:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate(timeout=2)
        raise

class AuxInput:
    def __init__(self, session_seconds=None):
        if session_seconds is not None and (type(session_seconds) not in (int, float) or not 5 <= session_seconds <= 300):
            raise ValueError("Session timeout must be 5..300 seconds")
        self.session_seconds = session_seconds
        self.name = "SemanticCU-" + str(os.getpid()) + "-" + uuid.uuid4().hex[:8]
        self.started = False
        self.cleanup_pending = False
        self.guard = None
    def receive(self, timeout):
        if not select.select([self.guard.stdout], [], [], timeout)[0]:
            raise TimeoutError("Auxiliary supervisor response timed out; do not retry")
        line = self.guard.stdout.readline()
        if not line:
            raise RuntimeError("Auxiliary supervisor exited unexpectedly")
        reply = json.loads(line)
        if not reply["ok"]:
            raise RuntimeError(reply["error"])
        return reply["result"]
    def start(self):
        if os.environ.get("SEMANTIC_CU_ENABLE_AUX") != "1":
            raise PermissionError("Auxiliary input is disabled; enable only for an agreed live test")
        if os.environ.get("XDG_SESSION_TYPE") != "x11":
            raise RuntimeError("Native X11 required")
        if self.started and self.guard is not None and self.guard.poll() is None:
            return self.run("status")
        if self.cleanup_pending or self.guard is not None:
            self.close()
        self.guard = subprocess.Popen(
            [sys.executable, "-m", "semantic_cu.auxiliary_guard", self.name],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1, start_new_session=True,
            env=(dict(os.environ, SEMANTIC_CU_AUX_SESSION_SECONDS=str(self.session_seconds))
                 if self.session_seconds is not None else None))
        self.cleanup_pending = True
        try:
            result = self.receive(10)
        except Exception as exc:
            # A known collision never grants ownership of that pair.
            if any(marker in str(exc) for marker in (
                    "Name collision", "Another gateway owns",
                    "Unclean auxiliary lease", "Only a canonical local X11")):
                self.cleanup_pending = False
            self.close()
            raise
        self.started = True
        return result
    def run(self, operation, arguments=None):
        alive = self.guard is not None and self.guard.poll() is None
        if not self.started or not alive:
            self.started = False
            if operation == "status":
                result = worker(self.name, "status") if self.cleanup_pending else {"active": False}
                result["cleanup_pending"] = self.cleanup_pending
                return result
            raise RuntimeError("Auxiliary supervisor is not running; cleanup may be required")
        self.guard.stdin.write(json.dumps(
            {"operation": operation, "arguments": arguments or {}}) + "\n")
        self.guard.stdin.flush()
        return self.receive((arguments["max_seconds"] + 10) if operation == "batch" else 10 + len((arguments or {}).get("text", "")) * .38)
    def cancel(self):
        """Request cancellation without racing the execution thread for stdout."""
        guard = self.guard
        if guard is not None and guard.stdin is not None and not guard.stdin.closed:
            try: guard.stdin.close()
            except (BrokenPipeError, OSError): pass

    def close(self):
        self.started = False
        if self.guard is not None:
            if self.guard.stdin is not None and not self.guard.stdin.closed:
                self.guard.stdin.close()
            # EOF makes the supervisor cancel any active worker and clean up.
            self.guard.wait(timeout=20)
            self.guard.stdout.close()
            self.guard = None
        if not self.cleanup_pending:
            return {"active": False}
        # Preserve cleanup responsibility if either probe or stop fails.
        with input_lock(allow_owner=self.name) as lease:
            result = worker(self.name, "stop")
            if result.get("active") is not False:
                raise RuntimeError("Device cleanup is not confirmed")
            os.ftruncate(lease, 0)
            os.fsync(lease)
        self.cleanup_pending = False
        return result

def _holder_alive(holder):
    return (holder is not None and holder.process is not None
            and holder.process.poll() is None)


def _remove_owned_pair(name, holder):
    """Keep a live cursor owner and display lease until removal is confirmed.

    A failed stop enters cleanup-only retry, not a new input session. The caller
    retains the lock. It may time out waiting for us, but must not claim recovery.
    Abrupt supervisor/holder death cannot preserve this ordering.
    """
    while True:
        try:
            result = worker(name, "stop")
            if result.get("active") is not False:
                raise RuntimeError("Device removal was not confirmed")
        except Exception:
            if not _holder_alive(holder):
                if holder is not None:
                    holder.close()  # Reap an already-dead holder; keep stale lease.
                raise
            # Exiting here would trigger holder's parent-death SIGKILL while
            # the device might still use its cursor. Retain both lock and holder.
            time.sleep(1)
            continue
        if holder is not None:
            holder.close()
        return result


def supervise(name):
    if os.environ.get("SEMANTIC_CU_ENABLE_AUX") != "1":
        raise PermissionError("Auxiliary live input is disabled")
    duration = float(os.environ.get("SEMANTIC_CU_AUX_SESSION_SECONDS", "30"))
    if not 5 <= duration <= 300:
        raise ValueError("Experimental session limit must be 5..300 seconds")
    session_deadline = time.monotonic() + duration
    held = {}
    cursor_holder = None
    cleaning = False
    last_release = None
    incoming = bytearray()
    def watch():
        if cursor_holder is not None and not _holder_alive(cursor_holder):
            raise RuntimeError("Auxiliary cursor holder exited")
        if time.monotonic() >= session_deadline:
            raise TimeoutError("Experimental auxiliary session expired")
        if any(item["deadline"] <= time.monotonic() for item in held.values()):
            raise TimeoutError("Held-input lease expired during action")
        if select.select([sys.stdin], [], [], 0)[0]:
            if not os.read(sys.stdin.fileno(), 1):
                raise EOFError("Input owner disconnected")
            raise RuntimeError("Concurrent supervisor requests are not allowed")
    def reply(ok, value):
        print(json.dumps({"ok": ok, "result" if ok else "error": value}), flush=True)
    def release(reason):
        nonlocal last_release
        if held:
            worker(name, "release_all", {"held": list(held.values())})
            held.clear()
        last_release = reason
    def shutdown(signum, frame):
        if not cleaning:
            raise SystemExit()
        # Repeated graceful signals must not destroy a retained live cursor.

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    with input_lock() as lease:
        os.write(lease, json.dumps({"owner": name}).encode())
        os.fsync(lease)
        created = False
        try:
            created = True
            try:
                result = worker(name, "start", watch=watch)
            except Exception as exc:
                if "Name collision" in str(exc):
                    created = False
                raise
            reply(True, result)
            while True:
                if cursor_holder is not None and not _holder_alive(cursor_holder):
                    raise RuntimeError("Auxiliary cursor holder exited while idle")
                if time.monotonic() >= session_deadline:
                    break
                if any(item["deadline"] <= time.monotonic() for item in held.values()):
                    release("lease_expired")
                if not select.select([sys.stdin], [], [], .1)[0]:
                    continue
                chunk = os.read(sys.stdin.fileno(), 65536)
                if not chunk:
                    break
                incoming.extend(chunk)
                if len(incoming) > 65536:
                    raise ValueError("Supervisor request too large")
                if b"\n" not in incoming:
                    continue
                line, trailing = incoming.split(b"\n", 1)
                incoming.clear()
                if trailing:
                    raise ValueError("Concurrent supervisor requests are not allowed")
                request = json.loads(line)
                op, args = request["operation"], request["arguments"]
                if op == "stop":
                    release("stop")
                    cleaning = True
                    result = _remove_owned_pair(name, cursor_holder)
                    created = False
                    reply(True, result)
                    break
                if op == "release_all":
                    release("explicit")
                    reply(True, {"held": [], "last_release": last_release})
                    continue
                if held and op in ("text", "chord", "drag", "batch"):
                    reply(False, "Release held inputs before text/chord/drag")
                    continue
                if held and args.get("window_id") is not None and any(
                        item["window_id"] != args["window_id"] for item in held.values()):
                    reply(False, "Held input is bound to another observed window")
                    continue
                if ((op == "key" and "key:" + str(args.get("key")) in held)
                        or (op == "click" and "button:" + str(args.get("button")) in held)):
                    reply(False, "Use explicit up for an already held input")
                    continue
                ident = None
                if op in ("key_down", "key_up", "button_down", "button_up"):
                    kind = "key" if op.startswith("key") else "button"
                    ident = kind + ":" + str(args[kind])
                    if op.endswith("_down") and ident in held:
                        reply(False, "Input is already held")
                        continue
                    if op.endswith("_up") and ident not in held:
                        reply(False, "Input is not held by this supervisor")
                        continue
                    if op.endswith("_down"):
                        lease_seconds = args.get("lease_seconds")
                        if (type(lease_seconds) not in (int, float)
                                or not .2 <= lease_seconds <= 10 or len(held) >= 16):
                            reply(False, "Invalid lease or too many held inputs")
                            continue
                        held[ident] = dict(kind=kind, value=args[kind],
                            window_id=args["window_id"],
                            deadline=time.monotonic() + args["lease_seconds"])
                try:
                    if op not in {"status", "release_all"} and cursor_holder is None:
                        from .auxiliary_cursor import CursorHolder
                        cursor_holder = CursorHolder(name, args.get("window", args.get("window_id")))
                        cursor_holder.start()
                    result = worker(name, op, args, watch=watch)
                    if ident and op.endswith("_up"):
                        held.pop(ident, None)
                    if op == "status":
                        result["held"] = list(held.values())
                        result["last_release"] = last_release
                    reply(True, result)
                except Exception as exc:
                    reply(False, str(exc))
                    break
        finally:
            cleaning = True
            if created:
                try:
                    release("exit")
                finally:
                    result = _remove_owned_pair(name, cursor_holder)
                    if result.get("active") is False:
                        os.ftruncate(lease, 0)
                        os.fsync(lease)
            else:
                os.ftruncate(lease, 0)
                os.fsync(lease)

if __name__ == "__main__":
    try:
        supervise(sys.argv[1])
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), flush=True)
        