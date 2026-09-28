"""Fallback profiles: try the next profile when one can't answer (#104).

One pass per turn over [active profile] + cfg.fallback_profiles, with a
forward-only cursor: each request starts at the profile that answered
last, a profile that failed is not tried again this turn, and the next
turn starts over at the chat profile.

A request has succeeded once its first stream event has arrived. The
client's generators are lazy -- the connection is only made at the first
next() -- so the walker pulls that event itself and hands the caller an
iterator that yields it again. An error after it ends the turn as it
always has.
"""

import itertools
import logging
import re

from ..config import profile_supports_tools, profile_supports_vision
from .client import LLMError, create_client

logger = logging.getLogger(__name__)


def fallback_chain(cfg) -> list:
    """The labels a turn may try, in order: the chat profile first."""
    cfg.provider  # ensures active_profile names a real profile
    chain = [cfg.active_profile]
    for label in cfg.fallback_profiles:
        if label in cfg.profiles and label not in chain:
            chain.append(label)
    return chain


def short_reason(exc) -> str:
    """A few words for a log line or a chat note."""
    status = getattr(exc, "status", None)
    if status:
        return f"HTTP {status}"
    text = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
    for prefix in ("Connection error: ", "Request failed: "):
        if text.startswith(prefix):
            text = text[len(prefix):]
    text = re.sub(r"^\[Errno -?\d+\]\s*", "", text)
    return text[:1].lower() + text[1:]


class FallbackWalker:
    """Opens each request of one turn on the first profile that answers."""

    def __init__(self, cfg, *, cache_key="", needs_tools=False,
                 needs_vision=False, is_interrupted=lambda: False,
                 on_note=lambda text: None):
        self.cfg = cfg
        self.cache_key = cache_key
        self.chain = fallback_chain(cfg)
        self.cursor = 0
        self.attempts = []
        self.active = len(self.chain) > 1
        self._needs = {"tools": needs_tools, "vision": needs_vision}
        self._is_interrupted = is_interrupted
        self._on_note = on_note
        self._segments = []
        self._failures = []
        self._clients = {}
        # Only the last profile that can actually be tried keeps the 429
        # backoff; the others hand over at once.
        self._last_eligible = max(
            i for i in range(len(self.chain)) if self._missing(i) is None)

    def _missing(self, i):
        """The capability profile ``i`` lacks for this turn, or None."""
        if i == 0:
            return None          # the turn was prepared for it
        profile = self.cfg.profiles[self.chain[i]]
        if self._needs["tools"] and not profile_supports_tools(profile):
            return "tools"
        if self._needs["vision"] and not profile_supports_vision(profile):
            return "vision"
        return None

    def _client(self, i):
        if i not in self._clients:
            client = create_client(
                self.cfg, profile=None if i == 0 else self.chain[i],
                cache_key=self.cache_key)
            if i < self._last_eligible:
                client.max_retries = 0
            self._clients[i] = client
        return self._clients[i]

    def _record(self, round_no, label, outcome, error=""):
        if self.active:
            self.attempts.append({"round": round_no, "profile": label,
                                  "outcome": outcome, "error": error})

    def open(self, round_no, request):
        """Return (client, events) from the first profile that answers.

        None means Stop was pressed before an answer arrived.
        """
        while self.cursor < len(self.chain):
            if self._is_interrupted():
                return None
            i, label = self.cursor, self.chain[self.cursor]
            cap = self._missing(i)
            if cap:
                logger.warning("FreeCAD AI: %s skipped — no %s", label, cap)
                self._record(round_no, label, "skipped", f"no {cap}")
                self._segments.append(f"{label} skipped (no {cap})")
                self._failures.append(f"{label}: skipped (no {cap})")
                self.cursor += 1
                continue
            client = self._client(i)
            try:
                events = iter(request(client))
                head = [next(events)]
            except StopIteration:
                head = []
            except LLMError as exc:
                if not self.active:
                    raise
                reason = short_reason(exc)
                if i + 1 < len(self.chain):
                    logger.warning("FreeCAD AI: %s failed — %s; trying %s",
                                   label, reason, self.chain[i + 1])
                else:
                    logger.warning("FreeCAD AI: %s failed — %s; no profile left",
                                   label, reason)
                self._record(round_no, label, "failed", reason)
                if getattr(exc, "kind", "config") == "unreachable":
                    self._segments.append(f"{label} couldn't be reached ({reason})")
                else:
                    self._segments.append(f"{label} was refused ({reason})")
                self._failures.append(f"{label}: {reason}")
                self.cursor += 1
                continue
            self._record(round_no, label, "answered")
            if self._segments:
                logger.warning("FreeCAD AI: answered by %s (fallback %d of %d)",
                               label, i, len(self.chain) - 1)
                self._on_note("⚠ " + "; ".join(self._segments)
                              + f" — answered by {label}")
                self._segments = []
            return client, itertools.chain(head, events)
        raise LLMError("No profile could answer — " + " · ".join(self._failures))
