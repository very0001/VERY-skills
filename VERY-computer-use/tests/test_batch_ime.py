import pytest
from semantic_cu import fast_input
from semantic_cu.session import SessionController
class B:
    def __init__(self):self.calls=[]
    def call_tool(self,n,a):
        self.calls.append(n)
        w={'window_id':7,'pid':4,'focused':True,'hidden':False,'bounds':{'x':0,'y':0,'width':900,'height':800}}
        if n=='focused_window':return {'structuredContent':{'focused_window':w},'content':[]}
        if n=='get_app_state':return {'structuredContent':{'window_context':w},'content':[{'type':'image','mimeType':'image/png','data':'AA=='}]}
        return {'structuredContent':{'ok':True},'content':[]}
def test_text_and_enter_are_inside_one_guard(monkeypatch):
    b=B();s=SessionController(b,allow_actions=True);obs=s.observe({'window_id':7});b.calls=[]
    class Guard:
        def __init__(self,*a,**kw):pass
        def start(self):b.calls.append('guard_start');return {'status':'disabled_for_literal_input'}
        def restore(self):b.calls.append('guard_restore');return {'status':'restored'}
    monkeypatch.setattr(fast_input,'LiteralInputGuard',Guard)
    r=fast_input.run_input_batch(s,{'observation_id':obs['observation_id'],'host_approved':True,'actions':[
        {'tool':'type_text','arguments':{'text':'print(1)'}},{'tool':'press_key','arguments':{'key':'Return'}}]})
    assert r['status']=='dispatched'
    assert b.calls.index('guard_start')<b.calls.index('type_text')<b.calls.index('press_key')<b.calls.index('guard_restore')<b.calls.index('get_app_state')
    assert r['input_method']['restore']['status']=='restored'

def test_start_failure_still_restores_without_typing(monkeypatch):
    b=B();s=SessionController(b,allow_actions=True);obs=s.observe({'window_id':7});b.calls=[]
    class Guard:
        def __init__(self,*a,**kw):pass
        def start(self):raise RuntimeError('failed')
        def restore(self):b.calls.append('guard_restore');return {'status':'restored'}
    monkeypatch.setattr(fast_input,'LiteralInputGuard',Guard)
    r=fast_input.run_input_batch(s,{'observation_id':obs['observation_id'],'host_approved':True,'actions':[{'tool':'type_text','arguments':{'text':'x'}}]})
    assert r['status']=='stopped'
    assert 'type_text' not in b.calls and 'guard_restore' in b.calls

def test_focus_moves_while_deactivating_input_method(monkeypatch):
    b=B();s=SessionController(b,allow_actions=True);obs=s.observe({'window_id':7});b.calls=[]
    moved=[False];original=b.call_tool
    def call(n,a):
        r=original(n,a)
        if n=='focused_window' and moved[0]:r['structuredContent']['focused_window']['window_id']=8
        return r
    b.call_tool=call
    class Guard:
        def __init__(self,*a,**kw):pass
        def start(self):moved[0]=True;return {'status':'disabled_for_literal_input'}
        def restore(self):b.calls.append('guard_restore');return {'status':'restoration_pending'}
    monkeypatch.setattr(fast_input,'LiteralInputGuard',Guard)
    r=fast_input.run_input_batch(s,{'observation_id':obs['observation_id'],'host_approved':True,'actions':[{'tool':'type_text','arguments':{'text':'secret'}}]})
    assert r['status']=='stopped' and 'type_text' not in b.calls
    assert 'guard_restore' in b.calls
