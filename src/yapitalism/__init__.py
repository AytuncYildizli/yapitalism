"""Yapitalism public API."""

from importlib.metadata import PackageNotFoundError, version as _installed_version

from .model import EvidenceEvent, Leg, LegState, Provenance, Receipt, Status

__all__ = [
    "EvidenceEvent",
    "Leg",
    "LegState",
    "Provenance",
    "Receipt",
    "Status",
    "__version__",
]

#: Read from the installed distribution rather than written here. This was a
#: hand-maintained literal and it said "0.1.0.dev0" on a published 0.2.1 — three
#: releases stale, because nothing failed when it drifted and nothing looked at it.
#: A version a human has to remember to bump is a version that will be wrong; the
#: only number that cannot disagree with the wheel is the wheel's own.
try:
    __version__ = _installed_version("yapitalism")
except PackageNotFoundError:  # a source tree that was never installed
    __version__ = "0+unknown"
