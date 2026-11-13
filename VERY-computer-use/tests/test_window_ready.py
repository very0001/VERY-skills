import copy
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import threading

import pytest

from semantic_cu import window_ready as module
validate_wait_spec=module.validate_wait_spec
wait_for_window=module.wait_for_window
WindowNotReady=module.WindowNotReady


def window(**updates):
    value={'window_id':7,'pid':42,'app_id':'editor','wm_class':'Editor',
           'title':'Ready Document','focused':True,'hidden':False,
           'bounds':{'x':0,'y':0,'width':800,'height':600}}
    value.update(updates)
    return value


def setup(monkeypatch, frames=None):
    state={'clock':0.0,'sleeps':[],'timeouts':[],'calls':[]}
    def sleep(seconds):
        state['sleeps'].append(seconds);state['clock']+=seconds
    monkeypatch.setattr(module.time,'sleep',sleep)
    class Backend:
        timeout=15.0
        def call_tool(self,name,args):
            state['calls'].append((name,args));state['timeouts'].append(self.timeout)
            i=min(len(state['calls'])-1,len(frames)-1)
            return {'structuredContent':{'windows':copy.deepcopy(frames[i]),'error':None}}
    frames=frames if frames is not None else [[window()]]
    backend=Backend()
    session=SimpleNamespace(backend=backend,clock=lambda:state['clock'],allow_actions=True,
                            paused=False,batch_cancel=threading.Event())
    return session,state


def test_ready_window_no_sleep_or_input(monkeypatch):
    s,state=setup(monkeypatch)
    result=wait_for_window(s,{'wm_class':'Editor','title_contains':'Document'})
    assert result['window_id']==7 and result['pid']==42
    assert result['polls']==1 and result['timing_seconds']['window_wait']==0
    assert state['calls']==[('list_windows',{})]
    assert not state['sleeps'] and s.backend.timeout==15


def test_appearance_and_focus_are_polled_locally(monkeypatch):
    s,state=setup(monkeypatch,[[],[window(focused=False)],[window()]])
    result=wait_for_window(s,{'app_id':'editor'})
    assert result['polls']==3
    assert result['timing_seconds']['window_wait']==pytest.approx(.1)
    assert state['sleeps']==[.05,.05]
    assert state['timeouts']==pytest.approx([3,2.95,2.9])
    assert s.backend.timeout==15


@pytest.mark.parametrize('change',[{'hidden':True},{'focused':False},{'bounds':{'width':0,'height':10}}])
def test_not_visible_or_mapped_times_out(monkeypatch,change):
    s,state=setup(monkeypatch,[[window(**change)]])
    with pytest.raises(WindowNotReady) as caught:
        wait_for_window(s,{'window_id':7,'timeout_seconds':.12})
    assert caught.value.code=='timeout'
    assert state['clock']==pytest.approx(.12) and max(state['sleeps'])<=.05
    assert s.backend.timeout==15


def test_additional_fields_must_all_match(monkeypatch):
    s,state=setup(monkeypatch,[[window(pid=43)]])
    with pytest.raises(WindowNotReady) as caught:
        wait_for_window(s,{'window_id':7,'pid':42,'timeout_seconds':.05})
    assert caught.value.code=='timeout'


def test_ambiguous_window_never_guesses(monkeypatch):
    s,state=setup(monkeypatch,[[window(focused=False),window(window_id=8,focused=False)]])
    with pytest.raises(WindowNotReady) as caught:wait_for_window(s,{'wm_class':'Editor'})
    assert caught.value.code=='ambiguous_target'
    assert len(state['calls'])==1 and not state['sleeps']


def test_unique_focused_match_can_disambiguate(monkeypatch):
    s,state=setup(monkeypatch,[[window(focused=False),window(window_id=8)]])
    assert wait_for_window(s,{'wm_class':'editor'})['window_id']==8


def test_missing_focused_is_not_truthy(monkeypatch):
    w=window();w.pop('focused');s,state=setup(monkeypatch,[[w]])
    with pytest.raises(WindowNotReady) as caught:wait_for_window(s,{'window_id':7})
    assert caught.value.code=='malformed_window'
    assert s.backend.timeout==15


