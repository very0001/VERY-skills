import json

from semantic_cu.mcp_server import Gateway


class Backend:
    def __init__(self):
        self.calls = []
        self.fail_on = None
        self.bad_verification = False
        self.typed = None
        self.drop_text = False
        self.no_focus = False
        self.window_id = 7
        self.drop_image = False

    def call_tool(self, name, args):
        self.calls.append((name, args.copy()))
        if name == self.fail_on:
            raise TimeoutError('unknown result')
        if name == 'get_app_state':
            return {'structuredContent': {'snapshot_id':'s1','tree_scoped':True,
                    'accessibility_tree_truncated':False,
                    'window_context':{'window_id':self.window_id},'accessibility_tree':[
                        {'index':3,'role':'push button','states':[]},
                        {'index':4,'role':'text','states':(['editable'] if self.no_focus else ['focused','editable']),
                         'text':{'content':self.typed or ''}}],
                    'window_error':None,'accessibility_error':None,
                    'screenshot_error':'failed' if self.bad_verification else None},
                    'content':([] if self.drop_image else [{'type':'image','mimeType':'image/png','data':'YWJj'}])}
        if name == 'type_text' and not self.drop_text:
            self.typed = args['text']
        return {'structuredContent':{'ok':True},'content':[]}


def call(gateway,name,args):
    response=gateway.handle({'jsonrpc':'2.0','id':1,'method':'tools/call',
                             'params':{'name':name,'arguments':args}})
    assert 'error' not in response
    return response['result']


def parsed(value):
    return json.loads(value['content'][0]['text'])


def setup(enabled=True):
    backend=Backend();g=Gateway(backend,allow_actions=enabled)
    obs=parsed(call(g,'desktop_observe',{'target':{'window_id':7}}))
    return backend,g,obs['observation_id']


def args(obs):
    return {'observation_id':obs,'host_approved':True,'actions':[
        {'tool':'click','arguments':{'element_index':3}},
        {'tool':'type_text','arguments':{'text':'hello'}},
        {'tool':'press_key','arguments':{'key':'Return'}}]}


def test_sequence_uses_fresh_verification_and_returns_last_image():
    backend,g,obs=setup();result=call(g,'desktop_act_sequence',args(obs));value=parsed(result)
    assert value['status']=='dispatched'
    assert value['dispatched_actions']==3
    assert value['verification']['observation_id']!=obs
    assert any(c.get('type')=='image' for c in result['content'])
    assert [n for n,_ in backend.calls]==['get_app_state','click','get_app_state',
        'type_text','get_app_state','press_key','get_app_state']
    assert all(a.get('window_id')==7 for n,a in backend.calls if n in ('click','type_text','press_key'))
    checks=[a for name,a in backend.calls if name=='get_app_state']
    assert [a.get('include_screenshot') for a in checks]==[None,False,False,True]


def test_sequence_preserves_user_request_for_no_final_screenshot():
    backend=Backend();g=Gateway(backend,allow_actions=True)
    obs=parsed(call(g,'desktop_observe',{'target':{'window_id':7,'include_screenshot':False}}))
    result=parsed(call(g,'desktop_act_sequence',args(obs['observation_id'])))
    assert result['status']=='dispatched'
    checks=[a for name,a in backend.calls if name=='get_app_state']
    assert [a.get('include_screenshot') for a in checks]==[False,False,False,False]


def test_refuse_no_approval_disabled_or_malformed_without_dispatch():
    for enabled in (True,False):
        backend,g,obs=setup(enabled)
        payload=args(obs);payload['host_approved']=not enabled
        assert call(g,'desktop_act_sequence',payload)['isError']
        assert len(backend.calls)==1
    backend,g,obs=setup()
    for actions in ([],[{'tool':'click','arguments':{}},{'tool':'run_shell','arguments':{}}],
                    [{'tool':'click','arguments':{}},{'tool':'click','arguments':{'x':1,'y':1}}],
                    [{'tool':'type_text','arguments':{'text':'hello\n'}}],
                    [{'tool':'type_text','arguments':{'text':'hello'}},
                     {'tool':'press_key','arguments':{'key':'Ctrl+Return'}}]):
        payload=args(obs);payload['actions']=actions
        assert call(g,'desktop_act_sequence',payload)['isError']
    assert len(backend.calls)==1


def test_uncertain_action_stops_without_next_action_or_retry():
    backend,g,obs=setup();backend.fail_on='type_text'
    result=parsed(call(g,'desktop_act_sequence',args(obs)))
    assert result['status']=='uncertain'
    assert result['dispatched_actions']==1
    assert [n for n,_ in backend.calls]==['get_app_state','click','get_app_state','type_text']


def test_failed_verification_stops_before_next_action():
    backend,g,obs=setup();backend.bad_verification=True
    result=parsed(call(g,'desktop_act_sequence',args(obs)))
    assert result['status']=='verification_failed'
    assert result['dispatched_actions']==1
    assert [n for n,_ in backend.calls]==['get_app_state','click','get_app_state']


def test_sequence_stops_when_window_identity_changes():
    backend,g,obs=setup();backend.window_id=8
    result=parsed(call(g,'desktop_act_sequence',args(obs)))
    assert result['status']=='verification_failed'
    assert result['dispatched_actions']==1
    assert [n for n,_ in backend.calls]==['get_app_state','click','get_app_state']


def test_sequence_reports_missing_requested_final_image_without_retry():
    backend,g,obs=setup();backend.drop_image=True
    result=parsed(call(g,'desktop_act_sequence',args(obs)))
    assert result['status']=='verification_failed'
    assert result['dispatched_actions']==3
    assert result['retry_allowed'] is False
    assert [n for n,_ in backend.calls].count('press_key')==1


def test_enter_after_typing_requires_observed_exact_text():
    backend,g,obs=setup();backend.drop_text=True
    result=parsed(call(g,'desktop_act_sequence',args(obs)))
    assert result['status']=='verification_failed'
    assert result['dispatched_actions']==2
    assert 'press_key' not in [n for n,_ in backend.calls]


def test_typing_after_click_requires_a_focused_editable_field():
    backend,g,obs=setup();backend.no_focus=True
    result=parsed(call(g,'desktop_act_sequence',args(obs)))
    assert result['status']=='verification_failed'
    assert result['dispatched_actions']==1
    assert 'type_text' not in [n for n,_ in backend.calls]


def test_sequence_rejects_unscoped_or_stale_observation():
    backend=Backend();g=Gateway(backend,allow_actions=True)
    obs=parsed(call(g,'desktop_observe',{}))['observation_id']
    # The fake backend resolves a window; clearing scope simulates a host that
    # observed a full desktop without a reliable specific window.
    g.session._observation_arguments.clear()
    assert call(g,'desktop_act_sequence',args(obs))['isError']
    assert [n for n,_ in backend.calls]==['get_app_state']
    backend,g,obs=setup()
    assert call(g,'desktop_act_sequence',args('stale'))['isError']
    assert [n for n,_ in backend.calls]==['get_app_state']


def test_sequence_rejects_boolean_window_id_and_extra_action_fields():
    backend,g,obs=setup()
    g.session._observation_arguments['window_id']=True
    assert call(g,'desktop_act_sequence',args(obs))['isError']
    backend,g,obs=setup()
    payload=args(obs);payload['actions'][0]['extra']='ignored'
    assert call(g,'desktop_act_sequence',payload)['isError']
    assert len(backend.calls)==1
