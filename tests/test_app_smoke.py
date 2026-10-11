"""Render every page headlessly and assert nothing raises.

Uses Streamlit's own AppTest harness, so this catches the whole class of bugs
that only appear at render time — bad column names, Decimal vs float, widget
config mismatches — without a browser.

Runs against the scratch database named by VS_BCI_TEST_DB_URL and skips when
it is unset — never against the configured one, since rendering the
Internacional page auto-matches traspasos and commits. See tests/conftest.py.
"""
import pytest

from tests.conftest import SIN_BASE, TEST_DB_ENV, test_db_url

st_testing = pytest.importorskip("streamlit.testing.v1")
AppTest = st_testing.AppTest

PAGES = [
    "📄 Nacional (CLP)",
    "🌎 Internacional (USD)",
    "🔗 Conciliación Traspaso",
    "📈 Dashboard",
    "⚙️ Admin",
]


def _motivo_para_saltar() -> str | None:
    """None when the scratch database is usable, else why it is not."""
    url = test_db_url()
    if not url:
        return SIN_BASE
    try:
        from data.database import init_db

        init_db(url).close()
        return None
    except Exception as e:
        return f"{TEST_DB_ENV} está configurada pero no responde: {type(e).__name__}"


_MOTIVO = _motivo_para_saltar()
pytestmark = pytest.mark.skipif(_MOTIVO is not None, reason=_MOTIVO or "")


def _run(page: str):
    at = AppTest.from_file("app.py", default_timeout=90)
    at.session_state["authenticated"] = True  # bypass the password gate
    # Overrides the real secret, so the app under test cannot open the
    # configured database even though app.py reads st.secrets for it.
    at.secrets["supabase_db_url"] = test_db_url()
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
