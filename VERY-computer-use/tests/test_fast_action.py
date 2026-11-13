import json

from semantic_cu.mcp_server import Gateway


class Backend:
    def __init__(self):
        self.calls = []
        self.fail_action = False

    def call_tool(self, name, args):
        self.calls.append((name, args.copy()))
        if name == 'get_app_state':
            return {'structuredContent': {'snapshot_id':'snap', 'tree_scoped':True,
                    'window_context':{'window_id':7},
                    'accessibility_tree':[{'index':3}], 'window_error':None,
                    'accessibility_error':None},
                    'content':[{'type':'image','mimeType':'image/png','data':'YWJj'}]}
        if self.fail_action:
            raise TimeoutError('action result unknown')
        return {'structuredContent':{'ok':True},'content':[]}


def invoke(gateway, name, args):
    response = gateway.handle({'jsonrpc':'2.0','id':1,'method':'tools/call',
                               'params':{'name':name,'arguments':args}})
    assert 'error' not in response
    return response['result']


def decode(result):
    return json.loads(result['content'][0]['text'])


def test_fast_action_reuses_real_post_action_observation():
    backend=Backend(); gateway=Gateway(backend,allow_actions=True)
    observed=decode(invoke(gateway,'desktop_observe',{'target':{'window_id':7}}))
    action=invoke(gateway,'desktop_act',{'tool':'click','arguments':{'element_index':3},
                  'observation_id':observed['observation_id'],'host_approved':True})
    result=decode(action)
    assert result['status']=='dispatched'
    assert result['verification']['observation_id'] != observed['observation_id']
    assert action['content'][1]['type']=='image'
    assert [name for name,_ in backend.calls] == ['get_app_state','click','get_app_state']
    assert backend.calls[1][1]['window_id']==7
    assert backend.calls[1][1]['snapshot_id']=='snap'
    proposal=invoke(gateway,'desktop_prepare_action',{'tool':'click','arguments':{'element_index':3},
                    'observation_id':result['verification']['observation_id']})
    assert not proposal.get('isError',False)
    assert len(backend.calls)==3


def test_fast_action_requires_approval_and_enabled_actions():
    for enabled, approved in [(True,False),(False,True)]:
        backend=Backend(); gateway=Gateway(backend,allow_actions=enabled)
        observed=decode(invoke(gateway,'desktop_observe',{}))
        outcome=invoke(gateway,'desktop_act',{'tool':'click','arguments':{'x':1,'y':2},
                       'observation_id':observed['observation_id'],'host_approved':approved})
        assert outcome['isError']
        assert [name for name,_ in backend.calls] == ['get_app_state']
        assert gateway.session.proposals == {}


def test_fast_action_rejects_stale_scope_and_forbidden_tool():
    backend=Backend(); gateway=Gateway(backend,allow_actions=True)
    observed=decode(invoke(gateway,'desktop_observe',{'target':{'window_id':7}}))
    for tool,args,obs in [('click',{'x':1,'y':2},'stale'),
                          ('click',{'window_id':8,'x':1,'y':2},observed['observation_id']),
                          ('run_shell',{},observed['observation_id'])]:
        outcome=invoke(gateway,'desktop_act',{'tool':tool,'arguments':args,
                       'observation_id':obs,'host_approved':True})
        assert outcome['isError']
    assert [name for name,_ in backend.calls] == ['get_app_state']


def test_fast_action_failure_is_uncertain_and_not_retried():
    backend=Backend(); gateway=Gateway(backend,allow_actions=True)
    observed=decode(invoke(gateway,'desktop_observe',{})); backend.fail_action=True
    outcome=decode(invoke(gateway,'desktop_act',{'tool':'click','arguments':{'x':1,'y':2},
                   'observation_id':observed['observation_id'],'host_approved':True}))
    assert outcome['status']=='uncertain'
    assert outcome['retry_allowed'] is False
    assert [name for name,_ in backend.calls] == ['get_app_state','click']
    assert gateway.session.observation_id is None
