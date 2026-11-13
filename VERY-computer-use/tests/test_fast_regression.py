import copy
import importlib.util
import pathlib
import threading

import pytest
from semantic_cu.session import SessionController

from semantic_cu.fast_input import run_input_batch


class Backend:
    def __init__(self):
        self.calls = []
        self.focus = 7
        self.fail = None
        self.drift_after = None
        self.actions = 0
        self.timeout = 15.0
        self.session = None
        self.cancel_after = None
        self.no_final = False

    def call_tool(self, name, args):
        self.calls.append((name, copy.deepcopy(args)))
        if name == self.fail:
            raise TimeoutError('test timeout')
        if name == 'get_app_state':
            if self.no_final:
                raise RuntimeError('readback unavailable')
            return {'content': [{'type': 'image', 'mimeType': 'image/png', 'data': 'AA=='}], 'structuredContent': {
                'window_context': {'window_id': 7, 'pid': 40,
                    'bounds': {'x': 10, 'y': 20, 'width': 400, 'height': 300}},
                'screenshot_error': None, 'accessibility_error': None}}
        if name == 'focused_window':
            return {'structuredContent': {'error': None, 'focused_window': {
                'window_id': self.focus, 'pid': 40, 'focused': True, 'hidden': False,
                'bounds': {'x': 10, 'y': 20, 'width': 400, 'height': 300}}}, 'content': []}
        if name == 'activate_window':
            self.focus = args['window_id']
        else:
            self.actions += 1
            if self.drift_after == self.actions:
                self.focus = 8
            if self.cancel_after == self.actions:
                self.session.batch_cancel.set()
        return {'structuredContent': {'ok': True}, 'content': []}


def setup():
    b = Backend()
    s = SessionController(b, allow_actions=True)
    s.batch_cancel = threading.Event()
    b.session = s
    o = s.observe({'window_id': 7, 'max_width': 1920, 'max_height': 1080})
    b.calls.clear()
    return b, s, {'observation_id': o['observation_id'], 'host_approved': True,
        'actions': [
            {'tool': 'press_key', 'arguments': {'key': 'Ctrl+L'}},
            {'tool': 'type_text', 'arguments': {'text': 'hello'}},
            {'tool': 'press_key', 'arguments': {'key': 'Return'}}]}


def test_final_only_no_targets_or_intermediate_trees():
    b, s, args = setup()
    result = run_input_batch(s, args)
    assert result['status'] == 'dispatched'
    assert result['dispatched_actions'] == 3
    assert [name for name, _ in b.calls].count('get_app_state') == 1
    assert b.calls[-1] == ('get_app_state', {'window_id': 7, 'max_width': 1920, 'max_height': 1080})
    assert [name for name, _ in b.calls].count('activate_window') == 0
    for name, arg in b.calls:
        if name in ('press_key', 'type_text'):
            assert set(arg) <= {'key', 'text'}
    assert result['verification']['observation_id'] != args['observation_id']
    assert set(result['timing_seconds']) >= {'preflight', 'focus', 'actions', 'final_observe', 'total'}
    assert 'hello' not in str(result['steps'])


def test_focus_once_if_needed():
    b, s, args = setup(); b.focus = 8
    result = run_input_batch(s, args)
    assert result['status'] == 'dispatched'
    assert [name for name, _ in b.calls].count('activate_window') == 1
    assert b.calls[1] == ('activate_window', {'window_id': 7})


@pytest.mark.parametrize('change', [
    {'host_approved': False},
    {'observation_id': 'stale'},
    {'actions': []},
    {'actions': [{'tool': 'press_key', 'arguments': {'key': 'A'}}, {'tool':'run_shell','arguments':{}}]},
    {'actions': [{'tool': 'click', 'arguments': {'x': 30, 'y': 40, 'element_index': 1}}]},
    {'actions': [{'tool': 'click', 'arguments': {'x': 600, 'y': 40}}]},
    {'actions': [{'tool': 'click', 'arguments': {'x': 30, 'y': 40, 'relative': True}}]},
    {'actions': [{'tool': 'type_text', 'arguments': {'text': 'a', 'window_id':8}}]},
    {'actions': [{'tool': 'press_key', 'arguments': {'key': 'Return', 'screenshot_after': True}}]},
    {'max_seconds': 0},
    {'max_seconds': True},
    {'actions': [{'tool': 'type_text', 'arguments': {'text': 'x' * 4097}}]},
])
def test_invalid_payload_rejected_before_backend_call(change):
    b, s, args = setup(); args.update(change)
    with pytest.raises((ValueError, PermissionError, TypeError)):
        run_input_batch(s, args)
    assert b.calls == []


def test_disabled_or_paused_rejected():
    for state in ('allow_actions', 'paused'):
        b, s, args = setup(); setattr(s, state, state == 'paused')
        with pytest.raises(PermissionError): run_input_batch(s, args)
        assert b.calls == []


