"""Unit tests for the pure parsing/classification logic.

No database and no PDF fixtures — statements contain real cardholder data and
are deliberately gitignored. The PDF parsers themselves are exercised by
tests/test_pdfs.py, which skips when no local statements are available.
"""
import pytest

from data.categorias import opciones
from data.common import build_archivo_origen, extraer_identidad
from data.database import (
    STATIC_TIPO_GASTO_INTL,
    STATIC_TIPO_GASTO_NAC,
    auto_tipo_gasto,
)
from data.extractor_internacional import _to_float
from data.extractor_nacional import (
    LINE_RE,
    _ddmmyy_to_mmddyy,
    normalizar_monto_clp,
    parse_nacional,
)

HEADER = (
    "ESTADO DE CUENTA NACIONAL DE TARJETA DE CREDITO "
    "NOMBRE DEL TITULAR RAFAEL P. DIAZ "
    "N° DE TARJETA DE CRÉDITO XXXXXXXXXXXX4364 "
    "FECHA ESTADO DE CUENTA 23/01/2026"
)


# ---------------------------------------------------------------- identity
def test_extrae_titular_tarjeta_y_fecha():
    got = extraer_identidad(HEADER)
    assert got["TARJETA_ULT4"] == "4364"
    assert got["TITULAR_COMPLETO"] == "RAFAEL P. DIAZ"
    assert got["TITULAR_NOMBRE"] == "Rafael"
    assert got["FECHA_ESTADO"] == "23-01-2026"


def test_header_partido_en_varias_lineas():
    multiline = HEADER.replace(" N° DE TARJETA", "\n   N° DE TARJETA")
    assert extraer_identidad(multiline)["TARJETA_ULT4"] == "4364"


def test_identidad_faltante_no_revienta():
    got = extraer_identidad("documento sin encabezado")
    assert got == {
        "TITULAR_NOMBRE": None,
        "TITULAR_COMPLETO": None,
        "TARJETA_ULT4": None,
        "FECHA_ESTADO": None,
    }


def test_mismo_titular_distinta_tarjeta_da_claves_distintas():
    """The whole point of keying on the card instead of the name."""
    a = build_archivo_origen("BCI_NAC", "4364", "Rafael", "24-02-2026", "a.pdf")
    b = build_archivo_origen("BCI_NAC", "7325", "Rafael", "24-02-2026", "b.pdf")
    assert a != b


def test_misma_cartola_distinto_nombre_de_archivo_da_misma_clave():
    a = build_archivo_origen("BCI_NAC", "4364", "Rafael", "23-01-2026", "uno.pdf")
    b = build_archivo_origen("BCI_NAC", "4364", "Rafael", "23-01-2026", "otro (1).pdf")
    assert a == b == "BCI_NAC_4364_23-01-2026"


def test_sin_tarjeta_cae_al_nombre_y_luego_al_archivo():
    assert build_archivo_origen("BCI_NAC", None, "Rafael", "23-01-2026", "x.pdf") == (
        "BCI_NAC_Rafael_23-01-2026"
    )
    assert build_archivo_origen("BCI_NAC", None, None, None, "x.pdf") == "x.pdf"


# ---------------------------------------------------------------- amounts
@pytest.mark.parametrize(
    "raw,expected",
    [("$ 1.234.567", 1234567), ("83.139", 83139), ("-376.712", -376712), ("0", 0)],
)
def test_monto_clp(raw, expected):
    assert normalizar_monto_clp(raw) == expected


def test_monto_clp_invalido():
    assert normalizar_monto_clp("N/A") is None


@pytest.mark.parametrize(
    "raw,expected",
    [("49,44", 49.44), ("-17,35", -17.35), ("1.089,35", 1089.35), ("US$ 527,49", 527.49)],
)
def test_monto_usd(raw, expected):
    assert _to_float(raw) == pytest.approx(expected)


# ---------------------------------------------------------------- dates
def test_ddmmyy_a_mmddyy():
    assert _ddmmyy_to_mmddyy("23/01/26") == "01/23/26"


def test_ddmmyy_malformado_se_devuelve_igual():
    assert _ddmmyy_to_mmddyy("no-es-fecha") == "no-es-fecha"


# ---------------------------------------------------------------- categories
def test_historico_gana_sobre_reglas_estaticas():
    hist = {("INTERNACIONAL", "UBER TRIP"): "Taxi"}
    assert auto_tipo_gasto("UBER TRIP", hist, "INTERNACIONAL") == "Taxi"


def test_historico_no_cruza_entre_origenes():
    """A national row must not inherit an international-only category."""
    hist = {("INTERNACIONAL", "CANVA"): "Canva"}
    assert auto_tipo_gasto("CANVA", hist, "NACIONAL") != "Canva"


def test_reglas_estaticas_por_origen():
    assert auto_tipo_gasto("UBER *TRIP", {}, "INTERNACIONAL") == "Huber"
    assert auto_tipo_gasto("IMPUESTO DECRETO LEY 3475", {}, "NACIONAL") == "Impuesto"
    assert auto_tipo_gasto("ALGO DESCONOCIDO", {}, "NACIONAL") == ""


