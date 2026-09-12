"""Backfill TARJETA_ULT4 on statements loaded before card tracking existed.

Rows ingested by the old code have no card number, so they fall back to
name-based duplicate detection — which cannot tell two cards apart. This reads
the statement PDFs you still have locally, maps (ORIGEN, FECHA_ESTADO) to a
card, and fills the gap.

    python backfill_tarjetas.py --dry-run
    python backfill_tarjetas.py

Only ever writes rows where TARJETA_ULT4 IS NULL.
"""
from __future__ import annotations

import glob
import os
import sys

from data.common import detectar_tipo_cartola, extraer_identidad
from migrate import _db_url, connect

SEARCH_DIRS = [
    os.environ.get("BCI_PDF_DIR"),
    "Cartolas",
    "Cartolas/2025",
    os.path.expanduser("~/Desktop/Bci"),
]


def scan_pdfs() -> dict[tuple[str, str], tuple[str, str | None]]:
    """{(ORIGEN, FECHA_ESTADO): (TARJETA_ULT4, TITULAR_COMPLETO)} from local PDFs."""
    found: dict[tuple[str, str], tuple[str, str | None]] = {}
    conflicts: set[tuple[str, str]] = set()

    paths = sorted({p for d in SEARCH_DIRS if d and os.path.isdir(d)
                    for p in glob.glob(os.path.join(d, "*.pdf"))})
    for path in paths:
        raw = open(path, "rb").read()
        tipo = detectar_tipo_cartola(raw)
        if not tipo:
            continue
        ident = extraer_identidad(raw_text(raw))
        ult4, fecha = ident["TARJETA_ULT4"], ident["FECHA_ESTADO"]
        if not (ult4 and fecha):
            continue
        key = (tipo, fecha)
        if key in found and found[key][0] != ult4:
            # Two different cards billed the same day — cannot disambiguate
            # a legacy row from the date alone, so leave it alone.
            conflicts.add(key)
        found[key] = (ult4, ident["TITULAR_COMPLETO"])

    for k in conflicts:
        found.pop(k, None)
        print(f"  ! ambiguous, skipping: {k}")
    print(f"  scanned {len(paths)} PDFs -> {len(found)} usable (tipo, fecha) keys")
    return found


def raw_text(pdf_bytes: bytes) -> str:
    import io

    import pdfplumber

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages[:1])


def main(dry_run: bool) -> None:
    mapping = scan_pdfs()
    if not mapping:
        sys.exit("No local statement PDFs found — nothing to backfill.")

    conn = connect(_db_url())
    updated_ec = updated_tx = 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT ORIGEN, FECHA_ESTADO, ARCHIVO_ORIGEN FROM estados_cuenta "
                "WHERE TARJETA_ULT4 IS NULL OR TITULAR_COMPLETO IS NULL"
            )
            pending = cur.fetchall()
            print(f"  {len(pending)} statement(s) missing card and/or full name")

            for origen, fecha, archivo in pending:
                hit = mapping.get((origen, fecha))
                if not hit:
                    print(f"  ? no PDF for {origen} {fecha} ({archivo}) — left as is")
                    continue
                ult4, titular = hit
                print(f"  -> {archivo}: ••{ult4} {titular or ''}")
                if dry_run:
                    continue
                cur.execute(
                    "UPDATE estados_cuenta "
                    "SET TARJETA_ULT4     = COALESCE(TARJETA_ULT4, %s), "
                    "    TITULAR_COMPLETO = COALESCE(TITULAR_COMPLETO, %s) "
                    "WHERE ARCHIVO_ORIGEN = %s",
                    (ult4, titular, archivo),
                )
                updated_ec += cur.rowcount
                cur.execute(
                    "UPDATE transacciones SET TARJETA_ULT4 = %s "
                    "WHERE ARCHIVO_ORIGEN = %s AND TARJETA_ULT4 IS NULL",
                    (ult4, archivo),
                )
                updated_tx += cur.rowcount

        if dry_run:
            conn.rollback()
            print("\nDry run — nothing written.")
        else:
            conn.commit()
            print(f"\nUpdated {updated_ec} statement(s), {updated_tx} transaction(s).")
    finally:
        conn.close()


if __name__ == "__main__":
    main("--dry-run" in sys.argv)
