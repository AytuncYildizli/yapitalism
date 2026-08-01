from __future__ import annotations

import unittest

from yapitalism.canary import canary_observed, normalize_terminal_text


class CanaryTests(unittest.TestCase):
    def test_matches_line_wrapped_canary(self) -> None:
        text = "VOICE_PROGRESS_\nCANARY_20260730"
        self.assertTrue(canary_observed(text, "VOICE_PROGRESS_CANARY_20260730"))

    def test_ignores_ansi_sequences(self) -> None:
        text = "\x1b[32mCANARY_OK\x1b[0m"
        self.assertEqual(normalize_terminal_text(text), "CANARY_OK")
        self.assertTrue(canary_observed(text, "CANARY_OK"))

    def test_rejects_partial_canary(self) -> None:
        self.assertFalse(canary_observed("CANARY_2026", "CANARY_20260730"))

    def test_empty_canary_is_invalid(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            canary_observed("anything", "  \n")


if __name__ == "__main__":
    unittest.main()