def test_todas_las_reglas_apuntan_a_categorias_validas():
    """Static rules must only emit values the dropdown accepts.

    Caught a real bug: FACEBK -> "Marketing" was an international rule, but
    "Marketing" only existed in the national list.
    """
    for _, tipo in STATIC_TIPO_GASTO_NAC:
        assert tipo in opciones("NACIONAL"), f"NAC: {tipo!r} no está en la lista"
    for _, tipo in STATIC_TIPO_GASTO_INTL:
        assert tipo in opciones("INTERNACIONAL"), f"INTL: {tipo!r} no está en la lista"


# ---------------------------------------------------------------- cuotas
# An installment purchase bills only the monthly cuota in the current period.
# Reading the "total a pagar" column instead overstates the month of purchase
# and then double-counts as the later cuotas arrive.
def test_compra_en_cuotas_usa_el_valor_de_la_cuota():
    line = (
        "SANTIAGO 11/08/20 130810280075 PC FACTORY INTERNET TASA INT. 0,00% "
        "$ 670.080 $ 670.080 01/03 $ 223.360"
    )
    m = LINE_RE.search(line)
    assert m is not None
    assert m.group("cuota") == "01/03"
    assert normalizar_monto_clp(m.group("m_cuota")) == 223_360
    assert normalizar_monto_clp(m.group("m1")) == 670_080


def test_pago_unico_cuota_01_01_no_cambia_el_monto():
    line = (
        "SANTIAGO 08/01/26 090110788051 COMUNIDADFELIZ SANTIAGO "
        "$ 36.416 $ 36.416 01/01 $ 36.416"
    )
    m = LINE_RE.search(line)
    assert normalizar_monto_clp(m.group("m_cuota")) == normalizar_monto_clp(m.group("m2"))


def test_linea_sin_columna_de_cuota_sigue_matcheando():
    line = "17/08/20 A Y E SANTIAGO $ 82.650 $ 82.650"
    m = LINE_RE.search(line)
    assert m is not None
    assert m.group("m_cuota") is None
    assert normalizar_monto_clp(m.group("m2")) == 82_650


def test_cuota_negativa_de_un_pago_se_lee_completa():
    line = "08/01/26 080100343370 PAGO PAC EN PESOS $ -770.470 $ -770.470 01/01 $ -770.470"
    m = LINE_RE.search(line)
    assert normalizar_monto_clp(m.group("m_cuota")) == -770_470


# ------------------------------------------------- cartolas unidas en un PDF
# A scanned archive merges dozens of statements into one file. Parsing that as
# a single statement attributes two years of transactions to the first header's
# month and re-imports months already in the database — the bug that produced
# 611 duplicated rows.
def _pagina(origen: str, ult4: str, fecha: str) -> str:
    return (
        f"ESTADO DE CUENTA {origen} DE TARJETA DE CREDITO\n"
        f"NOMBRE DEL TITULAR RAFAEL P. DIAZ\n"
        f"N° DE TARJETA DE CRÉDITO XXXXXXXXXXXX{ult4}\n"
        f"FECHA ESTADO DE CUENTA {fecha}"
    )


def test_parse_nacional_solo_ve_las_paginas_que_recibe():
    pages = [_pagina("NACIONAL", "4364", "24/12/2024"),
             "SANTIAGO 05/12/24 061210673806 FLOW E-CERTCHILE $ 29.738 $ 29.738"]
    rows, meta = parse_nacional(pages, "x.pdf")
    assert meta["FECHA_ESTADO"] == "24-12-2024"
    assert [r["DESCRIPCION"] for r in rows] == ["FLOW E-CERTCHILE"]


def test_cada_cartola_queda_con_su_propia_fecha_de_estado():
    """Two statements in one file must not collapse into the first one's month."""
    pages_a = [_pagina("NACIONAL", "4364", "24/12/2024"),
               "SANTIAGO 05/12/24 061210673806 FLOW E-CERTCHILE $ 29.738 $ 29.738"]
    pages_b = [_pagina("NACIONAL", "4364", "22/11/2024"),
               "SANTIAGO 04/11/24 051111935564 ISAPRE ESENCIAL $ 272.348 $ 272.348"]

    _, meta_a = parse_nacional(pages_a, "x.pdf")
    _, meta_b = parse_nacional(pages_b, "x.pdf")
    assert meta_a["ARCHIVO_ORIGEN"] != meta_b["ARCHIVO_ORIGEN"]


def test_la_cuota_se_guarda_para_distinguir_mensualidades():
    """Without it, the 12 installments of one purchase look like 12 duplicates."""
    pages = [
        _pagina("NACIONAL", "4364", "27/12/2022"),
        "SANTIAGO 18/07/22 161210875225 MP *MERCADO LIB TASA INT. 0,00% "
        "$ 419.000 $ 419.000 06/12 $ 34.916",
    ]
    rows, _ = parse_nacional(pages, "x.pdf")
    assert rows[0]["CUOTA"] == "06/12"
    assert rows[0]["MONTO_TOTAL"] == 34_916


def test_linea_sin_cuota_guarda_cuota_vacia():
    pages = [_pagina("NACIONAL", "4364", "24/12/2024"),
             "SANTIAGO 05/12/24 061210673806 FLOW E-CERTCHILE $ 29.738 $ 29.738"]
    rows, _ = parse_nacional(pages, "x.pdf")
    assert rows[0]["CUOTA"] == ""
