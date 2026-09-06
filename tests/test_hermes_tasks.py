"""Hermes tasks: accepted is the ceiling of a send, and nothing is guessed.

The adapter shells out to Hermes's own peer surface (`hermes peer run/status`)
and re-implements nothing. These tests pin the claims discipline — a run id is
ACCEPTED and never more; `completed` needs Hermes's own terminal word AND
output; unrecognised words stay `unknown` with the raw answer attached — plus
the two guards this side owns: duplicate task keys refused locally, and the
depth-1 relay marker that turns a bot-to-bot loop into one refused call.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from yapitalism import hermes
from yapitalism.mcp import server

_RUN_OK = (
    "Warning: this peer does not advertise restart-durable run replay; keep "
    'the run ID.\n{"run_id": "run-42", "status": "pending"}\n'
)


def _fake_run(returns):
    """A _run stub returning queued (code, stdout, stderr) triples."""
    queue = list(returns)

    def run(args, stdin_text=None):
        run.calls.append((list(args), stdin_text))
        return queue.pop(0) if len(queue) > 1 else queue[0]

    run.calls = []
    return run


class ListPeersTests(unittest.TestCase):
    def test_no_peers_is_an_empty_list_not_an_error(self) -> None:
        with patch.object(
            hermes, "_run", _fake_run([(0, "No peers registered. Add one: ...", "")])
        ):
            self.assertEqual(hermes.list_peers(), [])

    def test_tab_rows_parse_and_unparsed_rows_survive(self) -> None:
        stdout = "studio\thttp://100.1.2.3:9119\t[key set]\nweird row\n"
        with patch.object(hermes, "_run", _fake_run([(0, stdout, "")])):
            peers = hermes.list_peers()
        self.assertEqual(peers[0], {"name": "studio", "url": "http://100.1.2.3:9119"})
        # An unparsed peer must not become an invisible one.
        self.assertEqual(peers[1], {"raw": "weird row"})


class SubmitTaskTests(unittest.TestCase):
    def test_a_run_id_is_accepted_and_never_more(self) -> None:
        run = _fake_run([(0, _RUN_OK, "")])
        with patch.object(hermes, "_run", run):
            result = hermes.submit_task("studio/grokbot", "do the thing", "key-1")
        self.assertTrue(result["ok"])
        self.assertEqual(result["state"], "accepted")
        self.assertEqual(result["run_id"], "run-42")
        # The message travels on stdin, marked, with the idempotency key on argv.
        args, stdin_text = run.calls[0]
        self.assertIn("--idempotency-key", args)
        self.assertIn("key-1", args)
        self.assertTrue(stdin_text.startswith("[relayed-by-yapitalism]"))
        self.assertIn("do the thing", stdin_text)

    def test_an_unreachable_peer_is_not_submitted(self) -> None:
        stdout = "Could not reach peer 'studio': <urlopen error ...>\n"
        with patch.object(hermes, "_run", _fake_run([(0, stdout, "")])):
            result = hermes.submit_task("studio", "x", "key-2")
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "not_submitted")
        self.assertIn("Could not reach", result["error"])

    def test_json_without_a_run_id_is_not_submitted(self) -> None:
        with patch.object(hermes, "_run", _fake_run([(0, '{"status": "ok"}', "")])):
            result = hermes.submit_task("studio", "x", "key-3")
        self.assertFalse(result["ok"])
        self.assertIn("without a run id", result["error"])


class TaskStatusTests(unittest.TestCase):
    def _status(self, payload: str):
        with patch.object(hermes, "_run", _fake_run([(0, payload, "")])):
            return hermes.task_status("studio", "run-42")

    def test_running_maps_to_working(self) -> None:
        result = self._status('{"status": "running"}')
        self.assertEqual(result["state"], "working")

    def test_completed_requires_output_not_just_the_word(self) -> None:
        with_output = self._status('{"status": "completed", "output": "did it"}')
        self.assertEqual(with_output["state"], "completed")
        self.assertEqual(with_output["output"], "did it")
        # "It finished and said nothing" is indistinguishable from "it died".
        without = self._status('{"status": "completed"}')
        self.assertEqual(without["state"], "blocked")

    def test_an_unrecognised_word_stays_unknown_with_the_raw_answer(self) -> None:
        result = self._status('{"status": "transcending"}')
        self.assertEqual(result["state"], "unknown")
        self.assertEqual(result["hermes"]["status"], "transcending")

    def test_no_json_is_unknown_not_a_guess(self) -> None:
        result = self._status("peer exploded\n")
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "unknown")


class ToolGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = dict(server._authority_state)

    def tearDown(self) -> None:
        server._authority_state.clear()
        server._authority_state.update(self._saved)

    def test_a_relayed_text_is_refused_on_the_second_hop(self) -> None:
        result = server.hermes_task_send(
            "studio", hermes.mark_task("originally fine"), task_key="loop-1"
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "relay_loop")

    def test_a_burned_task_key_is_refused_locally(self) -> None:
        with patch.object(
            hermes, "submit_task", return_value={"ok": True, "state": "accepted", "run_id": "r"}
        ):
            first = server.hermes_task_send("studio", "task", task_key="dup-1")
            second = server.hermes_task_send("studio", "task", task_key="dup-1")
        self.assertTrue(first["ok"])
        self.assertFalse(second["ok"])
        self.assertEqual(second["reason"], "duplicate_task_key")

    def test_the_write_gate_covers_hermes_sends(self) -> None:
        server._authority_state.update(
            {"transport": "http", "read_only": False,
             "http_writes": False, "remote_create": True}
        )
        result = server.hermes_task_send("studio", "task", task_key="gate-1")
        self.assertEqual(result["reason"], "http_writes_not_allowed")

    def test_a_mid_flight_failure_says_may_have_reached_and_keeps_the_key(self) -> None:
        with patch.object(hermes, "submit_task", side_effect=TimeoutError("60s")):
            result = server.hermes_task_send("studio", "task", task_key="amb-1")
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "unknown_after_dispatch")
        self.assertIn("MAY have", result["error"])
        self.assertEqual(result["task_key"], "amb-1")


if __name__ == "__main__":
    unittest.main()
