"""Dry-run validator: parse a folder of BCI statement PDFs without writing anything.

    python validar_cartolas.py ~/Downloads/Cartolas --csv reporte.csv

Answers three questions before a bulk load:
  1. Does every PDF parse, and is it routed to the right extractor?
  2. Did the parser DROP any transaction lines? (format drift across 2020-2026)
  3. Would any statement collide with, or duplicate, what is already loaded?

Writes nothing to the database. The --check-db flag opens a read-only
connection purely to run the same duplicate test the app performs on upload.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from pathlib import Path

import pdfplumber
from pdfminer.pdfdocument import PDFPasswordIncorrect

from data.common import dividir_en_cartolas, flatten
from data.extractor_internacional import (
    DATE_RE as INTL_DATE_RE,
    _norm,
    _parse_transaction_line,
    _to_float,
    parse_internacional,
)
from data.extractor_nacional import (
    LINE_RE as NAC_LINE_RE,
    SKIP_PREFIXES,
    normalizar_monto_clp,
    parse_nacional,
)

NAC_DATE_RE = re.compile(r"\b\d{2}/\d{2}/\d{2}\b")

# A statement balances by the bank's own arithmetic:
#
#     saldo anterior  +  sum(every transaction row)  ==  DEUDA TOTAL
#
# Inferring this from the rows alone does not work — payments, refunds and
# carried credit balances each break a rows-only rule. Reading the prior balance
# out of the summary block handles all three without special cases.
SALDO_ANT_NAC = re.compile(
    r"(?i)MONTO FACTURADO A PAGAR\s*\(PERIODO ANTERIOR\)\s*\(A\)\s*\$\s*(-?[\d.]+)"
)
SALDO_ANT_INTL = re.compile(
    r"(?i)SALDO ANTERIOR FACTURADO\s*US\$\s*(-?[\d.,]+)"
)


def parse_rango(spec: str, minimo: int, maximo: int, etiqueta: str) -> set[int]:
    """Expand "2021", "2021,2023", "2021-2024", "-2022" or "2024-" into a set.

    Open ends fall back to the caller's bounds, so "--anio -2022" means
    everything up to and including 2022.
    """
    valores: set[int] = set()
    for parte in spec.split(","):
        parte = parte.strip()
        if not parte:
            continue
        if "-" in parte.lstrip("-") or parte.startswith("-"):
            izq, _, der = parte.partition("-")
            try:
                desde = int(izq) if izq.strip() else minimo
                hasta = int(der) if der.strip() else maximo
            except ValueError:
                raise ValueError(f"{etiqueta} inválido: {parte!r}") from None
            if desde > hasta:
                desde, hasta = hasta, desde
            valores.update(range(desde, hasta + 1))
        else:
            try:
                valores.add(int(parte))
            except ValueError:
                raise ValueError(f"{etiqueta} inválido: {parte!r}") from None

    fuera = sorted(v for v in valores if not minimo <= v <= maximo)
    if fuera:
        raise ValueError(
            f"{etiqueta} fuera de rango ({minimo}-{maximo}): "
            + ", ".join(str(v) for v in fuera)
        )
    if not valores:
        raise ValueError(f"{etiqueta} vacío")
    return valores


def parse_aa_mm(spec: str, etiqueta: str) -> tuple[int, int]:
    """'2021-03' or '2021/03' or '2021' (-> month 1) into (year, month)."""
    txt = spec.strip().replace("/", "-")
    partes = [p for p in txt.split("-") if p]
    try:
        anio = int(partes[0])
        mes = int(partes[1]) if len(partes) > 1 else 1
    except (ValueError, IndexError):
        raise ValueError(f"{etiqueta} inválido: {spec!r} (usa AAAA-MM)") from None
    if not 1 <= mes <= 12:
        raise ValueError(f"{etiqueta}: mes fuera de rango en {spec!r}")
    return anio, mes


def fecha_estado_partes(fecha: str) -> tuple[int, int] | None:
    """(year, month) out of a DD-MM-YYYY statement date, or None if unusable."""
    partes = (fecha or "").split("-")
    if len(partes) != 3:
        return None
    try:
        return int(partes[2]), int(partes[1])
    except ValueError:
        return None


def en_periodo(
    fecha: str,
    anios: set[int] | None,
    meses: set[int] | None,
    desde: tuple[int, int] | None,
    hasta: tuple[int, int] | None,
) -> bool:
    """Does this statement date pass every filter that was actually given?"""
    if anios is None and meses is None and desde is None and hasta is None:
        return True
    partes = fecha_estado_partes(fecha)
    if partes is None:
        # A statement whose date could not be read cannot be placed in a period;
        # excluding it keeps a filtered run from quietly pulling in strays.
        return False
    anio, mes = partes
    if anios is not None and anio not in anios:
        return False
    if meses is not None and mes not in meses:
        return False
    if desde is not None and (anio, mes) < desde:
        return False
    if hasta is not None and (anio, mes) > hasta:
        return False
    return True


def agregar_filtros_periodo(ap) -> None:
    """Shared --anio/--mes/--desde/--hasta flags."""
    g = ap.add_argument_group("filtros por período (sobre FECHA ESTADO DE CUENTA)")
    g.add_argument("--anio", help="2021 | 2021,2023 | 2021-2024 | -2022 | 2024-")
    g.add_argument("--mes", help="3 | 1,6,12 | 1-6")
    g.add_argument("--desde", help="límite inferior AAAA-MM (ej: 2021-03)")
    g.add_argument("--hasta", help="límite superior AAAA-MM (ej: 2022-06)")


def resolver_filtros(args):
    """Turn the raw flags into (anios, meses, desde, hasta); exits on bad input."""
    try:
        anios = parse_rango(args.anio, 2000, 2100, "--anio") if args.anio else None
        meses = parse_rango(args.mes, 1, 12, "--mes") if args.mes else None
        desde = parse_aa_mm(args.desde, "--desde") if args.desde else None
        hasta = parse_aa_mm(args.hasta, "--hasta") if args.hasta else None
    except ValueError as e:
        sys.exit(f"Error en los filtros: {e}")
    if desde and hasta and desde > hasta:
        sys.exit("Error en los filtros: --desde es posterior a --hasta")
    return anios, meses, desde, hasta


def describir_filtros(anios, meses, desde, hasta) -> str:
    if not any((anios, meses, desde, hasta)):
        return "todo el archivo"
    partes = []
    if anios:
        partes.append("años " + _compactar(sorted(anios)))
    if meses:
        partes.append("meses " + _compactar(sorted(meses)))
    if desde:
        partes.append(f"desde {desde[0]}-{desde[1]:02d}")
    if hasta:
        partes.append(f"hasta {hasta[0]}-{hasta[1]:02d}")
    return "; ".join(partes)


def _compactar(valores: list[int]) -> str:
    """[2020,2021,2022,2024] -> '2020-2022, 2024'"""
    if not valores:
        return "-"
    tramos, inicio, previo = [], valores[0], valores[0]
    for v in valores[1:] + [None]:
        if v is not None and v == previo + 1:
            previo = v
            continue
        tramos.append(str(inicio) if inicio == previo else f"{inicio}-{previo}")
        if v is not None:
            inicio = previo = v
    return ", ".join(tramos)


def _saldo_anterior(pdf_bytes_or_pages, intl: bool):
    """Prior-period balance carried into this statement, or None if absent.

    Accepts the pages of one statement; passing whole PDF bytes reads the first
    statement in the file, which is wrong for a merged archive.
    """
    try:
        if isinstance(pdf_bytes_or_pages, (bytes, bytearray)):
            pages = _page_texts(bytes(pdf_bytes_or_pages))
        else:
            pages = list(pdf_bytes_or_pages)
        flat = flatten("\n".join(pages))
    except Exception:
        return None
    m = (SALDO_ANT_INTL if intl else SALDO_ANT_NAC).search(flat)
    if not m:
        return None
    try:
        return _to_float(m.group(1)) if intl else normalizar_monto_clp(m.group(1))
    except Exception:
        return None


def _page_texts(pdf_bytes: bytes) -> list[str]:
    import io

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return [(p.extract_text() or "") for p in pdf.pages]


def _dropped_nacional(pages: list[str]) -> list[str]:
    """Lines that look like CLP transactions but LINE_RE refused to match."""
    dropped = []
    for text in pages:
        for raw in text.splitlines():
            line = " ".join(raw.split())
            if not line or line.upper().startswith(SKIP_PREFIXES):
                continue
            looks_like_tx = NAC_DATE_RE.search(line) and "$" in line
            if looks_like_tx and not NAC_LINE_RE.search(line):
                dropped.append(line)
    return dropped


def _dropped_internacional(pages: list[str]) -> list[str]:
    """Lines inside the transaction sections that the USD parser returned None for."""
    dropped = []
    in_tx = in_com = False
    for text in pages:
        for raw in text.splitlines():
            line = " ".join(raw.split())
            if not line:
                continue
            u = _norm(line)
            if "2. INFORMACION DE TRANSACCIONES" in u:
                in_tx, in_com = True, False
                continue
            if "COMISIONES, OTROS CARGOS Y ABONOS" in u:
                in_com, in_tx = True, False
                continue
            if u.startswith("TOTAL TARJETA"):
                in_tx = False
                continue
            if u.startswith(
                ("NUMERO", "FECHA", "DESCRIPCION", "CIUDAD", "PAIS",
                 "MONTO", "TOTAL DE PAGOS", "TOTAL DE COMPRAS")
            ):
                continue
            if not (in_tx or in_com) or not INTL_DATE_RE.search(line):
                continue
            if _parse_transaction_line(line, "x", None, None) is None:
                dropped.append(line)
    return dropped


def validar(folder: Path, check_db: bool, filtros=None) -> list[dict]:
    anios, meses, desde, hasta = filtros or (None, None, None, None)
    dup_check = None
    if check_db:
        import tomllib

        from data.database import estado_ya_procesado, init_db

        secrets = tomllib.load(open(".streamlit/secrets.toml", "rb"))
        conn = init_db(secrets["supabase_db_url"])
        dup_check = lambda meta: estado_ya_procesado(conn, meta)  # noqa: E731

    pdfs = sorted(folder.rglob("*.pdf"))
    if not pdfs:
        sys.exit(f"No se encontraron PDFs en {folder}")

    results = []
    for path in pdfs:
        row = {"archivo": str(path.relative_to(folder)), "estado": "", "tipo": "",
               "tarjeta": "", "fecha": "", "clave": "", "filas": 0,
               "saldo_anterior": "", "suma_filas": "", "deuda_total": "",
               "descuadre": "", "lineas_perdidas": 0, "detalle": ""}
        try:
            raw = path.read_bytes()
        except Exception as e:
            row["estado"] = "ERROR_LECTURA"
            row["detalle"] = str(e)
            results.append(row)
            continue

        try:
            cartolas = dividir_en_cartolas(raw)
        except PDFPasswordIncorrect:
            row["estado"] = "PROTEGIDO_CON_CLAVE"
            results.append(row)
            continue
        except Exception as e:
            row["estado"] = "ILEGIBLE"
            row["detalle"] = f"{type(e).__name__}: {e}"
            results.append(row)
            continue

        if not cartolas:
            try:
                head = flatten((_page_texts(raw) or [""])[0])[:120]
            except Exception as e:
                row["estado"] = "ILEGIBLE"
                row["detalle"] = f"{type(e).__name__}: {e}"
                results.append(row)
                continue
            row["estado"] = "NO_ES_CARTOLA_BCI"
            row["detalle"] = f"encabezado: {head}"
            results.append(row)
            continue

        # One file can hold many statements; report each on its own line.
        for idx, (tipo, pages) in enumerate(cartolas):
            sub = dict(row)
            if len(cartolas) > 1:
                sub["archivo"] = f"{row['archivo']} [{idx + 1}/{len(cartolas)}]"
            _validar_una(
                sub, tipo, pages, path.name, anios, meses, desde, hasta,
                results, dup_check,
            )

    return _marcar_claves_duplicadas(results)


def _validar_una(
    row, tipo, pages, filename, anios, meses, desde, hasta, results, dup_check
) -> None:
    row["tipo"] = tipo
    parse = parse_internacional if tipo == "INTERNACIONAL" else parse_nacional
    try:
        rows, meta = parse(pages, filename)
    except Exception as e:
        row["estado"] = "ERROR_PARSEO"
        row["detalle"] = f"{type(e).__name__}: {e}"
        results.append(row)
        return

    try:
        dropped = (
            _dropped_internacional(pages)
            if tipo == "INTERNACIONAL"
            else _dropped_nacional(pages)
        )
    except Exception:
        dropped = []
    if not en_periodo(meta.get("FECHA_ESTADO") or "", anios, meses, desde, hasta):
        return

    intl = tipo == "INTERNACIONAL"
    monto_col = "MONTO_OPERACION" if intl else "MONTO_TOTAL"
    suma = sum((r[monto_col] or 0) for r in rows)
    saldo_ant = _saldo_anterior(pages, intl)

    deuda = meta.get("DEUDA_TOTAL")
    descuadre = None
    if deuda is not None and saldo_ant is not None:
        descuadre = round(float(saldo_ant) + float(suma) - float(deuda), 2)

    row.update({
        "tarjeta": meta.get("TARJETA_ULT4") or "",
        "fecha": meta.get("FECHA_ESTADO") or "",
        "clave": meta.get("ARCHIVO_ORIGEN") or "",
        "filas": len(rows),
        "saldo_anterior": saldo_ant,
        "suma_filas": round(float(suma), 2),
        "deuda_total": deuda,
        "descuadre": descuadre,
        "lineas_perdidas": len(dropped),
        "detalle": " || ".join(dropped[:3]),
    })

    problemas = []
    if not meta.get("TARJETA_ULT4"):
        problemas.append("SIN_TARJETA")
    if not meta.get("FECHA_ESTADO"):
        problemas.append("SIN_FECHA")
    if not rows:
        problemas.append("SIN_FILAS")
    if dropped:
        problemas.append("LINEAS_PERDIDAS")
    if deuda is None:
        problemas.append("SIN_DEUDA_TOTAL")
    elif saldo_ant is None:
        problemas.append("SIN_SALDO_ANTERIOR")
    elif abs(descuadre) >= 0.5:
        # The bank's own arithmetic does not close: rows were missed,
        # double-counted, or valued wrong.
        problemas.append(f"DESCUADRE({descuadre:,.0f})")
    if dup_check and dup_check(meta):
        problemas.append("YA_CARGADA")

    row["estado"] = " + ".join(problemas) if problemas else "OK"
    results.append(row)


def _marcar_claves_duplicadas(results: list[dict]) -> list[dict]:
    # A key collision means two different PDFs would fight for one row.
    claves = Counter(r["clave"] for r in results if r["clave"])
    for r in results:
        if r["clave"] and claves[r["clave"]] > 1:
            r["estado"] = (r["estado"] + " + CLAVE_DUPLICADA").lstrip(" +")

    return results


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("carpeta", type=Path)
    agregar_filtros_periodo(ap)
    ap.add_argument("--csv", type=Path, help="guardar el reporte completo")
    ap.add_argument("--check-db", action="store_true",
                    help="comparar contra la base (solo lectura)")
    args = ap.parse_args()

    filtros = resolver_filtros(args)
    results = validar(args.carpeta, args.check_db, filtros)
    if not results:
        sys.exit(f"Ningún archivo coincide con: {describir_filtros(*filtros)}")

    print(f"\n{len(results)} archivo(s) analizados — {describir_filtros(*filtros)}\n")
    ok = [r for r in results if r["estado"] == "OK"]
    prob = [r for r in results if r["estado"] != "OK"]

    # Group by problem class: a DESCUADRE carries its amount, so bucket on the
    # label alone or every mismatch becomes its own category.
    por_estado: dict[str, list[dict]] = {}
    for r in prob:
        label = re.sub(r"\(.*?\)", "", r["estado"]).strip()
        por_estado.setdefault(label, []).append(r)

    for label, items in sorted(por_estado.items(), key=lambda kv: -len(kv[1])):
        print(f"  {label}  ({len(items)})")
        for r in items[:8]:
            extra = f"  {r['estado']}" if "(" in r["estado"] else ""
            print(f"      {r['archivo'][:66]}{extra}")
        if len(items) > 8:
            print(f"      … y {len(items) - 8} más (ver CSV)")

    print(f"\n  OK: {len(ok)}   Con problemas: {len(prob)}")

    por_anio = Counter(r["fecha"][-4:] for r in results if r["fecha"])
    if por_anio:
        print("\n  Cartolas por año: " + ", ".join(
            f"{a}={n}" for a, n in sorted(por_anio.items())
        ))
    por_tarjeta = Counter(r["tarjeta"] for r in results if r["tarjeta"])
    if por_tarjeta:
        print("  Por tarjeta:      " + ", ".join(
            f"••{t}={n}" for t, n in sorted(por_tarjeta.items())
        ))

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(results[0].keys()))
            w.writeheader()
            w.writerows(results)
        print(f"\n  Reporte: {args.csv}")


if __name__ == "__main__":
    main()
