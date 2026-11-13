"""Offscreen VS Code/Fcitx probe; transient user service owns entire process tree.
No generic application launcher or host-display viewer. No automatic source save.
"""
import json,os,secrets,subprocess,sys,time,signal,re,fcntl
from pathlib import Path
from .isolated_session import IsolatedSession, atomic_json
TEST_ROOT=Path(os.environ.get('VERY_CU_ISOLATED_ROOT',str(Path.home()/'.local/share/VERY-computer-use/isolated'))).expanduser().resolve()

def validate_workdir(path,root=TEST_ROOT):
    path=Path(path).resolve();root=Path(root).resolve()
    if path==root or root not in path.parents:raise ValueError('Dedicated child test directory required')
    return path

def private_app_environment(display_env, state):
    state=Path(state)
    env={k:display_env[k] for k in ['DISPLAY','XAUTHORITY','PATH','LANG','LC_ALL','PYTHONPATH'] if k in display_env}
    for key,folder in [('HOME','home'),('XDG_CONFIG_HOME','config'),('XDG_CACHE_HOME','cache'),('XDG_DATA_HOME','data'),('XDG_RUNTIME_DIR','runtime')]:
        p=state/folder;p.mkdir(mode=0o700,parents=True,exist_ok=True);env[key]=str(p)
    env['VERY_CU_ISOLATED_ROOT']=str(TEST_ROOT)
    env.update(XDG_SESSION_TYPE='x11',GTK_IM_MODULE='fcitx',QT_IM_MODULE='fcitx',XMODIFIERS='@im=fcitx',PYTHONDONTWRITEBYTECODE='1')
    return env

def initialize_file(path, text):
    try:
        with Path(path).open('x') as f:f.write(text)
    except FileExistsError:pass

def cleanup_private_state(state,root=TEST_ROOT):
    state=validate_workdir(state,root)
    records=list((state/'display').glob('session-*/ownership.json'))
    if (state/'ready.json').exists():records.append(state/'ready.json')
    report=dict(cleaned=[],skipped=[],failed=[],complete=True)
    known_auth=set()
    for record in records:
        try:
            data=json.loads(record.read_text());auth=Path(data['auth'])
            if auth.is_symlink() or state not in auth.resolve().parents:
                raise ValueError('Refusing external authorization file')
            known_auth.add(auth)
            pid=int(data['server_pid'])
            if Path(f'/proc/{pid}').exists():
                report['skipped'].append(str(record));continue
            auth.unlink(missing_ok=True)
            display=data['display']
            if not re.fullmatch(r':1[0-9]{2}',display):
                report['skipped'].append(str(record));continue
            number=display[1:];lock=Path('/tmp/.X'+number+'-lock')
            socket=Path('/tmp/.X11-unix/X'+number)
            if lock.exists():
                if lock.is_symlink() or lock.stat().st_uid!=os.getuid() or int(lock.read_text().strip())!=pid:
                    report['skipped'].append(str(record));continue
                if socket.exists():
                    if socket.is_symlink() or socket.stat().st_uid!=os.getuid():
                        report['skipped'].append(str(record));continue
                    socket.unlink()
                lock.unlink()
            elif socket.exists():
                report['skipped'].append(str(record));continue
            report['cleaned'].append(str(record))
        except (OSError,ValueError,KeyError,TypeError) as exc:
            report['failed'].append(dict(record=str(record),error=str(exc)))
    for auth in (state/'display').glob('session-*/Xauthority'):
        if auth not in known_auth:report['skipped'].append(str(auth))
    report['complete']=not report['skipped'] and not report['failed']
    atomic_json(state/'cleanup-report.json',report)
    return report

def start_probe():
    TEST_ROOT.mkdir(mode=0o700,parents=True,exist_ok=True)
    state=TEST_ROOT/('.isolated-'+secrets.token_hex(8));state.mkdir(mode=0o700)
    unit='semantic-cu-isolated-'+secrets.token_hex(8)
    command=['systemd-run','--user','--unit='+unit,'--collect','--property=KillMode=control-group',
             '--property=TimeoutStopSec=5','--property=RuntimeMaxSec=180',
             '--property=ExecStopPost='+sys.executable+' -m semantic_cu.isolated_desktop --cleanup '+str(state),
             '--setenv=VERY_CU_ISOLATED_ROOT='+str(TEST_ROOT),
             '--setenv=PYTHONPATH='+str(Path(__file__).resolve().parents[1]),
             sys.executable,'-m','semantic_cu.isolated_desktop','--controller',str(state)]
    subprocess.run(command,check=True,timeout=10,capture_output=True,text=True)
    return unit,state

def stop_probe(unit):
    if not unit.startswith('semantic-cu-isolated-') or not all(c in '0123456789abcdef' for c in unit.removeprefix('semantic-cu-isolated-')):
        raise ValueError('Unrecognized owned unit')
    subprocess.run(['systemctl','--user','stop',unit],check=True,timeout=12,capture_output=True)

def controller(state):
    state=validate_workdir(state)
    lock=(state/'controller.lock').open('a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    session=IsolatedSession(state/'display',1200,1300)
    def stop(*_):raise SystemExit()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    try:
        session.start()
        env=private_app_environment(session.env,state/'app')
        child=subprocess.Popen(['/usr/bin/dbus-run-session','--',sys.executable,'-m','semantic_cu.isolated_desktop','--apps',str(state)],env=env)
        atomic_json(state/'ready.json',dict(display=session.display,auth=env['XAUTHORITY'],pid=os.getpid(),server_pid=session.process.pid,stage='display-ready'))
        while child.poll() is None and session.running:time.sleep(.2)
        raise RuntimeError('Private app supervisor or X server exited')
    finally:
        session.close()
        (state/'closed').write_text('server closed')

def apps(state):
    state=validate_workdir(state)
    profile=state/'app'/'code-profile';(profile/'User').mkdir(parents=True,exist_ok=True)
    initialize_file(profile/'User'/'settings.json',json.dumps({'telemetry.telemetryLevel':'off','update.mode':'none','extensions.autoUpdate':False,'extensions.autoCheckUpdates':False,'workbench.startupEditor':'none','files.autoSave':'off','editor.autoIndent':'none','editor.autoClosingBrackets':'never','editor.autoClosingQuotes':'never'}))
    work=state/'work';work.mkdir(exist_ok=True)
    initialize_file(work/'offscreen_probe.py','# private disposable input probe\n')
    ime=subprocess.Popen(['/usr/bin/fcitx5','-D'],env=os.environ)
    args=['/usr/share/code/code','--new-window','--disable-extensions','--skip-welcome','--skip-release-notes',
          '--user-data-dir',str(profile),'--extensions-dir',str(state/'app'/'extensions'),str(work/'offscreen_probe.py')]
    app=subprocess.Popen(args,env=os.environ)
    atomic_json(state/'app-ready.json',dict(code_pid=app.pid,ime_pid=ime.pid,bus=os.environ['DBUS_SESSION_BUS_ADDRESS'],stage='spawned-not-yet-verified'))
    while app.poll() is None and ime.poll() is None:time.sleep(.2)
    raise RuntimeError('Private Code or Fcitx exited')

if __name__=='__main__':
    if len(sys.argv)!=3:raise SystemExit('Explicit controller/apps mode required')
    if sys.argv[1]=='--controller':controller(sys.argv[2])
    elif sys.argv[1]=='--apps':apps(sys.argv[2])
    elif sys.argv[1]=='--cleanup':cleanup_private_state(sys.argv[2])
    else:raise SystemExit('Unknown mode')
