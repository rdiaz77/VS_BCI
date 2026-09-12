"""One-shot, idempotent schema migration for the BCI statements DB.

Run:  python migrate.py            (uses .streamlit/secrets.toml)
      python migrate.py --dry-run  (show what would change)

Safe to run repeatedly — every step is guarded.

Why the odd `::text::numeric` casts: PostgreSQL's built-in real->numeric cast
formats via FLT_DIG (6 significant digits), so a plain
`ALTER COLUMN ... TYPE NUMERIC` silently rewrites 1003291 as 1003290.
Going through text uses float4's shortest-round-trip representation, which is
exact. Verified on PG 18.6 before writing this.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

import psycopg2

MONEY_COLS = ["MONTO_ORIGEN", "MONTO_OPERACION", "MONTO_TOTAL", "MONTO_CLP"]


def _db_url() -> str:
    secrets = Path(".streamlit/secrets.toml")
    if not secrets.exists():
        sys.exit("No .streamlit/secrets.toml found.")
    m = re.search(r'supabase_db_url\s*=\s*"(.+?)"', secrets.read_text())
    if not m:
        sys.exit("supabase_db_url not found in secrets.toml")
    return m.group(1)


def connect(url: str):
    u = urlparse(url.replace("#", "%23"))
    return psycopg2.connect(
        host=u.hostname,
        port=u.port,
        dbname=u.path.lstrip("/"),
        user=u.username,
        password=unquote(u.password or ""),
        sslmode="require",
    )


def column_type(cur, table: str, col: str) -> str | None:
    cur.execute(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name = %s AND column_name = %s",
        (table, col.lower()),
    )
    row = cur.fetchone()
    return row[0] if row else None


def migrate(conn, dry_run: bool = False, drop_dead_table: bool = False) -> None:
    steps: list[str] = []

    with conn.cursor() as cur:
        # ── 1. Money: REAL -> NUMERIC, losslessly via text ───────────────
        for col in MONEY_COLS:
            t = column_type(cur, "transacciones", col)
            if t == "real":
                steps.append(f"transacciones.{col}: real -> numeric(18,4)")
                if not dry_run:
                    cur.execute(
                        f"ALTER TABLE transacciones "
                        f"ALTER COLUMN {col} TYPE NUMERIC(18,4) "
                        f"USING {col}::text::numeric"
                    )
        if column_type(cur, "estados_cuenta", "DEUDA_TOTAL") == "real":
            steps.append("estados_cuenta.DEUDA_TOTAL: real -> numeric(18,4)")
            if not dry_run:
                cur.execute(
                    "ALTER TABLE estados_cuenta "
                    "ALTER COLUMN DEUDA_TOTAL TYPE NUMERIC(18,4) "
                    "USING DEUDA_TOTAL::text::numeric"
                )
        if column_type(cur, "estados_cuenta", "TASA_CAMBIO") == "real":
            steps.append("estados_cuenta.TASA_CAMBIO: real -> numeric(12,4)")
            if not dry_run:
                cur.execute(
                    "ALTER TABLE estados_cuenta "
                    "ALTER COLUMN TASA_CAMBIO TYPE NUMERIC(12,4) "
                    "USING TASA_CAMBIO::text::numeric"
                )

        # ── 2. Card identity (last 4 digits) ─────────────────────────────
        for table in ("transacciones", "estados_cuenta"):
            if column_type(cur, table, "TARJETA_ULT4") is None:
                steps.append(f"{table}.TARJETA_ULT4: add TEXT")
                if not dry_run:
                    cur.execute(
                        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS TARJETA_ULT4 TEXT"
                    )
        if column_type(cur, "estados_cuenta", "TITULAR_COMPLETO") is None:
            steps.append("estados_cuenta.TITULAR_COMPLETO: add TEXT")
            if not dry_run:
                cur.execute(
                    "ALTER TABLE estados_cuenta "
                    "ADD COLUMN IF NOT EXISTS TITULAR_COMPLETO TEXT"
                )

        # ── 3. Real DATE columns (text dates stay for display) ───────────
        if column_type(cur, "transacciones", "FECHA_OPERACION_D") is None:
            steps.append("transacciones.FECHA_OPERACION_D: add DATE + backfill")
            if not dry_run:
                cur.execute(
                    "ALTER TABLE transacciones "
                    "ADD COLUMN IF NOT EXISTS FECHA_OPERACION_D DATE"
                )
                cur.execute(
                    "UPDATE transacciones SET FECHA_OPERACION_D = "
                    "  to_date(FECHA_OPERACION, 'MM/DD/YY') "
                    "WHERE FECHA_OPERACION_D IS NULL "
                    "  AND FECHA_OPERACION ~ '^[0-9]{2}/[0-9]{2}/[0-9]{2}$'"
                )
        if column_type(cur, "estados_cuenta", "FECHA_ESTADO_D") is None:
            steps.append("estados_cuenta.FECHA_ESTADO_D: add DATE + backfill")
            if not dry_run:
                cur.execute(
                    "ALTER TABLE estados_cuenta "
                    "ADD COLUMN IF NOT EXISTS FECHA_ESTADO_D DATE"
                )
                cur.execute(
                    "UPDATE estados_cuenta SET FECHA_ESTADO_D = "
                    "  to_date(FECHA_ESTADO, 'DD-MM-YYYY') "
                    "WHERE FECHA_ESTADO_D IS NULL "
                    "  AND FECHA_ESTADO ~ '^[0-9]{2}-[0-9]{2}-[0-9]{4}$'"
                )

        # ── 4. Upload audit timestamp (replaces archivos_procesados) ─────
        if column_type(cur, "estados_cuenta", "FECHA_CARGA") is None:
            steps.append("estados_cuenta.FECHA_CARGA: add TIMESTAMPTZ")
            if not dry_run:
                cur.execute(
                    "ALTER TABLE estados_cuenta "
                    "ADD COLUMN IF NOT EXISTS FECHA_CARGA TIMESTAMPTZ DEFAULT NOW()"
                )

        # ── 5. Indexes for the 2020->now archive ─────────────────────────
        for name, ddl in (
            ("idx_tx_fecha_d", "transacciones(FECHA_OPERACION_D)"),
            ("idx_tx_tarjeta", "transacciones(TARJETA_ULT4)"),
            ("idx_tx_origen_kame", "transacciones(ORIGEN, FACT_KAME)"),
            ("idx_ec_fecha_d", "estados_cuenta(FECHA_ESTADO_D)"),
            ("idx_ec_ident", "estados_cuenta(ORIGEN, FECHA_ESTADO, TARJETA_ULT4)"),
        ):
            steps.append(f"index {name}")
            if not dry_run:
                cur.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {ddl}")

        # ── 6. The dead dedup table (opt-in drop) ────────────────────────
        # archivos_procesados stored PDF filenames while ARCHIVO_ORIGEN holds
        # BCI_NAC_<card>_<date>, so its rows never matched anything and nothing
        # reads it any more. Dropping is safe but opt-in — everything above is
        # additive, so the default run cannot lose data.
        cur.execute("SELECT to_regclass('public.archivos_procesados')")
        if cur.fetchone()[0] is not None:
            if drop_dead_table:
                steps.append("DROP TABLE archivos_procesados")
                if not dry_run:
                    cur.execute("DROP TABLE IF EXISTS archivos_procesados")
            else:
                steps.append(
                    "(skipped) archivos_procesados is unused — "
                    "re-run with --drop-dead-table to remove it"
                )

    if dry_run:
        conn.rollback()
    else:
        conn.commit()

    print(("Would apply" if dry_run else "Applied") + f" {len(steps)} step(s):")
    for s in steps:
        print("  -", s)


def verify(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name='transacciones' "
            "  AND (column_name LIKE 'monto%' OR column_name LIKE 'fecha%' "
            "       OR column_name='tarjeta_ult4') ORDER BY column_name"
        )
        print("\ntransacciones:")
        for name, typ in cur.fetchall():
            print(f"  {name:22} {typ}")
        cur.execute(
            "SELECT count(*) FILTER (WHERE fecha_operacion_d IS NULL), count(*) "
            "FROM transacciones"
        )
        null_d, total = cur.fetchone()
        print(f"\n  rows={total}  fecha_operacion_d NULL={null_d}")
        cur.execute("SELECT min(fecha_operacion_d), max(fecha_operacion_d) FROM transacciones")
        print("  date span:", cur.fetchone())


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    drop = "--drop-dead-table" in sys.argv
    conn = connect(_db_url())
    try:
        migrate(conn, dry_run=dry, drop_dead_table=drop)
        if not dry:
            verify(conn)
    finally:
        conn.close()
