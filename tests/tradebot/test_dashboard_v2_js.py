"""Frontend safety tests for dashboard.v2.js (Phase 11, closes A14).

Static-analysis level: proves the unsafe-sink and unsafe-URL classes are absent
from the source and that safe primitives are used. Behavioural DOM assertions
run under Node+jsdom in CI Gate 5 when the toolchain is available; these checks
are the always-on floor that needs no Node.
"""

import re
from pathlib import Path

import pytest

V2 = Path(__file__).resolve().parents[2] / "dashboard" / "static" / "dashboard.v2.js"
SOURCE = V2.read_text(encoding="utf-8")


def _strip_comments(source: str) -> str:
    """Executable code only: drop block comments and // comments.

    `//` inside a string literal (e.g. 'http://x') is preserved by requiring
    the slashes to not be preceded by a colon.
    """

    without_blocks = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    lines = []
    for line in without_blocks.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("//"):
            continue
        lines.append(re.sub(r"(?<!:)//.*$", "", line))
    return "\n".join(lines)


CODE = _strip_comments(SOURCE)

UNSAFE_SINKS = ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write",
                "eval(", "new Function(", "setTimeout('", 'setTimeout("')


def test_v2_exists_and_is_strict_mode():
    assert V2.is_file()
    assert "'use strict';" in SOURCE


@pytest.mark.parametrize("sink", UNSAFE_SINKS)
def test_no_unsafe_dom_sinks(sink):
    """A14: not one HTML-injection sink survives in executable code."""
    assert sink not in CODE, f"unsafe sink present: {sink}"


def test_untrusted_values_use_textcontent():
    assert "textContent" in CODE
    # Every element factory sets text via textContent, never markup.
    assert re.search(r"node\.textContent\s*=", CODE)


def test_url_vetting_rejects_dangerous_schemes():
    """safeUrl only admits https:, plus http: on loopback."""
    assert "new URL(" in CODE
    assert "url.protocol === 'https:'" in CODE
    # Loopback-only http allowance.
    for host in ("'localhost'", "'127.0.0.1'"):
        assert host in CODE
    # Dangerous schemes are never allow-listed anywhere.
    for scheme in ("javascript:", "data:text/html", "vbscript:"):
        assert scheme not in CODE


def test_links_get_noopener_noreferrer():
    assert "'noopener noreferrer'" in CODE or '"noopener noreferrer"' in CODE


def test_no_model_endpoint_editing_in_ui():
    """A09: the UI cannot change the model endpoint or the allowlist."""
    for forbidden in ("aiBaseUrl", "ai_base_url", "allowlist", "/llm/config"):
        assert forbidden not in CODE


def test_truthful_state_rendering_exists():
    for state in ("empty", "loading", "error", "stale", "degraded"):
        assert state in CODE


def test_accessibility_primitives_present():
    for attr in ("aria-label", "aria-pressed", "aria-labelledby", "scope",
                 "role"):
        assert attr in CODE


def test_shadow_capital_rendered_separately():
    assert "summary--shadow" in CODE
    assert "summary--active" in CODE
    assert "Excluded from active totals." in SOURCE


def test_filters_cover_required_views():
    for value in ("'active'", "'shadow'", "'dark_horse'", "'dark_horse_daily'",
                  "'archived'", "'all'"):
        assert value in CODE


def test_legacy_v1_dashboard_still_present_but_v2_is_clean():
    """The v1 file retains its innerHTML usage; v2 must not inherit it."""
    v1 = V2.with_name("dashboard.v1.js")
    if v1.is_file():
        assert "innerHTML" in v1.read_text(encoding="utf-8")  # baseline A14
    assert "innerHTML" not in CODE
