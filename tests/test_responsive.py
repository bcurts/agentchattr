"""Responsive-layout behavior tests.

Goal: the chat must be usable on a phone WITHOUT changing how it behaves on
desktop. We drive a real browser at phone / laptop widths, toggle UI state
through the app's OWN functions (so we exercise real behavior, not a mock),
and assert on the rendered geometry.

Two groups:
  * mobile_*  - new behavior we want (no sideways scroll; panels overlay the
                chat instead of squeezing it)
  * desktop_* - characterization of CURRENT behavior we must preserve
                (panels stay side-by-side columns; no regression)
"""

from conftest import MOBILE, DESKTOP

TIMELINE = "main#timeline"
RULES = "#rules-panel"
SIDEBAR = "#channel-sidebar"


def _load(page, viewport, server):
    page.set_viewport_size(viewport)
    page.goto(server)
    page.wait_for_timeout(300)


def _box(page, selector):
    return page.locator(selector).bounding_box()


def _doc_overflow(page):
    """How many px the document scrolls horizontally past the viewport."""
    return page.evaluate(
        "() => document.documentElement.scrollWidth - window.innerWidth"
    )


def _open_rules(page):
    page.evaluate("toggleRulesPanel()")
    page.wait_for_timeout(400)  # css margin transition


def _open_jobs(page):
    page.evaluate("toggleJobsPanel()")
    page.wait_for_timeout(400)


def _enable_sidebar(page):
    page.evaluate("setChannelSidebarMode('sidebar')")
    page.wait_for_timeout(400)


# --------------------------------------------------------------------------
# MOBILE - the behavior we are adding
# --------------------------------------------------------------------------

def test_mobile_no_horizontal_overflow(page, server):
    """A phone screen must not scroll sideways in the default view."""
    _load(page, MOBILE, server)
    overflow = _doc_overflow(page)
    assert overflow <= 1, f"page overflows horizontally by {overflow}px on mobile"


def test_mobile_rules_panel_does_not_squeeze_timeline(page, server):
    """Opening the rules panel on a phone must overlay, not shrink the chat."""
    _load(page, MOBILE, server)
    _open_rules(page)
    timeline = _box(page, TIMELINE)
    assert timeline is not None
    assert timeline["width"] >= MOBILE["width"] * 0.85, (
        f"timeline only {timeline['width']}px of {MOBILE['width']}px; "
        "rules panel is stealing horizontal space instead of overlaying"
    )
    assert _doc_overflow(page) <= 1, "rules panel causes horizontal scroll on mobile"


def test_mobile_jobs_panel_does_not_squeeze_timeline(page, server):
    """Opening the jobs panel on a phone must overlay, not shrink the chat."""
    _load(page, MOBILE, server)
    _open_jobs(page)
    timeline = _box(page, TIMELINE)
    assert timeline is not None
    assert timeline["width"] >= MOBILE["width"] * 0.85, (
        f"timeline only {timeline['width']}px of {MOBILE['width']}px; "
        "jobs panel is stealing horizontal space instead of overlaying"
    )
    assert _doc_overflow(page) <= 1, "jobs panel causes horizontal scroll on mobile"


def test_mobile_channel_sidebar_does_not_squeeze_timeline(page, server):
    """Channel-sidebar mode on a phone must overlay, not shrink the chat."""
    _load(page, MOBILE, server)
    _enable_sidebar(page)
    timeline = _box(page, TIMELINE)
    assert timeline is not None
    assert timeline["x"] <= MOBILE["width"] * 0.1, (
        f"timeline starts at x={timeline['x']}; channel sidebar is pushing "
        "the chat sideways instead of overlaying"
    )
    assert _doc_overflow(page) <= 1, "sidebar mode causes horizontal scroll on mobile"


def test_mobile_input_box_is_usable_size(page, server):
    """The message box must be a comfortable size to type in on a phone.

    On a 375px screen the surrounding controls (sender label, session/mic/
    send/schedule buttons) crowded the textarea down to ~102px wide. It needs
    real width, a finger-friendly height, and a >=16px font so iOS does not
    zoom the page when it gets focus.
    """
    _load(page, MOBILE, server)
    box = _box(page, "#input")
    assert box is not None
    assert box["width"] >= MOBILE["width"] * 0.7, (
        f"input only {box['width']}px wide of {MOBILE['width']}px; "
        "surrounding controls are crowding the text box"
    )
    assert box["height"] >= 44, f"input only {box['height']}px tall; too small to tap"
    font_px = page.evaluate(
        "() => parseFloat(getComputedStyle(document.querySelector('#input')).fontSize)"
    )
    assert font_px >= 16, f"input font {font_px}px; <16px makes iOS zoom on focus"


def test_mobile_send_and_mic_align_right(page, server):
    """On a phone, the send button and the mic button sit at the right edge.

    After the input wraps to its own line, the controls were left-packed,
    leaving the send group ending ~57px short of the row's right edge. Send
    and the audio (mic) button should hug the right, with mic just left of send.
    """
    _load(page, MOBILE, server)
    row = _box(page, "#input-row")
    group = _box(page, ".send-group")
    mic = _box(page, "#mic")
    assert row and group and mic
    row_right = row["x"] + row["width"]
    group_right = group["x"] + group["width"]
    mic_right = mic["x"] + mic["width"]
    # send group hugs the right edge of the row
    assert group_right >= row_right - 8, (
        f"send group ends at {group_right}px but row right edge is {row_right}px"
    )
    # mic sits immediately to the left of the send group (grouped on the right)
    gap = group["x"] - mic_right
    assert 0 <= gap <= 16, f"mic not adjacent to send (gap {gap}px)"
    assert mic["x"] >= row["x"] + row["width"] * 0.45, "mic is not on the right side"


# --------------------------------------------------------------------------
# DESKTOP - characterization of behavior that must NOT change
# --------------------------------------------------------------------------

def test_desktop_no_horizontal_overflow(page, server):
    """Laptop view never scrolled sideways; keep it that way."""
    _load(page, DESKTOP, server)
    assert _doc_overflow(page) <= 1


def test_desktop_rules_panel_sits_beside_timeline(page, server):
    """On a laptop, the rules panel stays a side column to the right of chat."""
    _load(page, DESKTOP, server)
    _open_rules(page)
    timeline = _box(page, TIMELINE)
    rules = _box(page, RULES)
    assert timeline and rules
    assert rules["x"] >= timeline["x"] + timeline["width"] - 2, "panel not to the right"
    # same row (vertically overlapping) => side-by-side, not stacked
    assert rules["y"] < timeline["y"] + timeline["height"]
    assert timeline["y"] < rules["y"] + rules["height"]


def test_desktop_channel_sidebar_sits_beside_timeline(page, server):
    """On a laptop, the channel sidebar stays a left column beside the chat."""
    _load(page, DESKTOP, server)
    _enable_sidebar(page)
    timeline = _box(page, TIMELINE)
    sidebar = _box(page, SIDEBAR)
    assert timeline and sidebar
    assert sidebar["x"] + sidebar["width"] <= timeline["x"] + 2, "sidebar not to the left"
    assert sidebar["y"] < timeline["y"] + timeline["height"]
    assert timeline["y"] < sidebar["y"] + sidebar["height"]
