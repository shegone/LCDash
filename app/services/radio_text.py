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

import hashlib
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
MAX_TERM_CHANNELS = 24

MIN_CORRECTION_CHARS = 1
MAX_CORRECTION_CHARS = 20000

# Transcriber hint words (docs/MINDSHARE_RADIO_HEALTH.md, v3).
MIN_HINT_CHARS = 2
MAX_HINT_CHARS = 60
MAX_HINTS = 200
MAX_GLOSSARY_CHARS = 600
GLOSSARY_PROMPT_PREFIX = (
    "Public safety radio traffic. Names and terms that may be spoken: "
)
EMPTY_GLOSSARY_VERSION = "empty"

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


# ----------------------------------------------------------------- glossary


def build_glossary(
    hints: Iterable[object],
    watch_phrases: Iterable[object],
    replacement_texts: Iterable[object],
) -> dict[str, str]:
    """The vocabulary the local transcriber is told about.

    Order is priority: admin hint words first, then enabled watch phrases, then
    the ``replace_text`` of the fixes. Duplicates (case-insensitive, whitespace
    collapsed) keep their first spelling and position. Items are joined with
    ``", "`` and the list stops at the first item that would take the string
    past ``MAX_GLOSSARY_CHARS``: it never exceeds that length and a later,
    shorter item never jumps the queue.

    ``version`` is the first 12 hex characters of sha256(hotwords), or
    ``"empty"`` (with empty strings) when there is nothing to say. Pure and
    deterministic, so a listener can compare versions to skip a download.
    """
    seen: set[str] = set()
    chosen: list[str] = []
    length = 0
    full = False
    for source in (hints, watch_phrases, replacement_texts):
        if full:
            break
        for raw in source:
            item = " ".join(str(raw if raw is not None else "").split())
            if not item:
                continue
            key = item.casefold()
            if key in seen:
                continue
            needed = len(item) if not chosen else length + 2 + len(item)
            if needed > MAX_GLOSSARY_CHARS:
                full = True
                break
            seen.add(key)
            chosen.append(item)
            length = needed
    if not chosen:
        return {"version": EMPTY_GLOSSARY_VERSION, "hotwords": "", "prompt": ""}
    hotwords = ", ".join(chosen)
    return {
        "version": hashlib.sha256(hotwords.encode("utf-8")).hexdigest()[:12],
        "hotwords": hotwords,
        "prompt": f"{GLOSSARY_PROMPT_PREFIX}{hotwords}.",
    }


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


# --------------------------------------------------------------- possible noise

# Short clips of room or channel noise make the transcriber invent a stock
# phrase. These are matched against the whole normalized transcript.
NOISE_EXACT_PHRASES = (
    "thank you",
    "thanks",
    "thanks for watching",
    "thank you for watching",
    "you",
    "bye",
    "bye bye",
    "beep",
    "music",
    "applause",
    "like and subscribe",
    "see you next time",
)

# Credit-line boilerplate that carries a variable tail ("Subtitles by the
# Amara.org community", "TAMARA KEITH, CBC NEWS ANCHOR..."). The normalized
# transcript only has to START with one of these, on a word boundary.
NOISE_PREFIX_PHRASES = (
    "subtitles by",
    "transcription by",
    "captioned by",
    "amara.org",
    "subtitle workshop",
    "tamara keith",
)

NOISE_MAX_SECONDS = 1.5
NOISE_MAX_WORDS = 3
_NOISE_SEPARATORS = re.compile(r"[\W_]+")


def _normalize_for_noise(text: str) -> str:
    """Lower-case, punctuation to spaces, whitespace collapsed and trimmed."""
    return _NOISE_SEPARATORS.sub(" ", str(text or "").casefold()).strip()


_NOISE_EXACT = frozenset(_normalize_for_noise(p) for p in NOISE_EXACT_PHRASES)
_NOISE_PREFIXES = tuple(_normalize_for_noise(p) for p in NOISE_PREFIX_PHRASES)


def is_possible_noise(
    text: str,
    duration_seconds: float,
    low_confidence: bool,
    corrected: bool = False,
) -> bool:
    """Whether a transcript is probably invented from background noise.

    Display-only: the caller shows a badge and may skip keyword alerts; the
    stored text is never changed or deleted. Measured on 120 real clips, about
    18% of plain Whisper transcripts of very short clips (usually under 2 s)
    were boilerplate such as "Thanks for watching!", "Bye.", "Beep.",
    "Transcription by CastingWords" or "Subtitles by the Amara.org community".

    Rules, in order:

    * A human-corrected segment is never noise.
    * An empty transcript is not noise (it already reads "(no speech
      recognized)").
    * True when the whole normalized transcript (lower case, punctuation and
      repeated spaces removed) equals a phrase in ``NOISE_EXACT_PHRASES``, or
      starts with a phrase in ``NOISE_PREFIX_PHRASES`` (credit lines whose
      tail varies). A phrase inside real traffic ("10-4 thank you") does not
      count.
    * True when the clip is shorter than 1.5 s, has 3 or fewer words, and the
      transcriber marked it low confidence.
    """
    if corrected:
        return False
    raw = str(text or "")
    if not raw.strip():
        return False
    normalized = _normalize_for_noise(raw)
    if normalized in _NOISE_EXACT:
        return True
    for prefix in _NOISE_PREFIXES:
        if normalized == prefix or normalized.startswith(prefix + " "):
            return True
    try:
        seconds = float(duration_seconds)
    except (TypeError, ValueError):
        return False
    return (
        bool(low_confidence)
        and seconds < NOISE_MAX_SECONDS
        and len(raw.split()) <= NOISE_MAX_WORDS
    )


# A segment this short or shorter is a key-up or a beep, not speech. Ted listened
# to the audio (2026-10-21), and the data agrees: of 416 segments of 2.1 s or
# less on the listener, about 97% were stock phrases such as "Thanks for
# watching!", "Thank you.", "Beep." or credit lines, and real words ("Go ahead.")
# only start to appear just above 2.1 s. Display only, like possible noise: the
# text is kept, and it does NOT suppress keyword alerts (a real two-second call
# that names a watch phrase must still alert).
KEYUP_MAX_SECONDS = 2.1


def is_keyup_length(duration_seconds, corrected: bool = False) -> bool:
    """Whether a clip is short enough to be a key-up or a beep.

    A human-corrected segment is never flagged (a person said it is speech).
    A missing or unreadable duration is not flagged.
    """
    if corrected:
        return False
    try:
        seconds = float(duration_seconds)
    except (TypeError, ValueError):
        return False
    return 0 <= seconds <= KEYUP_MAX_SECONDS


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
