import importlib.util
from pathlib import Path
import subprocess
from types import SimpleNamespace
import pytest

from semantic_cu import literal_input as m

class Backend:
    def __init__(self):self.window=7;self.pid=10;self.visible=True;self.focused=True
    def call_tool(self,name,args):
        assert name=='focused_window' and args=={}
        return {'structuredContent':{'focused_window':{'window_id':self.window,'pid':self.pid,'focused':self.focused,'hidden':not self.visible}}}

class IM:
    def __init__(self):self.state=2;self.engine='rime';self.calls=[];self.fail=None;self.no_confirm=False
    def call(self,name):
        self.calls.append(name)
        if name=='Deactivate':
            self.state=1
            if self.fail==name:raise subprocess.TimeoutExpired('gdbus',1)
            if self.no_confirm:self.state=2
            return ()
        if self.fail==name:raise RuntimeError('private failure details')
        if name=='State':return (self.state,)
        if name=='CurrentInputMethod':return (self.engine if self.state==2 else 'keyboard-us',)
        if name=='Activate':self.state=2;return ()
        raise AssertionError(name)

def setup():
    b=Backend();g=m.LiteralInputGuard(b,7,10);im=IM();g._call=im.call
    return b,g,im

def test_active_guard_restores_once():
    b,g,im=setup(); assert g.start()['status']=='disabled_for_literal_input';assert im.state==1
    assert g.restore()['status']=='restored';assert im.state==2
    g.restore();assert im.calls.count('Activate')==1

@pytest.mark.parametrize('state',[0,1])
def test_inactive_guard_does_not_activate(state):
    b,g,im=setup();im.state=state
    assert g.start()['status']=='already_inactive';assert g.restore()['status']=='not_needed'
    assert 'Deactivate' not in im.calls and 'Activate' not in im.calls

def test_provider_absent_is_noop():
    b,g,im=setup()
    def absent(name):raise m.ProviderUnavailable()
    g._call=absent
    assert g.start()['status']=='unavailable';assert g.restore()['status']=='not_needed'

@pytest.mark.parametrize('field,value',[('window',8),('pid',11),('visible',False),('focused',False)])
def test_focus_mismatch_changes_nothing(field,value):
    b,g,im=setup();setattr(b,field,value)
    with pytest.raises(m.LiteralInputError,match='focus_changed'):g.start()
    assert not im.calls

def test_focus_drift_during_queries_does_not_deactivate():
    b,g,im=setup();original=im.call
    def call(name):
        r=original(name)
        if name=='CurrentInputMethod':b.window=8
        return r
    g._call=call
    with pytest.raises(m.LiteralInputError,match='focus_changed'):g.start()
    assert im.state==2 and 'Deactivate' not in im.calls

def test_focus_drift_skips_restore_on_new_window():
    b,g,im=setup();g.start();b.window=8
    assert g.restore()['status']=='restoration_pending';assert im.state==1
    assert 'Activate' not in im.calls

def test_user_switching_engine_not_overridden():
    b,g,im=setup();g.start();im.state=2;im.engine='pinyin'
    assert g.restore()['reason']=='input_method_changed';assert 'Activate' not in im.calls

def test_deactivate_timeout_still_arms_cleanup():
    b,g,im=setup();im.fail='Deactivate'
    with pytest.raises(m.LiteralInputError,match='deactivation_failed'):g.start()
    im.fail=None
    assert g.restore()['status']=='restored';assert im.state==2

def test_deactivation_not_confirmed_refuses_input():
    b,g,im=setup();im.no_confirm=True
    with pytest.raises(m.LiteralInputError,match='deactivation_not_confirmed'):g.start()
    assert g.restore()['status']=='already_restored'

def test_restore_failure_is_reported_without_sensitive_error():
    b,g,im=setup();g.start();im.fail='Activate'
    result=g.restore();assert result['status']=='restoration_failed'
    assert 'private failure' not in str(result)

def test_query_failure_is_not_silently_treated_as_absent():
    b,g,im=setup();im.fail='State'
    with pytest.raises(m.LiteralInputError,match='ime_query_failed'):g.start()
    assert 'Deactivate' not in im.calls

def test_real_command_runner_uses_literal_argv_and_bounded_timeout(monkeypatch):
    calls=[]
    def run(argv,**kwargs):
        calls.append((argv,kwargs));return SimpleNamespace(returncode=0,stdout="(':1.42',)\n" if argv[-1]=='org.fcitx.Fcitx5' else "('rime',)\n",stderr='')
    monkeypatch.setattr(m.subprocess,'run',run)
    g=m.LiteralInputGuard(Backend(),7)
    assert g._call('CurrentInputMethod')==('rime',)
    assert calls[0][0][-2]=='org.freedesktop.DBus.GetNameOwner'
    argv,kw=calls[1];assert argv[0]=='/usr/bin/gdbus';assert argv[-1].endswith('.CurrentInputMethod')
    assert argv[argv.index('--dest')+1]==':1.42'
    assert kw['timeout']<=1 and kw.get('shell',False) is False
    with pytest.raises(ValueError):g._call('SetCurrentIM')

def test_unknown_service_classified_unavailable(monkeypatch):
    monkeypatch.setattr(m.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=1,stdout='',stderr='GDBus.Error:org.freedesktop.DBus.Error.ServiceUnknown: no owner'))
    with pytest.raises(m.ProviderUnavailable):m.LiteralInputGuard(Backend(),7)._call('State')


def test_errors_expose_machine_readable_code():
    assert m.LiteralInputError('ime_query_failed').code=='ime_query_failed'

def test_query_timeout_is_not_silently_treated_as_absent():
    b,g,im=setup()
    def timeout(name):raise subprocess.TimeoutExpired('gdbus',.8)
    g._call=timeout
    with pytest.raises(m.LiteralInputError,match='ime_query_failed'):g.start()
    assert g.restore()['status']=='not_needed'

def test_malformed_state_refuses_typing():
    b,g,im=setup();g._call=lambda name: ('invalid',)
    with pytest.raises(m.LiteralInputError,match='ime_query_failed'):g.start()

def test_restore_after_user_already_reactivated_original_is_noop():
    b,g,im=setup();g.start();im.state=2
    assert g.restore()['status']=='already_restored'
    assert 'Activate' not in im.calls
