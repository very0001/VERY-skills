import copy
import pytest
from semantic_cu.session import SessionController
from semantic_cu.app_task import AppTasks

class Backend:
    def __init__(self):
        self.calls=[]; self.timeout=15; self.text=''; self.focus=7; self.windows=[self.window()]
    def window(self):
        return {'window_id':7,'pid':40,'app_id':'gedit','wm_class':'Gedit','focused':self.focus==7,
                'hidden':False,'bounds':{'x':10,'y':20,'width':400,'height':300}}
    def call_tool(self,name,args):
        self.calls.append((name,copy.deepcopy(args)))
        if name=='list_windows': return {'structuredContent':{'windows':self.windows,'error':None},'content':[]}
        if name=='focused_window': return {'structuredContent':{'focused_window':self.window()},'content':[]}
        if name=='activate_window': self.focus=7
        if name=='type_text': self.text+=args['text']
        if name=='get_app_state':
            return {'structuredContent':{'window_context':self.window(), 'tree_scoped':True,
                'accessibility_tree':[{'index':1,'role':'text','states':['focused','editable'],
                                       'text':{'content':self.text}}]},
                'content':[] if args.get('include_screenshot') is False else
                 [{'type':'image','mimeType':'image/png','data':'AA=='}]}
        return {'structuredContent':{'ok':True},'content':[]}

def setup():
    b=Backend();s=SessionController(b,allow_actions=True); a=AppTasks(s)
    args={'request_id':'test-1','target':{'wm_class':'Gedit'},'host_approved':True,
          'actions':[{'tool':'type_text','arguments':{'text':'Hello World'}}], 'expected_text':'Hello World'}
    return b,s,a,args

def test_one_call_one_final_image_and_replay_has_no_effect():
    b,s,a,args=setup();r=a.run(args)
    assert r['status']=='dispatched'
    assert r['text_check']['status']=='matched'
    observes=[v for n,v in b.calls if n=='get_app_state']
    assert len(observes)==2 and observes[0]['include_screenshot'] is False
    assert observes[1]['include_screenshot'] is True
    assert not any(n=='activate_window' for n,_ in b.calls)
    before=copy.deepcopy(b.calls); replay=a.run(args)
    assert replay['status']=='already_processed' and b.calls==before
    assert 'Hello World' not in str(a.requests)
    assert r['timing_seconds']['task_total']>=r['timing_seconds']['total']

@pytest.mark.parametrize('change',[
    {'host_approved':False},{'target':{}},{'target':{'title':'text'}},
    {'actions':[{'tool':'click','arguments':{'x':20,'y':30}}]},
    {'actions':[{'tool':'press_key','arguments':{'key':'semicolon'}}]},
    {'launch_desktop_id':'../../bin/sh'}, {'max_seconds':float('nan')},
])
def test_bad_arguments_no_desktop_calls(change):
    b,s,a,args=setup();args.update(change)
    with pytest.raises((ValueError,PermissionError)):a.run(args)
    assert b.calls==[]

def test_ambiguous_window_stops_without_input():
    b,s,a,args=setup();b.windows=[dict(b.window(),focused=False),dict(b.window(),window_id=8,focused=False)]
    r=a.run(args); assert r['status']=='stopped' and r['failure']['code']=='ambiguous_target'
    assert not any(n in ('type_text','activate_window') for n,_ in b.calls)

def test_conflicting_id_never_types_twice():
    b,s,a,args=setup();a.run(args);args['actions'][0]['arguments']['text']='different'
    with pytest.raises(ValueError,match='request_id'): a.run(args)
    assert b.text=='Hello World'

def test_launch_missing_app_once(monkeypatch):
    b,s,a,args=setup();b.windows=[]; launches=[]
    args['launch_desktop_id']='org.gnome.gedit.desktop'
    monkeypatch.setattr(a,'_validate_launcher',lambda x:x)
    def launch(ident, remaining):launches.append(ident); b.windows=[b.window()]
    monkeypatch.setattr(a,'_launch',launch)
    assert a.run(args)['status']=='dispatched'
    assert launches==['org.gnome.gedit.desktop']

def test_missing_app_no_implicit_launch():
    b,s,a,args=setup();b.windows=[];r=a.run(args)
    assert r['failure']['code']=='app_not_running'
    assert len(b.calls)==1

def test_text_mismatch_never_retypes():
    b,s,a,args=setup();args['expected_text']='other';r=a.run(args)
    assert r['text_check']['status']=='mismatch'
    assert r['status']=='verification_failed'
    assert len([1 for n,_ in b.calls if n=='type_text'])==1

def test_disabled_or_paused_does_not_reuse_or_run():
    b,s,a,args=setup();s.pause()
    with pytest.raises(PermissionError):a.run(args)
    assert b.calls==[]

def test_focus_interference_stops_without_stealing_back():
    b,s,a,args=setup(); original=b.call_tool
    def call(name,values):
        result=original(name,values)
        if name=='press_key':
            b.focus=8
        return result
    b.call_tool=call
    args['actions'].insert(0,{'tool':'press_key','arguments':{'key':'Ctrl+T'}})
    r=a.run(args)
    assert r['status']=='stopped' and r['dispatched_actions']==1
    assert b.text==''
    assert not any(n=='activate_window' for n,_ in b.calls)

def test_ledger_retains_uncertain_result_without_replaying():
    b,s,a,args=setup();original=b.call_tool
    def call(name,values):
        if name=='type_text':
            b.text='maybe'
            raise TimeoutError('uncertain')
        return original(name,values)
    b.call_tool=call
    r=a.run(args);assert r['status']=='uncertain'
    before=len(b.calls);assert a.run(args)['status']=='already_processed'
    assert len(b.calls)==before

def test_multiple_windows_choose_focused_matching_window():
    b,s,a,args=setup(); b.windows.append(dict(b.window(),window_id=8,focused=False))
    assert a.run(args)['status']=='dispatched'

def test_partial_tree_cannot_prove_unique_focused_text():
    b,s,a,args=setup(); original=b.call_tool
    def call(name,values):
        r=original(name,values)
        if name=='get_app_state':r['structuredContent']['accessibility_tree_truncated']=True
        return r
    b.call_tool=call
    assert a.run(args)['text_check']['status']=='unavailable'

def test_gateway_exposes_task_and_holds_shared_input_lock(monkeypatch):
    import json
    from contextlib import contextmanager
    from semantic_cu.mcp_server import Gateway
    holds=[]
    @contextmanager
    def lock():
        holds.append('enter');yield;holds.append('exit')
    monkeypatch.setenv('XDG_SESSION_TYPE','x11')
    monkeypatch.setattr('semantic_cu.auxiliary_guard.input_lock',lock)
    b,s,a,args=setup();g=Gateway(b,allow_actions=True)
    assert 'desktop_app_task' in [t['name'] for t in g.tools]
    out=g.handle({'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'desktop_app_task','arguments':args}})
    assert json.loads(out['result']['content'][0]['text'])['status']=='dispatched'
    assert holds==['enter','exit']
    g.aux.started=True
    with pytest.raises(RuntimeError,match='auxiliary'):g.call('desktop_app_task',args)
