"""Pure text rules for radio transcripts: fixes, watch-term matching, validation.

Contract: docs/MINDSHARE_RADIO_CORRECTIONS.md (v2). Everything here is a pure
function of its inputs (no database, no clock, no network) so it can be tested
exhaustively and cannot become a way to fail an ingest by itself.

Safety properties, each pinned by tests:

* No regular expressions from users. Fixes and watch terms are LITERAL text:
  every rule is passed through ``re.escape`` before it is ever compiled, so a
  hostile ``(a+)+$`` is just the characters ``(a+)+$``. The only regex features
  in play are fixed lookarounds around that escaped literal, which cannot
  backtrack catastrophically.
* Whole-word matching uses lookarounds on ``\\w`` instead of ``\\b`` so a rule
  that begins or ends in punctuation (``10-4``, ``(x)``) still means what it
  says.
* Fixes are applied in ONE pass over the text with the longest ``find_text``
  first, so one rule's output is never re-scanned by another (no cascades) and
  the result is deterministic.
* Fixes never run on a human correction. Callers apply them to the machine
  transcript only.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Sequence

MAX_FIX_RULES = 200
MAX_FIND_CHARS = 80
MAX_REPLACE_CHARS = 200

MIN_PHRASE_CHARS = 2
MAX_PHRASE_CHARS = 80
MAX_WATCH_TERMS = 100
MAX_TERM_CHANNELS = 16

MIN_CORRECTION_CHARS = 1
MAX_CORRECTION_CHARS = 20000

# Bidirectional override/isolate characters can make text read differently from
# how it is stored, so they are refused in anything a person types.
_BIDI_CONTROLS = frozenset(
    "‪‫‬‭‮⁦⁧⁨⁩؜‎‏"
)


class TextRuleError(ValueError):
    """A typed value failed validation; the message is safe to show the caller."""


def _has_forbidden_control(text: str, *, allow_newline_tab: bool) -> bool:
    for character in text:
        if character in _BIDI_CONTROLS:
            return True
        if unicodedata.category(character) == "Cc":
            if allow_newline_tab and character in ("\n", "\t"):
                continue
            return True
    return False


def clean_correction_text(value: object) -> str:
    """Validate a human-typed correction: plain text, 1-20000 chars after strip."""
    if not isinstance(value, str):
        raise TextRuleError("The 'text' value must be a string.")
    text = value.replace("\r\n", "\n").strip()
    if not MIN_CORRECTION_CHARS <= len(text) <= MAX_CORRECTION_CHARS:
        raise TextRuleError(
            f"The 'text' value must be {MIN_CORRECTION_CHARS} to "
            f"{MAX_CORRECTION_CHARS} characters."
        )
    if _has_forbidden_control(text, allow_newline_tab=True):
        raise TextRuleError("The 'text' value contains control characters.")
    return text


def clean_single_line(value: object, *, label: str, minimum: int, maximum: int) -> str:
    """Validate a one-line value (a find string, a phrase)."""
    if not isinstance(value, str):
        raise TextRuleError(f"The '{label}' value must be a string.")
    text = value.strip()
    if not minimum <= len(text) <= maximum:
        raise TextRuleError(
            f"The '{label}' value must be {minimum} to {maximum} characters."
        )
    if _has_forbidden_control(text, allow_newline_tab=False):
        raise TextRuleError(f"The '{label}' value contains control characters.")
    return text


def clean_replacement(value: object) -> str:
    """The replacement text: may be empty (deleting a word), one line, <= 200."""
    if not isinstance(value, str):
        raise TextRuleError("The 'replace' value must be a string.")
    text = value.strip()
    if len(text) > MAX_REPLACE_CHARS:
        raise TextRuleError(
            f"The 'replace' value must be at most {MAX_REPLACE_CHARS} characters."
        )
    if _has_forbidden_control(text, allow_newline_tab=False):
        raise TextRuleError("The 'replace' value contains control characters.")
    return text


def clean_channels(value: object) -> list[str]:
    """A watch term's channel filter: [] (every channel) or channel names."""
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_TERM_CHANNELS:
        raise TextRuleError("The 'channels' value must be a short list of channels.")
    channels: list[str] = []
    for item in value:
        if not isinstance(item, str) or not re.fullmatch(r"[a-z0-9_]{1,32}", item):
            raise TextRuleError("The 'channels' value contains an invalid channel.")
        if item not in channels:
            channels.append(item)
    return channels


# ----------------------------------------------------------------- literals


def _literal_pattern(text: str, *, whole_word: bool) -> str:
    """A pattern for ``text`` as literal characters.

    Runs of whitespace in the rule match any run of whitespace in the
    transcript (a transcript may break a phrase over a space or a newline).
    Everything else is escaped, so there is no way to smuggle regex syntax in.
    """
    parts = [re.escape(piece) for piece in text.split()]
    body = r"\s+".join(parts)
    if whole_word:
        return rf"(?<!\w){body}(?!\w)"
    return body


# --------------------------------------------------------------------- fixes


@dataclass(frozen=True)
class FixRule:
    find_text: str
    replace_text: str
    whole_word: bool = True


class FixSet:
    """A compiled, immutable set of replacement fixes."""

    def __init__(self, rules: Iterable[FixRule]) -> None:
        # Longest find first; ties broken by text so the order is stable.
        ordered = sorted(
            (rule for rule in rules if rule.find_text.strip()),
            key=lambda rule: (-len(rule.find_text), rule.find_text.lower()),
        )
        self._replacements: dict[str, str] = {}
        branches: list[str] = []
        for index, rule in enumerate(ordered[:MAX_FIX_RULES]):
            name = f"r{index}"
            self._replacements[name] = rule.replace_text
            branches.append(
                f"(?P<{name}>{_literal_pattern(rule.find_text, whole_word=rule.whole_word)})"
            )
        self._pattern = (
            re.compile("|".join(branches), re.IGNORECASE) if branches else None
        )
        self.rule_count = len(branches)

    def apply(self, text: str) -> str:
        if not text or self._pattern is None:
            return text
        replacements = self._replacements

        def substitute(match: re.Match) -> str:
            return replacements[match.lastgroup]

        return self._pattern.sub(substitute, text)


EMPTY_FIXES = FixSet(())


# ---------------------------------------------------------------- watch terms


def compile_phrase(phrase: str) -> re.Pattern:
    """Whole-word, case-insensitive, literal match for one watch phrase."""
    return re.compile(_literal_pattern(phrase, whole_word=True), re.IGNORECASE)


def find_match(pattern: re.Pattern, text: str) -> bool:
    return bool(text) and pattern.search(text) is not None


def term_applies_to_channel(channels: Sequence[str] | None, channel: str) -> bool:
    """An empty channel list means every channel."""
    return not channels or channel in channels


def make_snippet(text: str, limit: int = 300) -> str:
    """The first ``limit`` characters of display text, whitespace flattened."""
    flat = " ".join(str(text or "").split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1].rstrip() + "…"