def test_focus_drift_stops_no_refocus_or_retry():
    b, s, args = setup(); b.drift_after = 1
    result = run_input_batch(s, args)
    assert result['status'] == 'stopped'
    assert result['dispatched_actions'] == 1
    assert result['failed_step'] == 1
    assert [n for n, _ in b.calls].count('activate_window') == 0
    assert not any(n == 'type_text' for n, _ in b.calls)
    assert result['retry_allowed'] is False


def test_timeout_uncertain_never_replays():
    b, s, args = setup(); b.fail = 'type_text'
    result = run_input_batch(s, args)
    assert result['status'] == 'uncertain'
    assert result['dispatched_actions'] == 1
    assert result['attempted_actions'] == 2
    assert [n for n, _ in b.calls].count('type_text') == 1
    assert b.timeout == 15.0


def test_cancel_stops_at_step_boundary():
    b, s, args = setup(); b.cancel_after = 1
    result = run_input_batch(s, args)
    assert result['status'] == 'stopped'
    assert result['dispatched_actions'] == 1
    assert not any(n == 'type_text' for n, _ in b.calls)


def test_final_failure_does_not_reuse_initial_observation():
    b, s, args = setup(); b.no_final = True
    result = run_input_batch(s, args)
    assert result['status'] == 'verification_failed'
    assert result['dispatched_actions'] == 3
    assert result['verification'].get('observation_id') is None
    assert s.observation_id is None


def test_observation_consumed_even_after_uncertain_action():
    b, s, args = setup(); b.fail = 'type_text'
    run_input_batch(s, args)
    with pytest.raises(ValueError): run_input_batch(s, args)


def test_preflight_failure_keeps_observation_usable():
    b, s, args = setup()
    original = args['observation_id']
    args['actions'].append({'tool': 'click', 'arguments': {'x': -99, 'y': 1}})
    with pytest.raises(ValueError):
        run_input_batch(s, args)
    assert b.calls == []
    assert s.observation_id == original


def test_native_typing_budget_rejected_before_any_action():
    b, s, args = setup()
    args['max_seconds'] = 1
    args['actions'][1]['arguments']['text'] = 'a' * 100
    with pytest.raises(ValueError):
        run_input_batch(s, args)
    assert b.calls == []


@pytest.mark.parametrize('key', ['A B', 'Ctrl++L', 'Ctrl+\nA', '--clearmodifiers'])
def test_malformed_key_rejected_before_any_action(key):
    b, s, args = setup()
    args['actions'][2]['arguments']['key'] = key
    with pytest.raises(ValueError):
        run_input_batch(s, args)
    assert b.calls == []


@pytest.mark.parametrize('key', [
    'Ctrl+L', 'shift+Tab', 'Return', 'F5', 'Control+Alt+Delete', 'Ctrl+Shift+A',
    'OPTION+ArrowLeft', 'Command+0', 'Meta+Home', 'Super+End', 'shift',
    'Page-Up', 'page_up', 'Page-Down', 'page_down', 'Escape', 'Esc',
    'Backspace', 'Del', 'Space', 'Left', 'Right', 'Up', 'Down',
    'ArrowRight', 'ArrowUp', 'ArrowDown', 'Enter', 'F1', 'F12', 'Z', '9',
])
def test_common_upstream_key_names_remain_supported(key):
    b, s, args = setup()
    args['actions'] = [{'tool': 'press_key', 'arguments': {'key': key}}]
    assert run_input_batch(s, args)['dispatched_actions'] == 1


@pytest.mark.parametrize('field', ['focused', 'hidden'])
def test_malformed_focused_window_does_not_activate_or_send(field):
    b, s, args = setup()
    original = b.call_tool
    def call(name, values):
        result = original(name, values)
        if name == 'focused_window':
            del result['structuredContent']['focused_window'][field]
        return result
    b.call_tool = call
    result = run_input_batch(s, args)
    assert result['status'] == 'stopped'
    assert result['attempted_actions'] == 0
    assert not any(name == 'activate_window' for name, _ in b.calls)


def test_pointer_stops_if_window_moves_even_when_point_still_inside():
    b, s, args = setup()
    args['actions'] = [{'tool': 'click', 'arguments': {'x': 80, 'y': 80}}]
    original = b.call_tool
    def call(name, values):
        result = original(name, values)
        if name == 'focused_window':
            result['structuredContent']['focused_window']['bounds']['x'] = 11
        return result
    b.call_tool = call
    result = run_input_batch(s, args)
    assert result['status'] == 'stopped'
    assert result['attempted_actions'] == 0


