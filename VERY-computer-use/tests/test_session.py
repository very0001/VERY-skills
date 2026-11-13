"""Reconstructed local safety-contract tests; no desktop calls."""

import copy
import unittest

from semantic_cu.session import SessionController


class Backend:
    def __init__(self):
        self.calls = []
        self.fail = False

    def call_tool(self, name, args):
        self.calls.append((name, args))
        if self.fail:
            raise TimeoutError("timed out")
        return {"content": [{"type": "text", "text": "ok"}]}


class SessionTests(unittest.TestCase):
    def test_disabled(self):
        session = SessionController(Backend())
        observation = session.observe()
        with self.assertRaises(PermissionError):
            session.prepare("click", {}, observation["observation_id"])

    def test_once_and_verify(self):
        backend = Backend()
        session = SessionController(backend, allow_actions=True)
        observation = session.observe()
        proposal = session.prepare(
            "click", {"x": 1, "y": 2}, observation["observation_id"]
        )
        result = session.execute(proposal["proposal_token"], host_approved=True)
        self.assertEqual(result["status"], "dispatched")
        self.assertEqual(len(backend.calls), 3)
        with self.assertRaises(ValueError):
            session.execute(proposal["proposal_token"], host_approved=True)

    def test_uncertain_no_retry(self):
        backend = Backend()
        session = SessionController(backend, allow_actions=True)
        observation = session.observe()
        proposal = session.prepare("click", {}, observation["observation_id"])
        backend.fail = True
        self.assertEqual(
            session.execute(proposal["proposal_token"], host_approved=True)["status"],
            "uncertain",
        )
        self.assertIsNone(session.observation_id)

    def test_expiry_and_shell(self):
        now = [0]
        session = SessionController(Backend(), allow_actions=True, clock=lambda: now[0])
        observation = session.observe()
        with self.assertRaises(PermissionError):
            session.prepare("run_shell", {}, observation["observation_id"])
        now[0] = 31
        with self.assertRaises(ValueError):
            session.prepare("click", {}, observation["observation_id"])

    def test_pause(self):
        session = SessionController(Backend(), allow_actions=True)
        observation = session.observe()
        session.pause()
        with self.assertRaises(PermissionError):
            session.prepare("click", {}, observation["observation_id"])
        session.resume()
        self.assertIsNone(session.observation_id)


def healthy_report():
    return {
        "snapshot_id": "snapshot-1",
        "tree_scoped": True,
        "window_context": {
            "window_id": 7,
            "pid": 42,
            "app_id": "org.example.App",
            "title": "Example",
        },
        "accessibility_tree": [
            {
                "index": 3,
                "object_ref": "bus:/button",
                "role": "push button",
                "name": "Save",
            }
        ],
        "accessibility_error": None,
        "window_error": None,
    }


class StructuredBackend:
    def __init__(self, report=None):
        self.report = healthy_report() if report is None else report
        self.calls = []
        self.fail = False
        self.tool_error = False

    def call_tool(self, name, args):
        self.calls.append((name, copy.deepcopy(args)))
        if self.fail:
            raise TimeoutError("fixture timeout")
        if name == "get_app_state":
            return {
                "isError": self.tool_error,
                "structuredContent": copy.deepcopy(self.report),
                "content": [],
            }
        return {"isError": False, "structuredContent": {"ok": True}, "content": []}


