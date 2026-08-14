"""What the operator actually hears.

Tool names are read by the model; `speak` is read to a person. These sentences were
saying "pane tmux:%6", which is heard as "pane tmux percent six" — jargon plus a raw
identifier, in the one channel a human consumes directly.
"""

from __future__ import annotations

import unittest

from yapitalism.mcp.server import _spoken_pane_name


class SpokenPaneNameTests(unittest.TestCase):
    def test_a_pane_is_named_by_its_folder_and_runtime(self) -> None:
        self.assertEqual(
            _spoken_pane_name("/Users/a/projects/relayproof", "codex"),
            "relayproof klasorundeki codex",
        )

    def test_a_missing_directory_falls_back_to_the_runtime_alone(self) -> None:
        """Better a short true sentence than an empty possessive."""
        self.assertEqual(_spoken_pane_name("", "kimi"), "kimi")

    def test_a_trailing_slash_does_not_produce_an_empty_name(self) -> None:
        self.assertEqual(
            _spoken_pane_name("/Users/a/work/whip/", "claude"), "whip klasorundeki claude"
        )

    def test_no_spoken_line_reads_a_target_id_aloud(self) -> None:
        """The id belongs in the payload, where the model needs it for the next call.

        `panes_list`'s docstring already tells the model to name panes by project or
        folder; these sentences were contradicting it exactly where it counts.
        """
        import pathlib

        source = (
            pathlib.Path(__file__).resolve().parent.parent
            / "src/yapitalism/mcp/server.py"
        ).read_text()
        spoken = [
            line for line in source.splitlines()
            if "speak" in line or line.strip().startswith('f"')
        ]
        offenders = [line.strip() for line in spoken if "target_id}" in line]
        self.assertEqual(offenders, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
