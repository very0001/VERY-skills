import json
from semantic_cu.mcp_server import Gateway

class Backend:
    def __init__(self):self.calls=[];self.open=False;self.timeout=15
    def call_tool(self,name,args):
        self.calls.append((name,args.copy()))
        w={'window_id':9,'pid':30,'wm_class':'Files','title':'Trash','focused':True,'hidden':False,'bounds':{'x':0,'y':0,'width':800,'height':600}}
        if name=='list_windows':return {'structuredContent':{'windows':[w] if self.open else []},'content':[]}
        if name=='get_app_state':return {'structuredContent':{'window_context':dict(w,window_id=args.get('window_id',7))},'content':[{'type':'image','mimeType':'image/png','data':'AA=='}]}
        if name=='click':self.open=True
        return {'structuredContent':{'ok':True},'content':[]}

def test_click_waits_locally_and_captures_target_once(monkeypatch):
    monkeypatch.setenv('XDG_SESSION_TYPE','other')
    b=Backend();g=Gateway(b,allow_actions=True);obs=g.session.observe({})
    out=g.handle({'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'desktop_act','arguments':{
        'tool':'click','arguments':{'x':20,'y':30},'host_approved':True,'observation_id':obs['observation_id'],
        'wait_for_window':{'wm_class':'Files','title_contains':'Trash'}}}})
    result=out['result'];v=json.loads(result['content'][0]['text'])
    assert v['status']=='dispatched'
    assert v['verification']['result']['structuredContent']['window_context']['window_id']==9
    assert [n for n,_ in b.calls]==['get_app_state','click','list_windows','get_app_state']
    assert len([c for c in result['content'] if c['type']=='image'])==1

def test_invalid_wait_rejected_before_click(monkeypatch):
    monkeypatch.setenv('XDG_SESSION_TYPE','other')
    b=Backend();g=Gateway(b,allow_actions=True);obs=g.session.observe({})
    out=g.handle({'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'desktop_act','arguments':{
        'tool':'click','arguments':{'x':20,'y':30},'host_approved':True,'observation_id':obs['observation_id'],
        'wait_for_window':{'title_contains':'Trash'}}}})
    assert out['result']['isError']
    assert [n for n,_ in b.calls]==['get_app_state']

def test_missing_environment_blocks_actions_but_not_discovery():
    import pytest
    b=Backend();g=Gateway(b,allow_actions=True);g.environment_error='desktop_environment_no_graphical_session'
    with pytest.raises(PermissionError,match='Desktop session environment unavailable'):
        g.call('desktop_app_task',{})
    assert b.calls==[]
    assert g.handle({'jsonrpc':'2.0','id':1,'method':'tools/list','params':{}})['result']['tools']

def test_focus_changed_between_poll_and_image_invalidates_result():
    from semantic_cu.session import SessionController
    from semantic_cu.window_ready import observe_ready_window, WindowNotReady
    import pytest
    b=Backend();b.open=True;old=b.call_tool
    def call(name,args):
        r=old(name,args)
        if name=='get_app_state':r['structuredContent']['window_context']['focused']=False
        return r
    b.call_tool=call;s=SessionController(b,allow_actions=True)
    with pytest.raises(WindowNotReady,match='window_changed_after_wait'):
        observe_ready_window(s,{'wm_class':'Files','title_contains':'Trash'}, {})
    assert s.observation_id is None

def test_tty_environment_does_not_disable_x11_input_lock():
    from semantic_cu.desktop_env import resolve_desktop_environment
    import pytest
    with pytest.raises(RuntimeError,match='invalid_session_type'):
        resolve_desktop_environment({'DISPLAY':':1','DBUS_SESSION_BUS_ADDRESS':'x',
            'XDG_RUNTIME_DIR':'/tmp','XDG_SESSION_TYPE':'tty'})
