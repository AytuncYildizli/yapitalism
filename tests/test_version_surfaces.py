"""Which version does this thing say it is?

Two surfaces answer that question and both were wrong at 0.2.1, in the same way and
for the same reason: a number written by hand somewhere it was never read back.

- `yapitalism.__version__` was the literal "0.1.0.dev0", three releases stale.
- The MCP handshake reported "3.4.7", which is FastMCP's version — left unset,
  FastMCP fills `serverInfo` with its own.

Neither broke anything, which is exactly why they drifted: nothing failed, and
nobody looked. Both now derive from the installed distribution, and these tests
fail if either grows a literal again.
"""

from __future__ import annotations

import pathlib
import re
import tomllib
import unittest


REPO = pathlib.Path(__file__).resolve().parent.parent


def declared_version() -> str:
    """The version in pyproject, which is the one the wheel is built with."""
    data = tomllib.loads((REPO / "pyproject.toml").read_text())
    return data["project"]["version"]


def _distribution_location() -> str:
    """Where the metadata that answers `__version__` actually lives."""
    from importlib.metadata import distribution

    return str(distribution("yapitalism").locate_file(""))


class VersionSurfaceTests(unittest.TestCase):
    def test_the_package_version_is_not_a_hand_written_literal(self) -> None:
        """The specific failure: a literal that nothing reads back cannot stay true.

        Asserted against the source rather than the value, because the value was
        *plausible* the whole time it was wrong — "0.1.0.dev0" looks like a version.
        """
        source = (REPO / "src/yapitalism/__init__.py").read_text()
        literal = re.search(r'^__version__\s*=\s*["\']', source, re.MULTILINE)
        self.assertIsNone(
            literal,
            "__version__ is assigned a literal again; derive it from the installed "
            "distribution so it cannot disagree with the wheel",
        )
        self.assertIn("importlib.metadata", source)

    def test_the_installed_version_matches_pyproject(self) -> None:
        """Only meaningful when the code being imported IS the installed distribution.

        Deriving from metadata has one sharp edge worth naming: run this tree via
        `PYTHONPATH=src` on a machine that also has an older yapitalism pip-installed,
        and `__version__` reports the *installed* one while the code executing is the
        checkout. That is an environment artifact, not a defect in the tree, and this
        test would otherwise fail forever on such a machine for the wrong reason.

        In the case that actually matters — a wheel a stranger installs — metadata and
        code come out of the same archive and cannot disagree. CI installs the checkout
        (`pip install -e .`), so this assertion runs there.
        """
        import yapitalism

        if yapitalism.__version__ == "0+unknown":
            self.skipTest("source tree is not installed; nothing to compare against")
        editable_into_this_tree = pathlib.Path(_distribution_location()).is_relative_to(
            REPO
        )
        if editable_into_this_tree and yapitalism.__version__ != declared_version():
            # An editable install records the version from the moment it was made and
            # does not follow later bumps. Skipped rather than failed: the tree is
            # correct, the developer's dist-info is stale, and failing here would be
            # blaming the repository for the environment.
            self.skipTest(
                f"stale editable install: dist-info says {yapitalism.__version__}, "
                f"pyproject says {declared_version()} — re-run `pip install -e .`"
            )
        self.assertEqual(yapitalism.__version__, declared_version())

    def test_the_mcp_handshake_advertises_this_project_not_fastmcp(self) -> None:
        """`serverInfo.version` is the answer to "which yapitalism is this".

        Left unset it is FastMCP's own version — a number matching no release of this
        project, and one that moves when a dependency updates.
        """
        import yapitalism
        from yapitalism.mcp.server import mcp

        self.assertEqual(mcp.name, "yapitalism")
        self.assertEqual(mcp.version, yapitalism.__version__)

        import fastmcp

        self.assertNotEqual(
            mcp.version,
            fastmcp.__version__,
            "the server is introducing itself with FastMCP's version",
        )

    def test_every_version_file_agrees(self) -> None:
        """pyproject, server.json (twice) and the changelog move together.

        server.json carries the version in two places — the server and its package —
        and the registry accepts a mismatch silently.
        """
        import json

        declared = declared_version()
        server = json.loads((REPO / "server.json").read_text())
        self.assertEqual(server["version"], declared)
        for package in server.get("packages", []):
            self.assertEqual(package["version"], declared)
        changelog = (REPO / "CHANGELOG.md").read_text()
        self.assertIn(f"## [{declared}]", changelog)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
