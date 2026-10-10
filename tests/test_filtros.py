"""Period filters shared by validar_cartolas.py and cargar_cartolas.py.

These decide which statements a bulk run touches, so an off-by-one here either
silently skips a month or loads one twice.
"""
import pytest

from validar_cartolas import (
    describir_filtros,
    en_periodo,
    fecha_estado_partes,
    parse_aa_mm,
    parse_rango,
)


# ---------------------------------------------------------------- parse_rango
@pytest.mark.parametrize(
    "spec,esperado",
    [
        ("2021", {2021}),
        ("2021,2023", {2021, 2023}),
        ("2021-2024", {2021, 2022, 2023, 2024}),
        ("-2021", {2019, 2020, 2021}),
        ("2025-", {2025, 2026}),
        ("2022-2020", {2020, 2021, 2022}),  # reversed range still works
        (" 2021 , 2023 ", {2021, 2023}),
    ],
)
def test_rango_de_anios(spec, esperado):
    assert parse_rango(spec, 2019, 2026, "--anio") == esperado


def test_rango_de_meses():
    assert parse_rango("1-6", 1, 12, "--mes") == {1, 2, 3, 4, 5, 6}
    assert parse_rango("1,6,12", 1, 12, "--mes") == {1, 6, 12}


@pytest.mark.parametrize("spec", ["abc", "13", "0", ""])
def test_rango_invalido_falla(spec):
    with pytest.raises(ValueError):
        parse_rango(spec, 1, 12, "--mes")


# ---------------------------------------------------------------- parse_aa_mm
@pytest.mark.parametrize(
    "spec,esperado",
    [("2021-03", (2021, 3)), ("2021/03", (2021, 3)), ("2021", (2021, 1))],
)
def test_anio_mes(spec, esperado):
    assert parse_aa_mm(spec, "--desde") == esperado


@pytest.mark.parametrize("spec", ["2021-13", "no-es-fecha", ""])
def test_anio_mes_invalido_falla(spec):
    with pytest.raises(ValueError):
        parse_aa_mm(spec, "--desde")


# ---------------------------------------------------------------- fechas
def test_partes_de_fecha_estado():
    assert fecha_estado_partes("17-08-2020") == (2020, 8)
    assert fecha_estado_partes("") is None
    assert fecha_estado_partes("17-08") is None
    assert fecha_estado_partes("xx-yy-zzzz") is None


# ---------------------------------------------------------------- en_periodo
def test_sin_filtros_pasa_todo():
    assert en_periodo("17-08-2020", None, None, None, None)
    assert en_periodo("", None, None, None, None)


def test_filtra_por_anio_y_por_mes():
    assert en_periodo("17-08-2020", {2020}, None, None, None)
    assert not en_periodo("17-08-2020", {2021}, None, None, None)
    assert en_periodo("17-08-2020", None, {8}, None, None)
    assert not en_periodo("17-08-2020", None, {1, 2}, None, None)


def test_anio_y_mes_se_combinan_con_and():
    assert en_periodo("17-08-2020", {2020}, {8}, None, None)
    assert not en_periodo("17-08-2020", {2020}, {9}, None, None)


def test_rango_desde_hasta_es_inclusivo():
    assert en_periodo("17-08-2020", None, None, (2020, 8), (2020, 8))
    assert not en_periodo("17-08-2020", None, None, (2020, 9), None)
    assert not en_periodo("17-08-2020", None, None, None, (2020, 7))


def test_fecha_ilegible_queda_fuera_si_hay_filtro():
    """A statement we cannot date must not sneak into a filtered run."""
    assert not en_periodo("", {2020}, None, None, None)
    assert not en_periodo("fecha-mala", None, None, (2020, 1), None)


# ---------------------------------------------------------------- descripción
def test_descripcion_compacta_los_tramos():
    assert describir_filtros({2020, 2021, 2022, 2024}, None, None, None) == (
        "años 2020-2022, 2024"
    )
    assert describir_filtros(None, None, None, None) == "todo el archivo"
    assert "desde 2021-03" in describir_filtros(None, None, (2021, 3), None)