def test_target_pid_change_does_not_activate_or_send():
    b, s, args = setup()
    original = b.call_tool
    def call(name, values):
        result = original(name, values)
        if name == 'focused_window':
            result['structuredContent']['focused_window']['pid'] = 41
        return result
    b.call_tool = call
    result = run_input_batch(s, args)
    assert result['status'] == 'stopped'
    assert not any(name == 'activate_window' for name, _ in b.calls)
    assert result['attempted_actions'] == 0


def test_deadline_stops_next_input_and_never_returns_old_image():
    b, s, args = setup()
    now = [0.0]
    s.clock = lambda: now[0]
    s.observed_at = 0.0
    args['max_seconds'] = 1
    original = b.call_tool
    timeouts = []
    def call(name, values):
        timeouts.append(b.timeout)
        result = original(name, values)
        if name == 'press_key':
            now[0] += 1.1
        return result
    b.call_tool = call
    result = run_input_batch(s, args)
    assert result['status'] == 'stopped'
    assert result['dispatched_actions'] == 1
    assert result['attempted_actions'] == 1
    assert result['verification'].get('observation_id') is None
    assert result['verification']['code'] == 'budget_exhausted'
    assert s.observation_id is None
    assert all(timeout <= 1 for timeout in timeouts)
    assert b.timeout == 15


def test_click_and_scroll_send_absolute_coordinates_without_targets():
    b, s, args = setup()
    args['actions'] = [
        {'tool': 'click', 'arguments': {'x': 80, 'y': 90, 'click_count': 2}},
        {'tool': 'scroll', 'arguments': {'direction': 'down', 'pages': 1, 'x': 80, 'y': 90}}]
    result = run_input_batch(s, args)
    assert result['status'] == 'dispatched'
    for action in args['actions']:
        assert (action['tool'], action['arguments']) in b.calls
    assert [name for name, _ in b.calls].count('get_app_state') == 1


@pytest.mark.parametrize('text', ['unsafe\0text', '\ud800'])
def test_unrepresentable_native_text_rejected_before_any_action(text):
    b, s, args = setup()
    args['actions'][1]['arguments']['text'] = text
    with pytest.raises(ValueError):
        run_input_batch(s, args)
    assert b.calls == []


def test_requested_final_image_missing_invalidates_observation():
    b, s, args = setup()
    original = b.call_tool
    def call(name, values):
        result = original(name, values)
        if name == 'get_app_state':
            result['content'] = []
        return result
    b.call_tool = call
    result = run_input_batch(s, args)
    assert result['status'] == 'verification_failed'
    assert result['verification']['status'] == 'incomplete'
    assert result['verification'].get('observation_id') is None
    assert s.observation_id is None
    assert s._report == {}


def test_explicit_tree_only_final_observation_does_not_require_image():
    b, s, args = setup()
    s._observation_arguments['include_screenshot'] = False
    original = b.call_tool
    def call(name, values):
        result = original(name, values)
        if name == 'get_app_state':
            result['content'] = []
        return result
    b.call_tool = call
    result = run_input_batch(s, args)
    assert result['status'] == 'dispatched'
    assert result['verification']['observation_id'] == s.observation_id
    assert s.observation_id != args['observation_id']
    assert b.calls[-1][1]['include_screenshot'] is False


@pytest.mark.parametrize('field,value', [
    ('window_error', 'failed'), ('screenshot_error', 'failed'), ('ok', False)])
def test_final_report_error_invalidates_observation(field, value):
    b, s, args = setup()
    original = b.call_tool
    def call(name, values):
        result = original(name, values)
        if name == 'get_app_state':
            result['structuredContent'][field] = value
        return result
    b.call_tool = call
    result = run_input_batch(s, args)
    assert result['status'] == 'verification_failed'
    assert result['verification']['status'] == 'incomplete'
    assert result['verification'].get('observation_id') is None
    assert s.observation_id is None


@pytest.mark.parametrize('key', [
    '.', ',', '/', '-', 'Ctrl+semicolon', 'plus', 'Ctrl+plus', 'semicolon',
    'F13', 'F24', 'Ctrl+F13', 'Ctrl+Shift', 'Hyper+A', 'A+B', 'Print',
    'Pause', 'Insert', 'Ctrl+Insert', 'Ctrl+é', 'Ctrl+K', 'Control_L+A',
    'Shift_L+Tab', 'KP_Enter',
])
def test_keys_rejected_by_pinned_upstream_fail_before_any_input(key):
    b, s, args = setup()
    args['actions'][2]['arguments']['key'] = key
    with pytest.raises(ValueError):
        run_input_batch(s, args)
    assert b.calls == []
    assert s.observation_id == args['observation_id']


def test_punctuation_text_remains_supported_as_type_text():
    b, s, args = setup()
    args['actions'] = [{'tool': 'type_text', 'arguments': {'text': '.,/;-+'}}]
    result = run_input_batch(s, args)
    assert result['status'] == 'dispatched'
    assert ('type_text', {'text': '.,/;-+'}) in b.calls
