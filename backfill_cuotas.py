"""Backfill the CUOTA column on statements imported before it existed.

Rows are matched on (archivo_origen, fecha_operacion, descripcion, monto_total).
Where a statement bills several identical lines, the cuota values are assigned
in document order, which is the order they were inserted.
"""
from __future__ import annotations

import sys
import tomllib
from collections import defaultdict
from pathlib import Path

from data.common import dividir_en_cartolas
from data.database import init_pool, pooled_conn
from data.extractor_nacional import parse_nacional

ARCHIVO = Path(
    "~/Library/CloudStorage/GoogleDrive-rafadiazm77@gmail.com/"
    "My Drive/Vitros-BCI/BCI"
).expanduser()

EXTRA = [Path(p) for p in sys.argv[1:]]


def main() -> int:
    with open(".streamlit/secrets.toml", "rb") as fh:
        secrets = tomllib.load(fh)
    pool = init_pool(secrets.get("supabase_db_url") or secrets["SUPABASE_DB_URL"])

    pdfs = EXTRA + sorted(ARCHIVO.rglob("*.pdf"))
    print(f"{len(pdfs)} PDFs a revisar", flush=True)

    # (archivo_origen, fecha, desc, monto) -> [cuota, ...] in document order
    cuotas: dict[tuple, list[str]] = defaultdict(list)
    leidos = fallidos = 0

    for i, path in enumerate(pdfs, 1):
        try:
            raw = path.read_bytes()
            for tipo, pages in dividir_en_cartolas(raw):
                if tipo != "NACIONAL":
                    continue
                rows, meta = parse_nacional(pages, path.name)
                if not meta.get("ARCHIVO_ORIGEN"):
                    continue
                for r in rows:
                    if not r.get("CUOTA"):
                        continue
                    key = (
                        r["ARCHIVO_ORIGEN"], r["FECHA_OPERACION"],
                        r["DESCRIPCION"], r["MONTO_TOTAL"],
                    )
                    cuotas[key].append(r["CUOTA"])
            leidos += 1
        except Exception as e:
            fallidos += 1
            print(f"  ! {path.name}: {e}", flush=True)
        if i % 50 == 0:
            print(f"  {i}/{len(pdfs)} — {len(cuotas)} lineas con cuota", flush=True)

    print(f"Leidos {leidos}, fallidos {fallidos}, {len(cuotas)} lineas con cuota", flush=True)

    actualizados = 0
    with pooled_conn(pool) as conn:
        with conn.cursor() as cur:
            for (archivo, fecha, desc, monto), vals in cuotas.items():
                cur.execute(
                    """SELECT id FROM transacciones
                       WHERE archivo_origen=%s AND fecha_operacion=%s
                         AND descripcion=%s AND monto_total=%s
                         AND (cuota IS NULL OR cuota='')
                       ORDER BY id""",
                    (archivo, fecha, desc, monto),
                )
                ids = [r[0] for r in cur.fetchall()]
                for rid, cuota in zip(ids, vals):
                    cur.execute(
                        "UPDATE transacciones SET cuota=%s WHERE id=%s", (cuota, rid)
                    )
                    actualizados += 1
        conn.commit()

    print(f"CUOTA escrita en {actualizados} filas", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
