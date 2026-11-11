"""Pure screen-text classifiers shared by the Windows and Unix activity checkers.

Both look only at the live bottom of the screen (physical rows, blank or not) and
only at lines shaped like real status/permission UI, so scrolled-back history or
quoted examples never count as live state.
"""
import re

BUSY_ROWS = 12
INPUT_ROWS = 20

_MARKER = r"(?:esc|ctrl\+c) to (?:interrupt|cancel)"
_TIMER = r"\d+\s*[smh]\b"
# A whole status line: optional spinner/bullet glyphs, a short label with no colon
# ("Thinking…", "Working"), then ONE parenthesis ending the line that holds the
# marker and a timer joined by the TUIs' separators (· • ,):
#   "✻ Thinking… (12s · esc to interrupt)"  "• Working (1m 3s • esc to interrupt)"
#   "⠋ Thinking (esc to cancel, 5s)"
_BUSY_LINE = re.compile(
    r"^[^\w(:]*[^\W\d_][\w …\.\-]{0,40}?\s*"
    r"\((?=[^)\n]*" + _MARKER + r")(?=[^)\n]*" + _TIMER + r")"
    r"[^)\n]*[·•,][^)\n]*\)\s*$",
    re.I,
)

# Option rows of a live permission menu.
_OPTION_ROW = re.compile(r"^[\s│|]*[❯>›]?\s*(?:\d+\.\s+\S|yes, proceed|\(y\))", re.I)
_QUESTION_LINE = re.compile(
    r"^[\s│|╭╰─>❯›•●]*(?:do you want to (?:proceed|make this edit|create|allow|run|approve|apply)"
    r"|would you like to (?:run the following command|make the following edits?|apply|allow)"
    r"|(?:allow|approve) (?:codex|claude|this|the following)\b.*\?\s*$)",
    re.I,
)
_YN_LINE = re.compile(r"[\[(]y/n[\])]\??\s*$", re.I)
_ENTER_LINE = re.compile(r"^[\s│|]*(?:press )?enter to confirm", re.I)
# The first option of an approval menu ("› 1. Yes, proceed (y)", "1. Yes, proceed"). Line-anchored
# with only menu decoration before it, so quoted/inline text never matches.
_YES_PROCEED_LINE = re.compile(
    r"^[\s│|╭╰─❯>›•●]*1\.\s+yes,?\s+proceed(?:\s*\((?:y|enter)\))?[\s│|╮╯─]*$", re.I)
# "› 1. Allow" / "2. Allow for this session" rows of an allow/deny menu (same whole-line anchoring).
_ALLOW_LINE = re.compile(
    r"^[\s│|╭╰─❯>›•●]*[12]\.\s+allow(?:\s+for\s+this\s+session)?(?:\s*\((?:y|a|enter)\))?[\s│|╮╯─]*$", re.I)

# "2. Yes, and don't ask again" (Codex adds " for commands that start with `x` (p)"). Whole line only.
_DONT_ASK_LINE = re.compile(
    r"^[\s│|╭╰─❯>›•●]*2\.\s+yes,?\s+and\s+don['’]t\s+ask\s+again"
    r"(?:\s+for\s+commands\s+that\s+start\s+with\s+`[^`\n]+`)?(?:\s*\([a-z]\))?[\s│|╮╯─]*$", re.I)


def _tail(rows, n):
    """Last n physical rows first, then drop blanks (so old text can't slide in)."""
    if isinstance(rows, str):
        rows = rows.splitlines()
    return [r.rstrip() for r in list(rows)[-n:] if r.strip()]


def has_busy_marker(rows) -> bool:
    return any(_BUSY_LINE.match(r.strip()) for r in _tail(rows, BUSY_ROWS))


# "Turn finished" summary lines, shown when the agent stops working:
#   Claude: "✻ Worked for 1m 3s" / "✻ Baked for 2m 5s" / "done 1:03"
#   Codex:  "─ Worked for 2m 14s ────────"
# Whole line only: optional glyph/box decoration, the phrase, a duration, nothing else.
_DURATION = r"(?:\d+\s*[hms]\b\s*)+|\d{1,2}:\d{2}(?::\d{2})?"
_DONE_LINE = re.compile(
    r"^[\s─━│┃✻✽✳✢✶✷●•⏺◆✓·*]*(?:[a-z]+ed for|done(?: in)?)\s+(?:" + _DURATION + r")[\s─━]*$",
    re.I,
)
DONE_ROWS = 8


def has_done_marker(rows) -> bool:
    """True when a finished-turn summary line is among the last few live rows."""
    return any(_DONE_LINE.match(r.strip()) for r in _tail(rows, DONE_ROWS))


def agent_idle_by_marker(rows) -> bool:
    """A done summary is visible and no live busy status is: the agent is definitely not working."""
    return has_done_marker(rows) and not has_busy_marker(rows)


def needs_user_input(rows) -> bool:
    live = _tail(rows, INPUT_ROWS)
    if any(_YN_LINE.search(r) or _ENTER_LINE.match(r) or _YES_PROCEED_LINE.match(r) or _ALLOW_LINE.match(r)
           or _DONT_ASK_LINE.match(r) for r in live):
        return True
    return (any(_QUESTION_LINE.match(r) for r in live)
            and any(_OPTION_ROW.match(r) for r in live))
