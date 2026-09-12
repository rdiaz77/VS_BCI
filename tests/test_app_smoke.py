"""Render every page headlessly and assert nothing raises.

Uses Streamlit's own AppTest harness, so this catches the whole class of bugs
that only appear at render time — bad column names, Decimal vs float, widget
config mismatches — without a browser.

Requires a reachable database (it reads the configured one); skips otherwise.
"""
import pytest

st_testing = pytest.importorskip("streamlit.testing.v1")
AppTest = st_testing.AppTest

PAGES = [
    "📄 Nacional (CLP)",
    "🌎 Internacional (USD)",
    "🔗 Conciliación Traspaso",
    "📈 Dashboard",
    "⚙️ Admin",
]


def _db_reachable() -> bool:
    try:
        import re
        from pathlib import Path

        from data.database import init_db

        p = Path(".streamlit/secrets.toml")
        if not p.exists():
            return False
        m = re.search(r'supabase_db_url\s*=\s*"(.+?)"', p.read_text())
        if not m:
            return False
        init_db(m.group(1)).close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="no database configured")


def _run(page: str):
    at = AppTest.from_file("app.py", default_timeout=90)
    at.session_state["authenticated"] = True  # bypass the password gate
    at.run()
    if page != PAGES[0]:
        at.sidebar.radio[0].set_value(page).run()
    return at


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_without_exception(page):
    at = _run(page)
    assert not at.exception, f"{page}: {[e.value for e in at.exception]}"


@pytest.mark.parametrize("page", PAGES)
def test_page_shows_no_error_box(page):
    at = _run(page)
    errors = [e.value for e in at.error]
    assert not errors, f"{page}: {errors}"


def test_nacional_has_the_expected_controls():
    at = _run("📄 Nacional (CLP)")
    labels = [w.label for w in at.selectbox] + [w.label for w in at.text_input]
    assert "Tarjeta" in labels
    assert "Buscar en descripción" in labels


def test_admin_lists_cards():
    at = _run("⚙️ Admin")
    assert any("Tarjetas en la base" in m.value for m in at.markdown)
