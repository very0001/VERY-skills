import importlib.util
import os
from pathlib import Path

import pytest

from semantic_cu import desktop_env as module

BASE = {'DISPLAY': ':1', 'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/run/user/1000/bus',
        'XDG_RUNTIME_DIR': '/run/user/1000', 'XDG_SESSION_TYPE': 'x11',
        'XAUTHORITY': '/run/user/1000/gdm/Xauthority'}


def process(root, pid=123, executable='/usr/bin/gnome-shell', env=None):
    p = root / str(pid); p.mkdir()
    (p/'exe').symlink_to(executable)
    (p/'comm').write_text(Path(executable).name+'\n')
    # Linux stat field 22 is starttime, at index 19 after the command field.
    (p/'stat').write_text(f'{pid} (gnome-shell) '+' '.join(['S']+['0']*18+['42']+['0']*5))
    values = dict(BASE if env is None else env)
    values['SECRET_TEST'] = 'do-not-copy-this'
    (p/'environ').write_bytes(b'\0'.join(f'{k}={v}'.encode() for k,v in values.items())+b'\0')
    return p


def test_complete_inherited_environment_needs_no_proc(tmp_path):
    env = dict(BASE, CUSTOM='preserved')
    effective, info = module.resolve_desktop_environment(env, tmp_path/'missing')
    assert effective == env and effective is not env
    assert info['source'] == 'inherited'


def test_unique_same_uid_recovers_whitelist_only(tmp_path):
    process(tmp_path)
    original={'PATH':'kept'}
    effective,info=module.resolve_desktop_environment(original,tmp_path)
    assert effective == dict(BASE, PATH='kept')
    assert original == {'PATH':'kept'}
    assert info['source_pid']==123
    assert 'do-not-copy-this' not in repr(info)


def test_multiple_shells_refused(tmp_path):
    process(tmp_path);process(tmp_path,124)
    with pytest.raises(RuntimeError,match='multiple'):
        module.resolve_desktop_environment({},tmp_path)


def test_no_candidate_refused(tmp_path):
    process(tmp_path,executable='/usr/bin/unrelated')
    with pytest.raises(RuntimeError,match='no_graphical'):
        module.resolve_desktop_environment({},tmp_path)


def test_different_uid_is_not_a_candidate(tmp_path):
    process(tmp_path)
    with pytest.raises(RuntimeError,match='no_graphical'):
        module.resolve_desktop_environment({},tmp_path,uid=os.getuid()+1)


def test_conflict_names_only(tmp_path):
    process(tmp_path)
    with pytest.raises(RuntimeError) as error:
        module.resolve_desktop_environment({'DISPLAY':'secret-other-display'},tmp_path)
    assert 'DISPLAY' in str(error.value)
    assert 'secret-other-display' not in str(error.value)


def test_empty_values_can_be_filled(tmp_path):
    process(tmp_path)
    env,_=module.resolve_desktop_environment({'DISPLAY':''},tmp_path)
    assert env['DISPLAY']==':1'


def test_incomplete_candidate_refused(tmp_path):
    process(tmp_path,env={'DISPLAY':':1'})
    with pytest.raises(RuntimeError,match='DBUS_SESSION_BUS_ADDRESS'):
        module.resolve_desktop_environment({},tmp_path)


def test_unreadable_candidate_refused(tmp_path):
    p=process(tmp_path);(p/'environ').unlink()
    with pytest.raises(RuntimeError,match='unreadable'):
        module.resolve_desktop_environment({},tmp_path)


def test_pid_reuse_is_rejected(tmp_path,monkeypatch):
    process(tmp_path)
    read=module._read_session_environment
    def replacing(p):
        value=read(p)
        (p/'stat').write_text((p/'stat').read_text().replace('42','43'))
        return value
    monkeypatch.setattr(module,'_read_session_environment',replacing)
    with pytest.raises(RuntimeError,match='changed'):
        module.resolve_desktop_environment({},tmp_path)


def test_wayland_inherited_environment(tmp_path):
    env=dict(BASE);env.pop('DISPLAY');env['WAYLAND_DISPLAY']='wayland-0';env['XDG_SESSION_TYPE']='wayland'
    result,info=module.resolve_desktop_environment(env,tmp_path/'missing')
    assert result==env and info['source']=='inherited'


def test_executable_identity_must_be_verified(tmp_path):
    p=process(tmp_path);(p/'exe').unlink()
    with pytest.raises(RuntimeError,match='unreadable'):
        module.resolve_desktop_environment({},tmp_path)


def test_supplied_wayland_does_not_mix_with_x11_candidate(tmp_path):
    process(tmp_path)
    with pytest.raises(RuntimeError,match='WAYLAND_DISPLAY'):
        module.resolve_desktop_environment({'WAYLAND_DISPLAY':'wayland-other'},tmp_path)


def test_exec_identity_change_is_rejected(tmp_path,monkeypatch):
    process(tmp_path);read=module._read_session_environment
    def replacing(p):
        result=read(p);(p/'exe').unlink();(p/'exe').symlink_to('/usr/bin/other')
        return result
    monkeypatch.setattr(module,'_read_session_environment',replacing)
    with pytest.raises(RuntimeError,match='changed'):
        module.resolve_desktop_environment({},tmp_path)


def test_invalid_stat_has_value_free_error(tmp_path):
    p=process(tmp_path);(p/'stat').write_text('secret malformed process metadata')
    with pytest.raises(RuntimeError) as error:
        module.resolve_desktop_environment({},tmp_path)
    assert str(error.value)=='desktop_environment_candidate_unreadable'
