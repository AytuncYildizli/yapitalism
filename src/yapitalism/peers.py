"""Other machines' yapitalism servers, mounted as backends.

A peer is a yapitalism MCP server on another machine, reached over the tailnet
(or any private network) and mounted into the local registry under its own
namespace: the codex pane `tmux:%0` on the machine `studio` is addressed here as
`studio:tmux:%0`. Everything a peer proves stays proved — receipts pass through
verbatim, because the peer built them next to its own terminals and this side
restating them would be a claim without evidence.

The config is one JSON object per peer in `~/.config/yapitalism/peers.json`,
owner-only, same discipline as the Superset manifest:

    {"peers": {"studio": {"url": "http://100.73.28.102:8792/mcp",
                          "token": "..."}}}

Trust rules, enforced at load rather than documented and hoped for:

- The URL must be plain http(s) with an explicit host.
- A non-loopback peer REQUIRES a token. 0.3.0 servers demand one anyway; a
  config that omits it is a registration that can only 401.
- A peer on a public address is refused unless `"allow_public": true` is set on
  that peer. Terminals over the open internet is a decision someone must make
  with their whole name, not inherit from a typo. Loopback, RFC1918 and the
  CGNAT range tailscale uses (100.64/10) count as private; so do *.ts.net
  MagicDNS names, which only resolve inside a tailnet.
"""

from __future__ import annotations

import ipaddress
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

_MAX_BYTES = 64 * 1024


class PeerConfigError(RuntimeError):
    """The peers file exists and cannot be used as written."""


@dataclass(frozen=True, slots=True)
class Peer:
    name: str
    url: str
    token: str

    def __repr__(self) -> str:  # the token must not reach logs
        return f"Peer(name={self.name!r}, url={self.url!r}, token='<redacted>')"


def peers_path() -> Path:
    override = os.environ.get("YAPITALISM_PEERS")
    if override:
        return Path(override)
    configured = os.environ.get("XDG_CONFIG_HOME")
    root = Path(configured) if configured else Path.home() / ".config"
    return root / "yapitalism" / "peers.json"


_PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "127.0.0.0/8",      # loopback
        "10.0.0.0/8",       # RFC1918
        "172.16.0.0/12",    # RFC1918
        "192.168.0.0/16",   # RFC1918
        "100.64.0.0/10",    # CGNAT — what tailscale hands out
        "::1/128",          # loopback v6
        "fc00::/7",         # ULA
        "fe80::/10",        # link-local v6
    )
)


def _host_is_private(host: str) -> bool:
    if host.lower() == "localhost" or host.lower().endswith(".ts.net"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # An arbitrary DNS name could resolve anywhere; treat as public and make
        # the operator say so. MagicDNS names are recognised above.
        return False
    # A closed list, not `is_private`. Python's predicate says True for the
    # documentation ranges (203.0.113.7 is "private" to it) and False for
    # tailscale's CGNAT space — both measured here after this module confidently
    # assumed otherwise. These are the networks that mean "my LAN or my tailnet":
    for network in _PRIVATE_NETWORKS:
        if address in network:
            return True
    return False


def _validate(name: str, raw: dict) -> Peer:
    if not isinstance(raw, dict):
        raise PeerConfigError(f"peer {name!r} must be an object")
    url = raw.get("url")
    if not isinstance(url, str) or not url:
        raise PeerConfigError(f"peer {name!r} has no url")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise PeerConfigError(f"peer {name!r}: url must be absolute http(s)")
    if parsed.username or parsed.password:
        raise PeerConfigError(f"peer {name!r}: url must not carry userinfo")
    token = raw.get("token", "")
    if not isinstance(token, str):
        raise PeerConfigError(f"peer {name!r}: token must be a string")
    host = parsed.hostname
    is_loopback = host.lower() == "localhost" or (
        _safe_ip(host) is not None and _safe_ip(host).is_loopback
    )
    if not is_loopback and not token:
        raise PeerConfigError(
            f"peer {name!r} is not loopback and has no token; a 0.3.0 server "
            "will 401 it, so this registration can only fail"
        )
    if not _host_is_private(host) and raw.get("allow_public") is not True:
        raise PeerConfigError(
            f"peer {name!r} points at a public address ({host}); terminals over "
            'the open internet need "allow_public": true said explicitly'
        )
    if not name.replace("-", "").replace("_", "").isalnum():
        raise PeerConfigError(f"peer name {name!r} must be alphanumeric with - or _")
    if name in {"tmux", "superset"}:
        raise PeerConfigError(f"peer name {name!r} shadows a local backend")
    return Peer(name=name, url=url.rstrip("/"), token=token)


def _safe_ip(host: str):
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def load_peers(path: Path | None = None) -> list[Peer]:
    """Every configured peer, or an empty list when the file does not exist.

    Absence is the normal state — most machines have no fleet — so no file is no
    peers, silently. A file that EXISTS gets the manifest treatment: owner-only
    permissions, bounded size, strict keys, and any invalid peer fails the whole
    load rather than being skipped, because a skipped peer is a machine the
    operator believes is watched and is not.
    """
    target = path or peers_path()
    try:
        descriptor = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return []
    except OSError as error:
        raise PeerConfigError(f"peers file unreadable: {error}") from None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise PeerConfigError("peers file must be a regular file")
        if info.st_mode & 0o077:
            raise PeerConfigError("peers file must be chmod 600 - it holds tokens")
        if info.st_size > _MAX_BYTES:
            raise PeerConfigError("peers file exceeds 64 KiB")
        with os.fdopen(descriptor, "r") as handle:
            payload = json.load(handle)
    except json.JSONDecodeError as error:
        raise PeerConfigError(f"peers file is not valid JSON: {error}") from None
    entries = payload.get("peers") if isinstance(payload, dict) else None
    if not isinstance(entries, dict):
        raise PeerConfigError('peers file must be {"peers": {...}}')
    return [_validate(name, raw) for name, raw in sorted(entries.items())]
