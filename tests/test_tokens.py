"""The bounded token record both backends deduplicate with."""

from __future__ import annotations

import unittest

from yapitalism.tokens import BoundedTokens


class BoundedTokenTests(unittest.TestCase):
    def test_membership_is_what_a_duplicate_check_needs(self) -> None:
        seen = BoundedTokens()
        seen.add("a")
        self.assertIn("a", seen)
        self.assertNotIn("b", seen)

    def test_the_oldest_entries_are_dropped_at_the_ceiling(self) -> None:
        """A plain set was a leak in a service that runs for weeks."""
        seen = BoundedTokens(capacity=3)
        for token in ("a", "b", "c", "d"):
            seen.add(token)
        self.assertEqual(len(seen), 3)
        self.assertNotIn("a", seen)
        self.assertIn("d", seen)

    def test_re_adding_does_not_extend_an_entry(self) -> None:
        """A token's age is when it was FIRST used.

        If a refused duplicate refreshed the entry that refused it, a client
        retrying in a loop would keep its own token alive forever and push out
        every other one.
        """
        seen = BoundedTokens(capacity=2)
        seen.add("a")
        seen.add("b")
        seen.add("a")          # refused duplicate, must not move "a" to newest
        seen.add("c")
        self.assertNotIn("a", seen)
        self.assertIn("b", seen)
        self.assertIn("c", seen)

    def test_discard_forgets_a_token(self) -> None:
        seen = BoundedTokens()
        seen.add("a")
        seen.discard("a")
        seen.discard("a")      # idempotent
        self.assertNotIn("a", seen)

    def test_a_nonsense_capacity_is_refused(self) -> None:
        for bad in (0, -1, True, 1.5, "8"):
            with self.subTest(capacity=bad), self.assertRaises(ValueError):
                BoundedTokens(capacity=bad)  # type: ignore[arg-type]


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
