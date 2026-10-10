"""Bulk-load statement PDFs into PostgreSQL, one year at a time.

    python cargar_cartolas.py <carpeta> --anio 2020            # ensayo, no escribe
    python cargar_cartolas.py <carpeta> --anio 2020 --ejecutar  # carga de verdad

Mirrors what the Streamlit uploader does — same parsers, same duplicate check,
same auto-categorization — but refuses to insert a statement whose own
arithmetic does not close, so a silently mis-parsed statement never reaches the
database. Rows land as FACT_KAME=0 (pendiente); nothing is ever marked as
already-accounted automatically.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from data.common import detectar_tipo_cartola
from data.database import (
    auto_tipo_gasto,
    estado_ya_procesado,
    fetch_tipo_gasto_map,
    ingest_statement,
    init_db,
)
from data.extractor_internacional import leer_cartola_internacional
from data.extractor_nacional import leer_cartola_nacional
from validar_cartolas import (
    _saldo_anterior,
    agregar_filtros_periodo,
    describir_filtros,
    en_periodo,
    resolver_filtros,
)

TOLERANCIA = 0.5


def _conn():
    import tomllib

    secrets = tomllib.load(open(".streamlit/secrets.toml", "rb"))
    url = secrets.get("supabase_db_url") or secrets.get("SUPABASE_DB_URL")
    if not url:
        sys.exit("Falta supabase_db_url en .streamlit/secrets.toml")
    return init_db(str(url))


def cargar(carpeta: Path, filtros, ejecutar: bool) -> None:
    anios, meses, desde, hasta = filtros
    conn = _conn()
    historic = fetch_tipo_gasto_map(conn)

    pdfs = sorted(carpeta.rglob("*.pdf"))
    if not pdfs:
        sys.exit(f"No hay PDFs en {carpeta}")

    stats = Counter()
    cargadas: list[tuple[str, str, str, int]] = []
    rechazadas: list[tuple[str, str]] = []
    vistos: set[str] = set()

    for path in pdfs:
        rel = str(path.relative_to(carpeta))
        try:
            raw = path.read_bytes()
            tipo = detectar_tipo_cartola(raw)
        except Exception as e:
            stats["ilegible"] += 1
            rechazadas.append((rel, f"ilegible: {type(e).__name__}"))
            continue

        if tipo is None:
            stats["no_es_cartola"] += 1
            continue

        extractor = (
            leer_cartola_internacional if tipo == "INTERNACIONAL" else leer_cartola_nacional
        )
        try:
            rows, meta = extractor(raw, filename=path.name)
        except Exception as e:
            stats["error_parseo"] += 1
            rechazadas.append((rel, f"error de parseo: {type(e).__name__}"))
            continue

        fecha = meta.get("FECHA_ESTADO") or ""
        if not en_periodo(fecha, anios, meses, desde, hasta):
            stats["fuera_de_periodo"] += 1
            continue

        if not rows:
            stats["sin_filas"] += 1
            rechazadas.append((rel, "sin filas"))
            continue

        # ---- refuse anything whose own arithmetic does not close ----
        intl = tipo == "INTERNACIONAL"
        deuda = meta.get("DEUDA_TOTAL")
        saldo = _saldo_anterior(raw, intl)
        if deuda is None or saldo is None:
            stats["sin_totales"] += 1
            rechazadas.append((rel, "no se pudo leer deuda/saldo anterior"))
            continue
        col = "MONTO_OPERACION" if intl else "MONTO_TOTAL"
        suma = sum((r[col] or 0) for r in rows)
        descuadre = round(float(saldo) + float(suma) - float(deuda), 2)
        if abs(descuadre) >= TOLERANCIA:
            stats["descuadre"] += 1
            rechazadas.append((rel, f"descuadre {descuadre:,.0f}"))
            continue

        # ---- duplicates: against the DB, and within this same batch ----
        clave = meta.get("ARCHIVO_ORIGEN") or ""
        if clave in vistos:
            stats["duplicada_en_lote"] += 1
            continue
        dup = estado_ya_procesado(conn, meta)
        if dup:
            stats["ya_cargada"] += 1
            continue
        vistos.add(clave)

        for r in rows:
            if not r.get("TIPO_GASTO"):
                r["TIPO_GASTO"] = auto_tipo_gasto(
                    r.get("DESCRIPCION", ""), historic, origen=r.get("ORIGEN", "")
                )

        if not ejecutar:
            stats["se_cargaria"] += 1
            cargadas.append((rel, tipo, fecha, len(rows)))
            vistos.add(clave)
            continue

        try:
            n = ingest_statement(conn, rows, meta)
        except Exception as e:
            stats["error_insert"] += 1
            rechazadas.append((rel, f"error al insertar: {e}"))
            continue
        if n == 0:
            stats["ya_cargada"] += 1
        else:
            stats["cargadas"] += 1
            cargadas.append((rel, tipo, fecha, n))
            # A later statement can now inherit categories from this one.
            historic = fetch_tipo_gasto_map(conn)

    modo = "CARGA REAL" if ejecutar else "ENSAYO (no se escribió nada)"
    print(f"\n=== {modo} — {describir_filtros(anios, meses, desde, hasta)} ===\n")
    for rel, tipo, fecha, n in cargadas:
        print(f"  + {fecha}  {tipo[:4]}  {n:>3} tx   {rel[:62]}")
    if rechazadas:
        print("\n  RECHAZADAS:")
        for rel, motivo in rechazadas:
            print(f"  - {rel[:62]:62s} {motivo}")
    print("\n  " + "  ".join(f"{k}={v}" for k, v in sorted(stats.items())))
    total_tx = sum(n for _, _, _, n in cargadas)
    print(f"  cartolas={len(cargadas)}  transacciones={total_tx}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("carpeta", type=Path)
    agregar_filtros_periodo(ap)
    ap.add_argument(
        "--ejecutar",
        action="store_true",
        help="escribir en la base; sin esta bandera solo simula",
    )
    args = ap.parse_args()
    cargar(args.carpeta, resolver_filtros(args), args.ejecutar)


if __name__ == "__main__":
    main()