def test_outer_budget_shortens_query_and_sleep(monkeypatch):
    s,state=setup(monkeypatch,[[]])
    with pytest.raises(WindowNotReady) as caught:
        wait_for_window(s,{'app_id':'editor','timeout_seconds':8},max_seconds=.08)
    assert caught.value.code=='timeout'
    assert state['clock']==pytest.approx(.08)
    assert state['timeouts']==pytest.approx([.08,.03])
    assert s.backend.timeout==15


def test_cancel_after_first_query_stops_before_sleep(monkeypatch):
    s,state=setup(monkeypatch,[[]]);original=s.backend.call_tool
    def call(*args):
        answer=original(*args);s.batch_cancel.set();return answer
    s.backend.call_tool=call
    with pytest.raises(WindowNotReady) as caught:wait_for_window(s,{'window_id':7})
    assert caught.value.code=='cancelled' and not state['sleeps']
    assert s.backend.timeout==15


@pytest.mark.parametrize('field',['paused','allow_actions'])
def test_disabled_session_makes_no_queries(monkeypatch,field):
    s,state=setup(monkeypatch);setattr(s,field,field=='paused')
    with pytest.raises(WindowNotReady):wait_for_window(s,{'window_id':7})
    assert not state['calls']


def test_backend_exception_restores_timeout(monkeypatch):
    s,state=setup(monkeypatch)
    def fail(*args):raise RuntimeError('backend details')
    s.backend.call_tool=fail
    with pytest.raises(WindowNotReady) as caught:wait_for_window(s,{'window_id':7})
    assert caught.value.code=='backend_error'
    assert 'backend details' not in str(caught.value)
    assert s.backend.timeout==15


def test_late_success_rejected_by_budget(monkeypatch):
    s,state=setup(monkeypatch);original=s.backend.call_tool
    def slow(*args):
        r=original(*args);state['clock']+=4;return r
    s.backend.call_tool=slow
    with pytest.raises(WindowNotReady) as caught:wait_for_window(s,{'window_id':7})
    assert caught.value.code=='timeout'


@pytest.mark.parametrize('args',[{}, {'title_contains':'foo'}, {'pid':42}, {'window_id':True},
    {'window_id':0},{'app_id':''},{'wm_class':'  '},{'wm_class':'x','timeout_seconds':0},
    {'wm_class':'x','timeout_seconds':11},{'wm_class':'x','timeout_seconds':float('nan')},
    {'wm_class':'x','focused':False},{'wm_class':'x','title_contains':''},
    {'window_id':7,'pid':True}, {'wm_class':'x\x00y'}])
def test_invalid_specs_are_rejected(args):
    with pytest.raises(ValueError):validate_wait_spec(args)


def test_normalized_spec_is_independent():
    original={'app_id':'editor'};result=validate_wait_spec(original)
    assert result=={'app_id':'editor','timeout_seconds':3.0}
    assert original=={'app_id':'editor'}


@pytest.mark.parametrize('reply',[
    {'isError':True,'structuredContent':{'windows':[]}},
    {'structuredContent':{'windows':[],'error':'not available'}},
    {'structuredContent':{'windows':None}},
])
def test_failed_window_query_is_not_polled_again(monkeypatch,reply):
    s,state=setup(monkeypatch)
    def call(*args):state['calls'].append(args);return reply
    s.backend.call_tool=call
    with pytest.raises(WindowNotReady) as caught:wait_for_window(s,{'window_id':7})
    assert caught.value.code=='window_query_failed'
    assert len(state['calls'])==1 and not state['sleeps']
    assert s.backend.timeout==15


@pytest.mark.parametrize('budget',[0,-1,True,float('inf'),float('nan')])
def test_invalid_outer_budget_has_no_calls(monkeypatch,budget):
    s,state=setup(monkeypatch)
    with pytest.raises(ValueError):wait_for_window(s,{'window_id':7},max_seconds=budget)
    assert not state['calls']
