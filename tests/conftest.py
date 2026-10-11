"""Database the suite is allowed to touch.

Rendering a page is not read-only — the Internacional page calls
`auto_match_traspasos()`, which commits — so a suite pointed at the configured
database rewrites reconciliation state as a side effect of running. The tests
therefore read `VS_BCI_TEST_DB_URL` and nothing else, and skip when it is
unset. They never fall back to `.streamlit/secrets.toml`.

To run them, point the variable at a scratch database (its schema is created
on connect):

    createdb vs_bci_test
    VS_BCI_TEST_DB_URL=postgresql:///vs_bci_test ./venv/bin/python -m pytest
"""
import os

import pytest

TEST_DB_ENV = "VS_BCI_TEST_DB_URL"

SIN_BASE = (
    f"{TEST_DB_ENV} no está configurada. Estas pruebas escriben en la base, "
    "así que no se ejecutan contra la de producción — apunta la variable a una "
    "base de prueba (ver tests/conftest.py)."
)


def test_db_url() -> str | None:
    return os.environ.get(TEST_DB_ENV, "").strip() or None


@pytest.fixture(scope="session")
def db_url() -> str:
    url = test_db_url()
    if not url:
        pytest.skip(SIN_BASE)
    return url
