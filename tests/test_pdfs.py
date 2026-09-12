"""End-to-end parser checks against whatever real statements exist locally.

Statements are gitignored (they contain real cardholder data), so these tests
skip cleanly on a fresh clone and in CI. Point BCI_PDF_DIR at a folder of
statements to run them elsewhere.
"""
import glob
import os

import pytest

from data.common import detectar_tipo_cartola
from data.extractor_internacional import leer_cartola_internacional
from data.extractor_nacional import leer_cartola_nacional

SEARCH_DIRS = [
    os.environ.get("BCI_PDF_DIR"),
    "Cartolas",
    "Cartolas/2025",
    os.path.expanduser("~/Desktop/Bci"),
]


def _pdfs():
    found = []
    for d in SEARCH_DIRS:
        if d and os.path.isdir(d):
            found += glob.glob(os.path.join(d, "*.pdf"))
    return sorted(set(found))


pytestmark = pytest.mark.skipif(not _pdfs(), reason="no local statement PDFs")


@pytest.fixture(scope="module")
def parsed():
    out = []
    for path in _pdfs():
        raw = open(path, "rb").read()
        tipo = detectar_tipo_cartola(raw)
        if tipo is None:
            continue
        fn = leer_cartola_internacional if tipo == "INTERNACIONAL" else leer_cartola_nacional
        rows, meta = fn(raw, filename=os.path.basename(path))
        out.append((path, tipo, rows, meta))
    return out


def test_todos_los_pdfs_se_detectan(parsed):
    assert len(parsed) == len(_pdfs()), "algún PDF no fue reconocido como cartola BCI"


def test_toda_cartola_tiene_identidad(parsed):
    for path, _tipo, _rows, meta in parsed:
        assert meta["TARJETA_ULT4"], f"sin número de tarjeta: {path}"
        assert meta["FECHA_ESTADO"], f"sin fecha de estado: {path}"
        assert meta["ARCHIVO_ORIGEN"], f"sin clave: {path}"


def test_toda_cartola_tiene_transacciones(parsed):
    for path, _tipo, rows, _meta in parsed:
        assert rows, f"no se extrajo ninguna transacción de {path}"


def test_filas_heredan_tarjeta_y_origen(parsed):
    for path, tipo, rows, meta in parsed:
        for r in rows:
            assert r["ORIGEN"] == tipo, path
            assert r["TARJETA_ULT4"] == meta["TARJETA_ULT4"], path
            assert r["ARCHIVO_ORIGEN"] == meta["ARCHIVO_ORIGEN"], path


def test_misma_cartola_bajo_distinto_nombre_colisiona(parsed):
    """Two files that are the same statement must produce the same key."""
    by_key = {}
    for path, _tipo, rows, meta in parsed:
        k = meta["ARCHIVO_ORIGEN"]
        if k in by_key:
            # Same statement saved twice — the parse must agree.
            assert len(rows) == by_key[k][0], f"{path} vs {by_key[k][1]}: distinto nº de filas"
        else:
            by_key[k] = (len(rows), path)


def test_montos_son_numericos(parsed):
    for path, tipo, rows, _meta in parsed:
        col = "MONTO_OPERACION" if tipo == "INTERNACIONAL" else "MONTO_TOTAL"
        for r in rows:
            assert isinstance(r[col], (int, float)), f"{path}: {r[col]!r} no es numérico"
