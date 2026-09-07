"""Structural regressions: all requested desktop destinations use one shell."""
from pathlib import Path

UI = Path(__file__).resolve().parents[1] / "ui"


def test_game_uses_shared_shell_without_recreating_old_sidebar():
    code = (UI / "app.js").read_text(encoding="utf-8")
    render = code.split("async function renderDashboard(", 1)[1].split(
        "function handlePendingCelebrations", 1
    )[0]
    assert "xpShell(mainHTML(game), true)" in render
    assert "sidebarHTML()" not in render
    assert "compactHTML(game)" in render
    assert "bindSidebar()" in render  # Existing content actions are retained.


def test_hall_uses_shared_shell_and_preserves_actions():
    code = (UI / "app.js").read_text(encoding="utf-8")
    hall = code.split("function renderHall()", 1)[1].split("function mainHTML", 1)[0]
    assert "root.innerHTML = xpShell(" in hall
    assert "sidebarHTML()" not in hall
    assert "data-hall-open" in hall
    assert "reopenCompletionEvent" in hall
    assert 'S.view !== "hall"' in code


def test_shell_styles_are_not_loaded_by_passive_overlays():
    html = (UI / "index.html").read_text(encoding="utf-8")
    assert "preview-a.css" in html
    for name in ("overlay.html", "notification.html"):
        assert "preview-a.css" not in (UI / name).read_text(encoding="utf-8")