class BindingTests(unittest.TestCase):
    def make_session(self, report=None, target=None):
        backend = StructuredBackend(report)
        session = SessionController(backend, allow_actions=True)
        observation = session.observe(
            {"window_id": 7, "include_screenshot": False} if target is None else target
        )
        return backend, session, observation["observation_id"]

    def test_scope_snapshot_injected_and_verification_scope_preserved(self):
        backend, session, observation = self.make_session()
        original = {"element_index": 3}
        proposal = session.prepare("click", original, observation)
        self.assertEqual(original, {"element_index": 3})
        self.assertEqual(
            proposal["arguments"],
            {"element_index": 3, "window_id": 7, "snapshot_id": "snapshot-1"},
        )
        session.execute(proposal["proposal_token"], host_approved=True)
        self.assertEqual(
            backend.calls[2],
            ("get_app_state", {"window_id": 7, "include_screenshot": False}),
        )

    def test_reject_conflicting_snapshot_and_scope(self):
        _, session, observation = self.make_session()
        for args in (
            {"element_index": 3, "snapshot_id": "old"},
            {"element_index": 3, "window_id": 8},
            {"element_index": 3, "window_id": True},
            {"element_index": 3, "pid": 99},
            {"element_index": 3, "app_id": "other.App"},
            {"element_index": 3, "window_title": "Other"},
        ):
            with self.subTest(args=args), self.assertRaises(ValueError):
                session.prepare("click", args, observation)

    def test_title_mapping_without_window_backend(self):
        report = healthy_report()
        report["window_context"] = None
        _, session, observation = self.make_session(
            report, {"title": "Example", "include_screenshot": False}
        )
        proposal = session.prepare("click", {"element_index": 3}, observation)
        self.assertEqual(proposal["arguments"]["window_title"], "Example")
        self.assertNotIn("title", proposal["arguments"])
        proposal = session.prepare("type_text", {"text": "hello"}, observation)
        self.assertEqual(proposal["arguments"]["title"], "Example")
        self.assertNotIn("window_title", proposal["arguments"])

    def test_object_ref_is_bound_to_observed_index(self):
        _, session, observation = self.make_session()
        proposal = session.prepare(
            "perform_action", {"element_identifier": "bus:/button"}, observation
        )
        self.assertNotIn("element_identifier", proposal["arguments"])
        self.assertEqual(proposal["arguments"]["element_index"], 3)
        self.assertEqual(proposal["arguments"]["snapshot_id"], "snapshot-1")
        for args in (
            {"element_identifier": "bus:/other"},
            {"element_identifier": "bus:/button", "element_index": 9},
        ):
            with self.subTest(args=args), self.assertRaises(ValueError):
                session.prepare("perform_action", args, observation)

    def test_semantic_actions_fail_closed(self):
        for delta in (
            {"snapshot_id": None},
            {"snapshot_id": ""},
            {"accessibility_error": "failed"},
            {"window_error": "not found"},
            {"tree_scoped": False},
            {"accessibility_tree": []},
            {"accessibility_tree": None},
        ):
            report = healthy_report()
            report.update(delta)
            _, session, observation = self.make_session(report)
            with self.subTest(delta=delta), self.assertRaises(ValueError):
                session.prepare("click", {"element_index": 3}, observation)

    def test_unobserved_index_or_invalid_identifier_blocked(self):
        _, session, observation = self.make_session()
        for index in (4, -1, True, "3"):
            with self.subTest(index=index), self.assertRaises(ValueError):
                session.prepare("click", {"element_index": index}, observation)
        with self.assertRaises(ValueError):
            session.prepare("click", {"element_identifier": "bus:/button"}, observation)
        with self.assertRaises(ValueError):
            session.prepare(
                "scroll", {"role": "button", "direction": "down"}, observation
            )

    def test_failed_refresh_invalidates_previous_proposal(self):
        for kind in ("exception", "isError"):
            backend, session, observation = self.make_session()
            proposal = session.prepare("click", {"element_index": 3}, observation)
            backend.fail = kind == "exception"
            backend.tool_error = kind == "isError"
            expected_error = TimeoutError if kind == "exception" else RuntimeError
            with self.assertRaises(expected_error):
                session.observe({"window_id": 7})
            self.assertIsNone(session.observation_id)
            with self.assertRaises(ValueError):
                session.execute(proposal["proposal_token"], host_approved=True)

    def test_private_binding_cannot_be_changed_through_return_values(self):
        backend = StructuredBackend()
        session = SessionController(backend, allow_actions=True)
        args = {"window_id": 7, "include_screenshot": False}
        observed = session.observe(args)
        args["window_id"] = 99
        observed["result"]["structuredContent"]["snapshot_id"] = "mutated"
        proposal = session.prepare(
            "click", {"element_index": 3}, observed["observation_id"]
        )
        proposal["arguments"]["window_id"] = 99
        session.execute(proposal["proposal_token"], host_approved=True)
        self.assertEqual(backend.calls[1][1]["snapshot_id"], "snapshot-1")
        self.assertEqual(backend.calls[1][1]["window_id"], 7)
        self.assertEqual(backend.calls[2][1]["window_id"], 7)

    def test_action_capture_cannot_widen_scope(self):
        _, session, observation = self.make_session()
        for capture in (True, "screen"):
            with self.subTest(capture=capture), self.assertRaises(ValueError):
                session.prepare(
                    "click",
                    {"element_index": 3, "screenshot_after": capture},
                    observation,
                )
        proposal = session.prepare(
            "click", {"element_index": 3, "screenshot_after": "window"}, observation
        )
        self.assertEqual(proposal["arguments"]["screenshot_after"], "window")

    def test_scoped_drag_is_refused(self):
        _, session, observation = self.make_session()
        with self.assertRaises(ValueError):
            session.prepare(
                "drag",
                {"start_x": 1, "start_y": 1, "end_x": 2, "end_y": 2},
                observation,
            )

    def test_target_cannot_be_added_without_observing_it(self):
        report = healthy_report()
        report["window_context"] = None
        report["tree_scoped"] = False
        _, session, observation = self.make_session(report, {})
        with self.assertRaises(ValueError):
            session.prepare("click", {"x": 1, "y": 1, "window_id": 99}, observation)

    def test_different_resolved_target_invalidates_observation(self):
        backend = StructuredBackend()
        session = SessionController(backend, allow_actions=True)
        with self.assertRaises(RuntimeError):
            session.observe({"window_id": 99})
        self.assertIsNone(session.observation_id)

    def test_new_snapshot_invalidates_old_token(self):
        backend, session, observation = self.make_session()
        proposal = session.prepare("click", {"element_index": 3}, observation)
        backend.report["snapshot_id"] = "snapshot-2"
        next_observation = session.observe({"window_id": 7})
        with self.assertRaises(ValueError):
            session.execute(proposal["proposal_token"], host_approved=True)
        next_proposal = session.prepare(
            "click", {"name": "Save"}, next_observation["observation_id"]
        )
        self.assertEqual(next_proposal["arguments"]["snapshot_id"], "snapshot-2")

    def test_capture_coordinates_cannot_bypass_snapshot_check(self):
        _, session, observation = self.make_session()
        for tool, args in (
            ("click", {"element_index": 3, "x": 5, "y": 5}),
            ("click", {"name": "Save", "x": 5}),
            ("scroll", {"element_index": 3, "x": 5, "y": 5, "direction": "down"}),
        ):
            with self.subTest(tool=tool, args=args), self.assertRaises(ValueError):
                session.prepare(tool, args, observation)
        proposal = session.prepare(
            "click",
            {"element_index": 3, "x": 5, "y": 5, "coordinate_space": "atspi"},
            observation,
        )
        self.assertEqual(proposal["arguments"]["snapshot_id"], "snapshot-1")


if __name__ == "__main__":
    unittest.main()
