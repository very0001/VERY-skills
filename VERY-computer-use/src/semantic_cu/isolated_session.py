"""Opt-in, offscreen single-seat prototype. No host-display input or preview.
Not a security sandbox. Existing applications cannot be moved into this session.
"""
import hashlib
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import time
from dataclasses import dataclass


def atomic_json(path, value):
    path=Path(path);tmp=path.with_name(path.name+'.tmp-'+secrets.token_hex(8))
    fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(fd,'w') as f:
            json.dump(value,f);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        tmp.unlink(missing_ok=True)

class IsolatedSession:
    def __init__(self, state_root, width=1200, height=1300):
        if not 64 <= width <= 4096 or not 64 <= height <= 4096:
            raise ValueError('Geometry must be between 64 and 4096')
        self.root=Path(state_root).resolve()
        self.width,self.height=width,height
        self.process=None
        self.display=None
        self.directory=None
        self.log=None
        self._env=None
    @property
    def running(self):
        return self.process is not None and self.process.poll() is None
    @property
    def env(self):
        if not self.running or self._env is None:
            raise RuntimeError('Private display is not running')
        return dict(self._env)
    def start(self):
        try:
            return self._start()
        except BaseException:
            self.close()
            raise
    def _start(self):
        if self.running:return self.display
        if self.process is not None:self.close()
        self.root.mkdir(parents=True,exist_ok=True)
        self.directory=self.root/('session-'+secrets.token_hex(8))
        self.directory.mkdir(mode=0o700)
        for folder in ['config','cache','data','runtime']:
            (self.directory/folder).mkdir(mode=0o700)
        number=next((n for n in range(100,200) if not Path(f'/tmp/.X{n}-lock').exists()
                     and not Path(f'/tmp/.X11-unix/X{n}').exists()),None)
        if number is None:raise RuntimeError('No unused private display slot')
        self.display=f':{number}'
        auth=self.directory/'Xauthority'
        auth.touch(mode=0o600)
        # Pass secret on stdin, never in argv or logs.
        subprocess.run(['xauth','-f',str(auth)],input=f'add {self.display} MIT-MAGIC-COOKIE-1 {secrets.token_hex(16)}\n',
                       text=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,check=True,timeout=3)
        env=dict(os.environ)
        for key in ['DISPLAY','WAYLAND_DISPLAY','XAUTHORITY','DBUS_SESSION_BUS_ADDRESS',
                    'SESSION_MANAGER','VSCODE_IPC_HOOK_CLI','AT_SPI_BUS_ADDRESS']:
            env.pop(key,None)
        env.update(DISPLAY=self.display,XAUTHORITY=str(auth),XDG_SESSION_TYPE='x11',
                   XDG_CONFIG_HOME=str(self.directory/'config'),XDG_CACHE_HOME=str(self.directory/'cache'),
                   XDG_DATA_HOME=str(self.directory/'data'),XDG_RUNTIME_DIR=str(self.directory/'runtime'))
        self._env=env
        self.log=(self.directory/'server.log').open('wb')
        command=['/usr/bin/Xtigervnc',self.display,'-geometry',f'{self.width}x{self.height}',
                 '-depth','24','-rfbport','-1','-nolisten','tcp','-auth',str(auth),'-noreset']
        wrapper=[sys.executable,'-m','semantic_cu.isolated_session','--owned-child',str(os.getpid()),*command]
        try:
            self.process=subprocess.Popen(wrapper,env=env,stdin=subprocess.DEVNULL,stdout=self.log,
                                          stderr=self.log,start_new_session=True)
            atomic_json(self.directory/'ownership.json',dict(display=self.display,auth=str(auth),server_pid=self.process.pid))
            deadline=time.monotonic()+8
            while self.running and time.monotonic()<deadline:
                # Xorg's lock is written by the actual server. A raced slot
                # must never be mistaken for this object's display.
                lock=Path(f'/tmp/.X{number}-lock')
                if not lock.exists():
                    time.sleep(.05)
                    continue
                if int(lock.read_text().strip()) != self.process.pid:
                    raise RuntimeError('Display slot belongs to another process')
                check=subprocess.run(['xdpyinfo','-display',self.display],env=env,stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL,timeout=1)
                if check.returncode==0:return self.display
                time.sleep(.05)
            raise RuntimeError('Private X server failed to become ready; inspect local server.log')
        except BaseException:
            self.close()
            raise
    def close(self):
        # Only PID/process groups created by this object; never kill by name/display.
        for p in ([self.process] if self.process else []):
            if p is None:continue
            if p.poll() is None:
                try:os.killpg(p.pid,signal.SIGTERM)
                except ProcessLookupError:pass
                try:p.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    try:os.killpg(p.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                    p.wait(timeout=3)
        self.process=None
        self._env=None
        if self.log:self.log.close();self.log=None
        # Keep logs/profiles/proposals; revoke only our ephemeral cookie.
        if self.directory:
            (self.directory/'Xauthority').unlink(missing_ok=True)
        self.display=None

@dataclass(frozen=True)
class EditProposal:
    source:Path
    digest:str
    @classmethod
    def capture(cls,path):
        path=Path(path)
        if path.is_symlink() or not path.is_file():raise ValueError('Regular source file required')
        return cls(path.resolve(),hashlib.sha256(path.read_bytes()).hexdigest())
    def conflicted(self):
        try:
            return self.source.is_symlink() or hashlib.sha256(self.source.read_bytes()).hexdigest()!=self.digest
        except OSError:return True
    def stage(self,text,directory):
        """Non-destructive proposal only. No check-then-overwrite race with editors."""
        directory=Path(directory)
        directory.mkdir(parents=True,exist_ok=True)
        name=self.source.name+'.proposal-'+secrets.token_hex(8)
        target=directory/name
        with target.open('x',encoding='utf-8') as f:f.write(text)
        metadata=dict(source=str(self.source),base_sha256=self.digest,conflicted=self.conflicted(),
                      automatic_publish=False)
        with (directory/(name+'.json')).open('x',encoding='utf-8') as f:json.dump(metadata,f)
        return target

if __name__=='__main__':
    if len(sys.argv)<4 or sys.argv[1]!='--owned-child':raise SystemExit('Internal child launcher only')
    from .auxiliary_guard import arm_parent_death
    arm_parent_death(int(sys.argv[2]))
    os.execvpe(sys.argv[3],sys.argv[3:],os.environ)
