import logging
from typing import Any, Dict, Iterable, List, Optional, Tuple

import psycopg2
import psycopg2.extras

# ============================================================
# Unified PostgreSQL layer (Supabase)
#   transacciones       — NACIONAL (CLP) and INTERNACIONAL (USD) rows
#   estados_cuenta      — one row per statement (traspaso reconciliation)
#   archivos_procesados — upload dedup
# ============================================================

_log = logging.getLogger(__name__)

TRANSACCIONES_COLS = [
    "ORIGEN",            # 'NACIONAL' | 'INTERNACIONAL'
    "TITULAR_NOMBRE",
    "FECHA_OPERACION",   # MM/DD/YY
    "DESCRIPCION",
    "CIUDAD",
    "PAIS",
    "REF_INTERNACIONAL",
    "MONTO_ORIGEN",      # intl: amount in origin currency
    "MONTO_OPERACION",   # USD (intl) or CLP (nacional)
    "MONTO_TOTAL",
    "MONTO_CLP",         # intl only: USD converted at bank traspaso rate
    "MONEDA",            # 'USD' | 'CLP'
    "TIPO_GASTO",
    "CONCILIADO",        # 0/1
    "FACT_KAME",         # 0/1 — entered in Kame
    "TRASPASADO",        # 0/1 — intl statement transferred to national
    "ARCHIVO_ORIGEN",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sort_expr(col: str) -> str:
    """Reformat MM/DD/YY text column to YYMMDD for correct chronological sort."""
    return (
        f"substring({col},7,2)||substring({col},1,2)||substring({col},4,2)"
    )


# ---------------------------------------------------------------------------
# Schema init
# ---------------------------------------------------------------------------

def init_db(db_url: str):
    """Connect to Supabase/PostgreSQL, create tables if needed, return connection."""
    from urllib.parse import urlparse as _up, unquote as _uq
    _u = _up(db_url.replace("#", "%23"))
    conn = psycopg2.connect(host=_u.hostname, port=_u.port, dbname=_u.path.lstrip("/"), user=_u.username, password=_uq(_u.password), sslmode="require")
    conn.autocommit = False

    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS transacciones (
                id              SERIAL PRIMARY KEY,
                ORIGEN          TEXT NOT NULL,
                TITULAR_NOMBRE  TEXT,
                FECHA_OPERACION TEXT,
                DESCRIPCION     TEXT,
                CIUDAD          TEXT,
                PAIS            TEXT,
                REF_INTERNACIONAL TEXT,
                MONTO_ORIGEN    REAL,
                MONTO_OPERACION REAL,
                MONTO_TOTAL     REAL,
                MONTO_CLP       REAL,
                MONEDA          TEXT,
                TIPO_GASTO      TEXT,
                CONCILIADO      INTEGER NOT NULL DEFAULT 0,
                FACT_KAME       INTEGER NOT NULL DEFAULT 0,
                TRASPASADO      INTEGER NOT NULL DEFAULT 0,
                ARCHIVO_ORIGEN  TEXT
            );
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_tx_origen     ON transacciones(ORIGEN);"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_tx_fact_kame  ON transacciones(FACT_KAME);"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_tx_archivo    ON transacciones(ARCHIVO_ORIGEN);"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_tx_traspasado ON transacciones(TRASPASADO);"
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS estados_cuenta (
                id              SERIAL PRIMARY KEY,
                ORIGEN          TEXT NOT NULL,
                TITULAR_NOMBRE  TEXT,
                ARCHIVO_ORIGEN  TEXT UNIQUE NOT NULL,
                FECHA_ESTADO    TEXT,
                PERIODO_DESDE   TEXT,
                PERIODO_HASTA   TEXT,
                DEUDA_TOTAL     REAL,
                MONEDA          TEXT,
                TRASPASO_ESTADO TEXT NOT NULL DEFAULT 'PENDIENTE',
                MATCH_RID       INTEGER,
                MATCH_ARCHIVO   TEXT,
                TASA_CAMBIO     REAL
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS archivos_procesados (
                nombre          TEXT PRIMARY KEY,
                fecha_procesado TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )

        # Safe column migrations for existing schemas
        for table, col, decl in (
            ("transacciones",  "MONTO_CLP",   "REAL"),
            ("estados_cuenta", "TASA_CAMBIO", "REAL"),
        ):
            cur.execute(
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {decl};"
            )

    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Processed-file dedup
# ---------------------------------------------------------------------------

def archivo_ya_procesado(conn, filename: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM archivos_procesados WHERE nombre = %s LIMIT 1", (filename,)
        )
        return cur.fetchone() is not None


def registrar_archivo_procesado(conn, filename: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO archivos_procesados(nombre) VALUES (%s) ON CONFLICT DO NOTHING",
            (filename,),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------

def insertar_transacciones(conn, rows: Iterable[Dict[str, Any]]) -> int:
    rows = list(rows)
    if not rows:
        return 0

    col_list = ", ".join(TRANSACCIONES_COLS)
    placeholders = ", ".join(["%s"] * len(TRANSACCIONES_COLS))

    data = [
        (
            r.get("ORIGEN", ""),
            r.get("TITULAR_NOMBRE"),
            r.get("FECHA_OPERACION", ""),
            r.get("DESCRIPCION", ""),
            r.get("CIUDAD", ""),
            r.get("PAIS", ""),
            r.get("REF_INTERNACIONAL", ""),
            r.get("MONTO_ORIGEN"),
            r.get("MONTO_OPERACION"),
            r.get("MONTO_TOTAL"),
            r.get("MONTO_CLP"),
            r.get("MONEDA", ""),
            r.get("TIPO_GASTO", ""),
            int(r.get("CONCILIADO") or 0),
            int(r.get("FACT_KAME") or 0),
            int(r.get("TRASPASADO") or 0),
            r.get("ARCHIVO_ORIGEN", ""),
        )
        for r in rows
    ]

    with conn.cursor() as cur:
        psycopg2.extras.execute_many(
            cur,
            f"INSERT INTO transacciones ({col_list}) VALUES ({placeholders});",
            data,
        )
    conn.commit()
    return len(rows)


def fetch_transacciones(
    conn, origen: Optional[str] = None
) -> Tuple[List[str], List[tuple]]:
    """Return (cols, rows) with id exposed as _RID_. Filter by ORIGEN if given."""
    sort = _sort_expr("FECHA_OPERACION")
    with conn.cursor() as cur:
        if origen:
            cur.execute(
                f"SELECT id AS _RID_, * FROM transacciones WHERE ORIGEN = %s ORDER BY {sort}",
                (origen,),
            )
        else:
            cur.execute(
                f"SELECT id AS _RID_, * FROM transacciones ORDER BY {sort}"
            )
        cols = [d[0] for d in cur.description]
        return cols, cur.fetchall()


def update_clasificacion(conn, updates: List[Dict[str, Any]]) -> None:
    if not updates:
        return
    with conn.cursor() as cur:
        psycopg2.extras.execute_many(
            cur,
            "UPDATE transacciones SET TIPO_GASTO = %s, CONCILIADO = %s WHERE id = %s;",
            [
                (
                    u.get("TIPO_GASTO") or "",
                    int(bool(u.get("CONCILIADO"))),
                    int(u["_RID_"]),
                )
                for u in updates
            ],
        )
    conn.commit()


def marcar_fact_kame(conn, rowids: List[int]) -> None:
    if not rowids:
        return
    with conn.cursor() as cur:
        psycopg2.extras.execute_many(
            cur,
            "UPDATE transacciones SET FACT_KAME = 1 WHERE id = %s;",
            [(int(r),) for r in rowids],
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Statements + traspaso reconciliation
# ---------------------------------------------------------------------------

def upsert_estado_cuenta(conn, meta: Dict[str, Any]) -> None:
    if not meta.get("ARCHIVO_ORIGEN"):
        return
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO estados_cuenta
                (ORIGEN, TITULAR_NOMBRE, ARCHIVO_ORIGEN, FECHA_ESTADO,
                 PERIODO_DESDE, PERIODO_HASTA, DEUDA_TOTAL, MONEDA)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ARCHIVO_ORIGEN) DO NOTHING;
            """,
            (
                meta.get("ORIGEN", ""),
                meta.get("TITULAR_NOMBRE"),
                meta.get("ARCHIVO_ORIGEN"),
                meta.get("FECHA_ESTADO"),
                meta.get("PERIODO_DESDE"),
                meta.get("PERIODO_HASTA"),
                meta.get("DEUDA_TOTAL"),
                meta.get("MONEDA", ""),
            ),
        )
    conn.commit()


def fetch_estados_cuenta(
    conn, origen: Optional[str] = None
) -> Tuple[List[str], List[tuple]]:
    with conn.cursor() as cur:
        if origen:
            cur.execute(
                "SELECT * FROM estados_cuenta WHERE ORIGEN = %s ORDER BY FECHA_ESTADO",
                (origen,),
            )
        else:
            cur.execute("SELECT * FROM estados_cuenta ORDER BY ORIGEN, FECHA_ESTADO")
        cols = [d[0] for d in cur.description]
        return cols, cur.fetchall()


def marcar_traspaso(
    conn,
    estado_id: int,
    match_rid: Optional[int],
    match_archivo: Optional[str],
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ARCHIVO_ORIGEN, DEUDA_TOTAL FROM estados_cuenta WHERE id = %s",
            (estado_id,),
        )
        row = cur.fetchone()

    tasa = None
    if match_rid is not None and row and row[1]:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT MONTO_TOTAL FROM transacciones WHERE id = %s", (int(match_rid),)
            )
            clp_row = cur.fetchone()
        deuda_usd = row[1]
        if clp_row and clp_row[0] and deuda_usd:
            try:
                tasa = abs(float(clp_row[0])) / abs(float(deuda_usd))
            except (ZeroDivisionError, ValueError):
                tasa = None

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE estados_cuenta
            SET TRASPASO_ESTADO = 'TRASPASADO', MATCH_RID = %s,
                MATCH_ARCHIVO = %s, TASA_CAMBIO = %s
            WHERE id = %s;
            """,
            (match_rid, match_archivo, tasa, int(estado_id)),
        )
        if row and row[0]:
            if tasa is not None:
                cur.execute(
                    """
                    UPDATE transacciones
                    SET TRASPASADO = 1, MONTO_CLP = ROUND(CAST(MONTO_OPERACION * %s AS NUMERIC))
                    WHERE ARCHIVO_ORIGEN = %s;
                    """,
                    (tasa, row[0]),
                )
            else:
                cur.execute(
                    "UPDATE transacciones SET TRASPASADO = 1 WHERE ARCHIVO_ORIGEN = %s;",
                    (row[0],),
                )
    conn.commit()


def desmarcar_traspaso(conn, estado_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ARCHIVO_ORIGEN FROM estados_cuenta WHERE id = %s", (int(estado_id),)
        )
        row = cur.fetchone()
        cur.execute(
            """
            UPDATE estados_cuenta
            SET TRASPASO_ESTADO = 'PENDIENTE', MATCH_RID = NULL,
                MATCH_ARCHIVO = NULL, TASA_CAMBIO = NULL
            WHERE id = %s;
            """,
            (int(estado_id),),
        )
        if row and row[0]:
            cur.execute(
                "UPDATE transacciones SET TRASPASADO = 0, MONTO_CLP = NULL WHERE ARCHIVO_ORIGEN = %s;",
                (row[0],),
            )
    conn.commit()


def fetch_traspaso_nacional_disponibles(conn) -> List[Dict[str, Any]]:
    sort = _sort_expr("t.FECHA_OPERACION")
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT t.id AS rid, t.FECHA_OPERACION AS fecha,
                   t.MONTO_TOTAL AS clp, t.ARCHIVO_ORIGEN AS archivo
            FROM transacciones t
            WHERE t.ORIGEN = 'NACIONAL'
              AND UPPER(t.DESCRIPCION) LIKE '%TRASPASO DEUDA INTERNAC%'
              AND t.id NOT IN (
                  SELECT MATCH_RID FROM estados_cuenta WHERE MATCH_RID IS NOT NULL
              )
            ORDER BY {sort}
            """
        )
        return [dict(r) for r in cur.fetchall()]


def fetch_estados_intl_pendientes(conn) -> List[Dict[str, Any]]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT ec.id AS id, ec.ARCHIVO_ORIGEN AS archivo,
                   ec.TITULAR_NOMBRE AS titular, ec.DEUDA_TOTAL AS deuda,
                   ec.PERIODO_DESDE AS desde, ec.PERIODO_HASTA AS hasta
            FROM estados_cuenta ec
            WHERE ec.ORIGEN = 'INTERNACIONAL' AND ec.TRASPASO_ESTADO != 'TRASPASADO'
            """
        )
        return [dict(r) for r in cur.fetchall()]


def fetch_traspaso_suggestions(
    conn,
) -> Tuple[Dict[int, Dict[str, Any]], set]:
    nac = fetch_traspaso_nacional_disponibles(conn)
    nac_by_date: Dict[str, List[Dict[str, Any]]] = {}
    for n in nac:
        nac_by_date.setdefault(n["fecha"], []).append(n)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT FECHA_OPERACION, MONTO_OPERACION FROM transacciones
            WHERE ORIGEN = 'INTERNACIONAL'
              AND UPPER(DESCRIPCION) LIKE '%TRASPASO%'
              AND MONTO_OPERACION IS NOT NULL
            """
        )
        credits = [(f, abs(float(u))) for f, u in cur.fetchall()]

    suggestions: Dict[int, Dict[str, Any]] = {}
    ambiguous: set = set()
    for est in fetch_estados_intl_pendientes(conn):
        deuda = est.get("deuda")
        if deuda is None:
            continue
        dates = [f for (f, u) in credits if abs(u - float(deuda)) < 0.01]
        cands = {n["rid"]: n for d in dates for n in nac_by_date.get(d, [])}
        cands = list(cands.values())
        if len(cands) == 1:
            n = cands[0]
            suggestions[int(est["id"])] = {
                "rid": int(n["rid"]),
                "archivo": n["archivo"],
                "clp": n["clp"],
                "tasa": abs(float(n["clp"])) / float(deuda) if deuda else None,
            }
        elif len(cands) > 1:
            ambiguous.add(int(est["id"]))
    return suggestions, ambiguous


def auto_match_traspasos(conn) -> int:
    applied = 0
    for _ in range(1000):
        suggestions, _ = fetch_traspaso_suggestions(conn)
        if not suggestions:
            break
        est_id, s = next(iter(suggestions.items()))
        marcar_traspaso(conn, est_id, s["rid"], s["archivo"])
        applied += 1
    return applied


# ---------------------------------------------------------------------------
# Auto-categorization
# ---------------------------------------------------------------------------

STATIC_TIPO_GASTO_NAC: list[tuple[str, str]] = [
    ("COMISION COMPRA INTERNACIONAL", "Comision Intl"),
    ("IMPUESTO DECRETO LEY",          "Impuesto"),
    ("COBRO ADM MENSUAL",             "Comision Nacional"),
    ("INTERESES ROTATIVOS",           "Comision Nacional"),
    ("TRASPASO DEUDA INTERNACIONAL",  "Tr Deuda Intl"),
    ("PAGO PAC EN PESOS",             "BCI Paga TC"),
]

STATIC_TIPO_GASTO_INTL: list[tuple[str, str]] = [
    ("HUBSPOT",                  "Hubspot"),
    ("GOOGLE *WORKSPACE",        "GSuite"),
    ("GOOGLE *",                 "Google"),
    ("GODADDY",                  "GSuite"),
    ("FACEBK",                   "Marketing"),
    ("AIRBNB",                   "Airbnb"),
    ("SHUTTERSTOCK",             "Shutterstock"),
    ("CANVA",                    "Canva"),
    ("TRASPASO DEUDA INTERNAC",  "Trp a Deuda Nacional"),
    ("UBER",                     "Huber"),
]


def fetch_tipo_gasto_map(conn) -> dict[str, str]:
    """Return {DESCRIPCION: TIPO_GASTO} using the most recently inserted row
    per description that has a non-empty TIPO_GASTO."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DESCRIPCION, TIPO_GASTO
            FROM transacciones
            WHERE TIPO_GASTO IS NOT NULL AND TIPO_GASTO != ''
              AND id IN (
                  SELECT MAX(id)
                  FROM transacciones
                  WHERE TIPO_GASTO IS NOT NULL AND TIPO_GASTO != ''
                  GROUP BY DESCRIPCION
              )
            """
        )
        return {row[0]: row[1] for row in cur.fetchall()}


def auto_tipo_gasto(descripcion: str, historic_map: dict[str, str], origen: str = "") -> str:
    if descripcion in historic_map:
        return historic_map[descripcion]
    desc_upper = descripcion.upper()
    rules = STATIC_TIPO_GASTO_INTL if origen == "INTERNACIONAL" else STATIC_TIPO_GASTO_NAC
    for keyword, tipo in rules:
        if keyword in desc_upper:
            return tipo
    return ""


def propagar_clasificacion(conn, updates: list[dict]) -> None:
    with conn.cursor() as cur:
        for u in updates:
            tipo = u.get("TIPO_GASTO") or ""
            if not tipo:
                continue
            cur.execute(
                """
                UPDATE transacciones
                SET TIPO_GASTO = %s
                WHERE DESCRIPCION = (SELECT DESCRIPCION FROM transacciones WHERE id = %s)
                  AND FACT_KAME = 0
                  AND (TIPO_GASTO IS NULL OR TIPO_GASTO = '' OR TIPO_GASTO != %s)
                """,
                (tipo, int(u["_RID_"]), tipo),
            )
    conn.commit()


# ---------------------------------------------------------------------------
# Uploaded-files summary (for dashboard)
# ---------------------------------------------------------------------------

def fetch_archivos_resumen(conn) -> Tuple[List[str], List[tuple]]:
    # FECHA_ESTADO is DD-MM-YYYY; reformat to YYYYMMDD for correct DESC sort
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                ec.ORIGEN           AS origen,
                ec.TITULAR_NOMBRE   AS titular,
                ec.ARCHIVO_ORIGEN   AS archivo,
                ec.FECHA_ESTADO     AS fecha_estado,
                ec.PERIODO_DESDE    AS periodo_desde,
                ec.PERIODO_HASTA    AS periodo_hasta,
                ec.DEUDA_TOTAL      AS deuda_total,
                ec.MONEDA           AS moneda,
                ec.TRASPASO_ESTADO  AS traspaso_estado,
                (SELECT COUNT(*) FROM transacciones t
                 WHERE t.ARCHIVO_ORIGEN = ec.ARCHIVO_ORIGEN) AS transacciones
            FROM estados_cuenta ec
            ORDER BY
                substring(ec.FECHA_ESTADO,7,4)||substring(ec.FECHA_ESTADO,4,2)||substring(ec.FECHA_ESTADO,1,2) DESC
            """
        )
        cols = [d[0] for d in cur.description]
        return cols, cur.fetchall()


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------

def reset_db(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("TRUNCATE transacciones, estados_cuenta, archivos_procesados RESTART IDENTITY CASCADE;")
    conn.commit()
