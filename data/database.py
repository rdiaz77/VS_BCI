"""PostgreSQL data layer (Neon).

Tables
  transacciones  — NACIONAL (CLP) and INTERNACIONAL (USD) rows
  estados_cuenta — one row per statement (card + date + type is the identity)

Money is NUMERIC, not float: a credit-card ledger cannot use binary floats.
Dates are stored twice — the original bank text (for display) and a real DATE
column (for sorting and range filters).
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import unquote, urlparse

import psycopg2
import psycopg2.extras
import psycopg2.pool

_log = logging.getLogger(__name__)

TRANSACCIONES_COLS = [
    "ORIGEN",             # 'NACIONAL' | 'INTERNACIONAL'
    "TITULAR_NOMBRE",
    "TARJETA_ULT4",       # last 4 digits — the stable card identity
    "FECHA_OPERACION",    # MM/DD/YY as printed by the bank
    "FECHA_OPERACION_D",  # real DATE, derived
    "DESCRIPCION",
    "CIUDAD",
    "PAIS",
    "REF_INTERNACIONAL",
    "MONTO_ORIGEN",
    "MONTO_OPERACION",
    "MONTO_TOTAL",
    "MONTO_CLP",          # intl only: USD converted at the bank traspaso rate
    "MONEDA",
    "TIPO_GASTO",
    "CONCILIADO",
    "FACT_KAME",
    "TRASPASADO",
    "ARCHIVO_ORIGEN",
]

MONEY_COLS = ("MONTO_ORIGEN", "MONTO_OPERACION", "MONTO_TOTAL", "MONTO_CLP")


# ---------------------------------------------------------------------------
# Connection pool
#
# Streamlit runs every browser session in its own thread, and a raw psycopg2
# connection is not safe for concurrent cursor use. One pooled connection is
# checked out per script run and returned in a finally block.
# ---------------------------------------------------------------------------

def _dsn_parts(db_url: str) -> dict:
    u = urlparse(db_url.replace("#", "%23"))
    return {
        "host": u.hostname,
        "port": u.port or 5432,
        "dbname": (u.path or "").lstrip("/"),
        "user": u.username,
        "password": unquote(u.password or ""),
        "sslmode": "require",
    }


def init_pool(db_url: str, minconn: int = 1, maxconn: int = 8):
    """Create the pool and ensure the schema exists."""
    pool = psycopg2.pool.ThreadedConnectionPool(minconn, maxconn, **_dsn_parts(db_url))
    conn = pool.getconn()
    try:
        _ensure_schema(conn)
    finally:
        pool.putconn(conn)
    return pool


@contextmanager
def pooled_conn(pool):
    """Check out a live connection; transparently replace one Neon has closed."""
    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
    except Exception:
        # Idle connection dropped server-side — discard it and take another.
        _log.warning("Pooled connection was dead; discarding and retrying")
        try:
            pool.putconn(conn, close=True)
        except Exception:
            pass
        conn = pool.getconn()

    try:
        yield conn
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            pool.putconn(conn)
        except Exception:
            pass


def init_db(db_url: str):
    """Single (unpooled) connection — used by scripts and tests, not the app."""
    conn = psycopg2.connect(**_dsn_parts(db_url))
    conn.autocommit = False
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS transacciones (
                id                SERIAL PRIMARY KEY,
                ORIGEN            TEXT NOT NULL,
                TITULAR_NOMBRE    TEXT,
                TARJETA_ULT4      TEXT,
                FECHA_OPERACION   TEXT,
                FECHA_OPERACION_D DATE,
                DESCRIPCION       TEXT,
                CIUDAD            TEXT,
                PAIS              TEXT,
                REF_INTERNACIONAL TEXT,
                MONTO_ORIGEN      NUMERIC(18,4),
                MONTO_OPERACION   NUMERIC(18,4),
                MONTO_TOTAL       NUMERIC(18,4),
                MONTO_CLP         NUMERIC(18,4),
                MONEDA            TEXT,
                TIPO_GASTO        TEXT,
                CONCILIADO        INTEGER NOT NULL DEFAULT 0,
                FACT_KAME         INTEGER NOT NULL DEFAULT 0,
                TRASPASADO        INTEGER NOT NULL DEFAULT 0,
                ARCHIVO_ORIGEN    TEXT
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS estados_cuenta (
                id               SERIAL PRIMARY KEY,
                ORIGEN           TEXT NOT NULL,
                TITULAR_NOMBRE   TEXT,
                TITULAR_COMPLETO TEXT,
                TARJETA_ULT4     TEXT,
                ARCHIVO_ORIGEN   TEXT UNIQUE NOT NULL,
                FECHA_ESTADO     TEXT,
                FECHA_ESTADO_D   DATE,
                PERIODO_DESDE    TEXT,
                PERIODO_HASTA    TEXT,
                DEUDA_TOTAL      NUMERIC(18,4),
                MONEDA           TEXT,
                TRASPASO_ESTADO  TEXT NOT NULL DEFAULT 'PENDIENTE',
                MATCH_RID        INTEGER,
                MATCH_ARCHIVO    TEXT,
                TASA_CAMBIO      NUMERIC(12,4),
                FECHA_CARGA      TIMESTAMPTZ DEFAULT NOW()
            );
            """
        )
        for name, target in (
            ("idx_tx_origen", "transacciones(ORIGEN)"),
            ("idx_tx_fact_kame", "transacciones(FACT_KAME)"),
            ("idx_tx_archivo", "transacciones(ARCHIVO_ORIGEN)"),
            ("idx_tx_traspasado", "transacciones(TRASPASADO)"),
            ("idx_tx_fecha_d", "transacciones(FECHA_OPERACION_D)"),
            ("idx_tx_tarjeta", "transacciones(TARJETA_ULT4)"),
            ("idx_tx_origen_kame", "transacciones(ORIGEN, FACT_KAME)"),
            ("idx_ec_fecha_d", "estados_cuenta(FECHA_ESTADO_D)"),
            ("idx_ec_ident", "estados_cuenta(ORIGEN, FECHA_ESTADO, TARJETA_ULT4)"),
        ):
            cur.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {target};")
    conn.commit()


# ---------------------------------------------------------------------------
# Statement identity / dedup
# ---------------------------------------------------------------------------

def estado_ya_procesado(conn, meta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return the already-stored statement this one duplicates, else None.

    Identity is (ORIGEN, FECHA_ESTADO, TARJETA_ULT4). The cardholder name is
    NOT part of it — the same card prints as 'RAFAEL DIAZ' on one statement
    and 'RAFAEL P. DIAZ' on the next.
    """
    origen = meta.get("ORIGEN")
    fecha = meta.get("FECHA_ESTADO")
    ult4 = meta.get("TARJETA_ULT4")
    titular = meta.get("TITULAR_NOMBRE")
    if not (origen and fecha):
        return None

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        if ult4:
            cur.execute(
                """SELECT ARCHIVO_ORIGEN AS archivo, TARJETA_ULT4 AS ult4,
                          TITULAR_NOMBRE AS titular, FECHA_ESTADO AS fecha
                   FROM estados_cuenta
                   WHERE ORIGEN = %s AND FECHA_ESTADO = %s AND TARJETA_ULT4 = %s
                   LIMIT 1""",
                (origen, fecha, ult4),
            )
            row = cur.fetchone()
            if row:
                return dict(row)
            # Rows loaded before card tracking existed have a NULL card.
            cur.execute(
                """SELECT ARCHIVO_ORIGEN AS archivo, TARJETA_ULT4 AS ult4,
                          TITULAR_NOMBRE AS titular, FECHA_ESTADO AS fecha
                   FROM estados_cuenta
                   WHERE ORIGEN = %s AND FECHA_ESTADO = %s
                     AND TARJETA_ULT4 IS NULL AND TITULAR_NOMBRE IS NOT DISTINCT FROM %s
                   LIMIT 1""",
                (origen, fecha, titular),
            )
        else:
            cur.execute(
                """SELECT ARCHIVO_ORIGEN AS archivo, TARJETA_ULT4 AS ult4,
                          TITULAR_NOMBRE AS titular, FECHA_ESTADO AS fecha
                   FROM estados_cuenta
                   WHERE ORIGEN = %s AND FECHA_ESTADO = %s
                     AND TITULAR_NOMBRE IS NOT DISTINCT FROM %s
                   LIMIT 1""",
                (origen, fecha, titular),
            )
        row = cur.fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# Ingest — statement + its transactions, in one transaction
# ---------------------------------------------------------------------------

def ingest_statement(conn, rows: Iterable[Dict[str, Any]], meta: Dict[str, Any]) -> int:
    """Insert one statement and its transactions atomically.

    Returns the number of transactions written, or 0 if the statement was
    already present (in which case nothing is written at all).
    """
    rows = list(rows)
    if not meta.get("ARCHIVO_ORIGEN"):
        raise ValueError("meta sin ARCHIVO_ORIGEN")

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO estados_cuenta
                    (ORIGEN, TITULAR_NOMBRE, TITULAR_COMPLETO, TARJETA_ULT4,
                     ARCHIVO_ORIGEN, FECHA_ESTADO, FECHA_ESTADO_D,
                     PERIODO_DESDE, PERIODO_HASTA, DEUDA_TOTAL, MONEDA)
                VALUES (%s, %s, %s, %s, %s, %s,
                        to_date(NULLIF(%s, ''), 'DD-MM-YYYY'), %s, %s, %s, %s)
                ON CONFLICT (ARCHIVO_ORIGEN) DO NOTHING;
                """,
                (
                    meta.get("ORIGEN", ""),
                    meta.get("TITULAR_NOMBRE"),
                    meta.get("TITULAR_COMPLETO"),
                    meta.get("TARJETA_ULT4"),
                    meta.get("ARCHIVO_ORIGEN"),
                    meta.get("FECHA_ESTADO"),
                    meta.get("FECHA_ESTADO"),
                    meta.get("PERIODO_DESDE"),
                    meta.get("PERIODO_HASTA"),
                    meta.get("DEUDA_TOTAL"),
                    meta.get("MONEDA", ""),
                ),
            )
            if cur.rowcount == 0:
                # Statement already there — do not orphan transactions onto it.
                conn.rollback()
                return 0

            if rows:
                col_list = ", ".join(TRANSACCIONES_COLS)
                placeholders = ", ".join(
                    "to_date(NULLIF(%s, ''), 'MM/DD/YY')"
                    if c == "FECHA_OPERACION_D"
                    else "%s"
                    for c in TRANSACCIONES_COLS
                )
                data = [
                    (
                        r.get("ORIGEN", ""),
                        r.get("TITULAR_NOMBRE"),
                        r.get("TARJETA_ULT4"),
                        r.get("FECHA_OPERACION", ""),
                        r.get("FECHA_OPERACION", ""),  # -> to_date(...)
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
                psycopg2.extras.execute_batch(
                    cur,
                    f"INSERT INTO transacciones ({col_list}) VALUES ({placeholders});",
                    data,
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return len(rows)


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------

def fetch_transacciones(
    conn,
    origen: Optional[str] = None,
    tarjeta: Optional[str] = None,
    desde: Optional[str] = None,
    hasta: Optional[str] = None,
    archivo: Optional[str] = None,
    search: Optional[str] = None,
    fact_kame: Optional[int] = None,
) -> Tuple[List[str], List[tuple]]:
    """Return (cols, rows) with id exposed as _RID_, newest last.

    All filters are optional and combine with AND. `desde`/`hasta` are ISO
    dates (YYYY-MM-DD) applied to the real DATE column.
    """
    where: List[str] = []
    params: List[Any] = []
    if origen:
        where.append("ORIGEN = %s")
        params.append(origen)
    if tarjeta:
        where.append("TARJETA_ULT4 = %s")
        params.append(tarjeta)
    if archivo:
        where.append("ARCHIVO_ORIGEN = %s")
        params.append(archivo)
    if desde:
        where.append("FECHA_OPERACION_D >= %s")
        params.append(desde)
    if hasta:
        where.append("FECHA_OPERACION_D <= %s")
        params.append(hasta)
    if search:
        where.append("DESCRIPCION ILIKE %s")
        params.append(f"%{search}%")
    if fact_kame is not None:
        where.append("FACT_KAME = %s")
        params.append(int(fact_kame))

    clause = f"WHERE {' AND '.join(where)}" if where else ""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT id AS _RID_, * FROM transacciones {clause} "
            f"ORDER BY FECHA_OPERACION_D NULLS LAST, id",
            params,
        )
        cols = [d[0].upper() for d in cur.description]
        return cols, cur.fetchall()


def fetch_tarjetas(conn) -> List[Dict[str, Any]]:
    """Distinct cards seen, with their statement counts and date span."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT TARJETA_ULT4 AS ult4,
                   max(TITULAR_COMPLETO) AS titular,
                   count(*)              AS estados,
                   min(FECHA_ESTADO_D)   AS desde,
                   max(FECHA_ESTADO_D)   AS hasta
            FROM estados_cuenta
            GROUP BY TARJETA_ULT4
            ORDER BY TARJETA_ULT4 NULLS FIRST
            """
        )
        return [dict(r) for r in cur.fetchall()]


def fetch_rango_fechas(conn) -> Tuple[Optional[Any], Optional[Any]]:
    with conn.cursor() as cur:
        cur.execute("SELECT min(FECHA_OPERACION_D), max(FECHA_OPERACION_D) FROM transacciones")
        return cur.fetchone()


def update_clasificacion(conn, updates: List[Dict[str, Any]]) -> None:
    if not updates:
        return
    try:
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(
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
    except Exception:
        conn.rollback()
        raise


def marcar_fact_kame(conn, rowids: List[int]) -> None:
    if not rowids:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE transacciones SET FACT_KAME = 1 WHERE id = ANY(%s);",
                ([int(r) for r in rowids],),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def mover_a_pendientes(conn, rids: List[int]) -> None:
    """Send transactions back to Pendientes by clearing FACT_KAME."""
    if not rids:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE transacciones SET FACT_KAME = 0 WHERE id = ANY(%s)",
                ([int(r) for r in rids],),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# ---------------------------------------------------------------------------
# Statements + traspaso reconciliation
# ---------------------------------------------------------------------------

def fetch_estados_cuenta(
    conn, origen: Optional[str] = None
) -> Tuple[List[str], List[tuple]]:
    with conn.cursor() as cur:
        if origen:
            cur.execute(
                "SELECT * FROM estados_cuenta WHERE ORIGEN = %s "
                "ORDER BY FECHA_ESTADO_D NULLS LAST",
                (origen,),
            )
        else:
            cur.execute(
                "SELECT * FROM estados_cuenta ORDER BY ORIGEN, FECHA_ESTADO_D NULLS LAST"
            )
        cols = [d[0].upper() for d in cur.description]
        return cols, cur.fetchall()


def marcar_traspaso(
    conn,
    estado_id: int,
    match_rid: Optional[int],
    match_archivo: Optional[str],
) -> None:
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT ARCHIVO_ORIGEN, DEUDA_TOTAL FROM estados_cuenta WHERE id = %s",
                (int(estado_id),),
            )
            row = cur.fetchone()

            tasa = None
            if match_rid is not None and row and row[1]:
                cur.execute(
                    "SELECT MONTO_TOTAL FROM transacciones WHERE id = %s",
                    (int(match_rid),),
                )
                clp_row = cur.fetchone()
                if clp_row and clp_row[0]:
                    try:
                        tasa = abs(clp_row[0]) / abs(row[1])
                    except (ZeroDivisionError, ArithmeticError):
                        tasa = None

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
                        SET TRASPASADO = 1, MONTO_CLP = ROUND(MONTO_OPERACION * %s, 0)
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
    except Exception:
        conn.rollback()
        raise


def desmarcar_traspaso(conn, estado_id: int) -> None:
    try:
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
                    "UPDATE transacciones SET TRASPASADO = 0, MONTO_CLP = NULL "
                    "WHERE ARCHIVO_ORIGEN = %s;",
                    (row[0],),
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def fetch_traspaso_nacional_disponibles(conn) -> List[Dict[str, Any]]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT t.id AS rid, t.FECHA_OPERACION AS fecha,
                   t.FECHA_OPERACION_D AS fecha_d,
                   t.MONTO_TOTAL AS clp, t.ARCHIVO_ORIGEN AS archivo,
                   t.TARJETA_ULT4 AS ult4
            FROM transacciones t
            WHERE t.ORIGEN = 'NACIONAL'
              AND UPPER(t.DESCRIPCION) LIKE '%TRASPASO DEUDA INTERNAC%'
              AND NOT EXISTS (
                  SELECT 1 FROM estados_cuenta ec WHERE ec.MATCH_RID = t.id
              )
            ORDER BY t.FECHA_OPERACION_D NULLS LAST
            """
        )
        return [dict(r) for r in cur.fetchall()]


def fetch_estados_intl_pendientes(conn) -> List[Dict[str, Any]]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT ec.id AS id, ec.ARCHIVO_ORIGEN AS archivo,
                   ec.TITULAR_NOMBRE AS titular, ec.TARJETA_ULT4 AS ult4,
                   ec.DEUDA_TOTAL AS deuda,
                   ec.PERIODO_DESDE AS desde, ec.PERIODO_HASTA AS hasta
            FROM estados_cuenta ec
            WHERE ec.ORIGEN = 'INTERNACIONAL'
              AND ec.TRASPASO_ESTADO != 'TRASPASADO'
            ORDER BY ec.FECHA_ESTADO_D NULLS LAST
            """
        )
        return [dict(r) for r in cur.fetchall()]


def fetch_traspaso_suggestions(conn) -> Tuple[Dict[int, Dict[str, Any]], set]:
    """Match each pending international statement to a national TRASPASO line.

    A statement's DEUDA TOTAL (USD) appears as a credit on the international
    side and as one CLP line on the national side, same date. Only unambiguous
    single matches are suggested; the card must agree when both are known.
    """
    nac = fetch_traspaso_nacional_disponibles(conn)
    nac_by_date: Dict[str, List[Dict[str, Any]]] = {}
    for n in nac:
        nac_by_date.setdefault(n["fecha"], []).append(n)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT FECHA_OPERACION, MONTO_OPERACION, TARJETA_ULT4
            FROM transacciones
            WHERE ORIGEN = 'INTERNACIONAL'
              AND UPPER(DESCRIPCION) LIKE '%TRASPASO%'
              AND MONTO_OPERACION IS NOT NULL
            """
        )
        credits = [(f, abs(u), c) for f, u, c in cur.fetchall() if u is not None]

    suggestions: Dict[int, Dict[str, Any]] = {}
    ambiguous: set = set()
    for est in fetch_estados_intl_pendientes(conn):
        deuda = est.get("deuda")
        if deuda is None:
            continue
        deuda_abs = abs(deuda)
        est_ult4 = est.get("ult4")
        # Dates on which this statement's balance was credited internationally.
        # Exact comparison is safe now that money is NUMERIC; the old 0.01
        # slack existed only to absorb float4 noise.
        dates = [
            f
            for (f, u, c) in credits
            if abs(u - deuda_abs) < Decimal("0.01")
            and (not est_ult4 or not c or c == est_ult4)
        ]
        cands = {
            n["rid"]: n
            for d in dates
            for n in nac_by_date.get(d, [])
            if not est_ult4 or not n.get("ult4") or n["ult4"] == est_ult4
        }
        cands = list(cands.values())
        if len(cands) == 1:
            n = cands[0]
            suggestions[int(est["id"])] = {
                "rid": int(n["rid"]),
                "archivo": n["archivo"],
                "clp": n["clp"],
                "tasa": (abs(n["clp"]) / deuda_abs) if deuda_abs else None,
            }
        elif len(cands) > 1:
            ambiguous.add(int(est["id"]))
    return suggestions, ambiguous


def auto_match_traspasos(conn) -> int:
    suggestions, _ = fetch_traspaso_suggestions(conn)
    for est_id, s in suggestions.items():
        marcar_traspaso(conn, est_id, s["rid"], s["archivo"])
    return len(suggestions)


# ---------------------------------------------------------------------------
# Auto-categorization
# ---------------------------------------------------------------------------

STATIC_TIPO_GASTO_NAC: List[Tuple[str, str]] = [
    ("COMISION COMPRA INTERNACIONAL", "Comision Intl"),
    ("IMPUESTO DECRETO LEY", "Impuesto"),
    ("COBRO ADM MENSUAL", "Comision Nacional"),
    ("INTERESES ROTATIVOS", "Comision Nacional"),
    ("TRASPASO DEUDA INTERNACIONAL", "Tr Deuda Intl"),
    ("PAGO PAC EN PESOS", "BCI Paga TC"),
]

STATIC_TIPO_GASTO_INTL: List[Tuple[str, str]] = [
    ("HUBSPOT", "Hubspot"),
    ("GOOGLE *WORKSPACE", "GSuite"),
    ("GOOGLE *", "Google"),
    ("GODADDY", "GSuite"),
    ("FACEBK", "Marketing"),
    ("MICROSOFT", "Microsoft"),
    ("AIRBNB", "Airbnb"),
    ("SHUTTERSTOCK", "Shutterstock"),
    ("CANVA", "Canva"),
    ("TRASPASO DEUDA INTERNAC", "Trp a Deuda Nacional"),
    ("UBER", "Huber"),
]


def fetch_tipo_gasto_map(conn) -> Dict[Tuple[str, str], str]:
    """{(ORIGEN, DESCRIPCION): TIPO_GASTO} from the most recent classified row.

    Keyed by ORIGEN because the national and international vocabularies are
    different — propagating across them writes values the dropdown rejects.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ORIGEN, DESCRIPCION, TIPO_GASTO
            FROM transacciones
            WHERE TIPO_GASTO IS NOT NULL AND TIPO_GASTO != ''
              AND id IN (
                  SELECT MAX(id) FROM transacciones
                  WHERE TIPO_GASTO IS NOT NULL AND TIPO_GASTO != ''
                  GROUP BY ORIGEN, DESCRIPCION
              )
            """
        )
        return {(r[0], r[1]): r[2] for r in cur.fetchall()}


def auto_tipo_gasto(
    descripcion: str, historic_map: Dict[Tuple[str, str], str], origen: str = ""
) -> str:
    hit = historic_map.get((origen, descripcion))
    if hit:
        return hit
    desc_upper = (descripcion or "").upper()
    rules = STATIC_TIPO_GASTO_INTL if origen == "INTERNACIONAL" else STATIC_TIPO_GASTO_NAC
    for keyword, tipo in rules:
        if keyword in desc_upper:
            return tipo
    return ""


def propagar_clasificacion(conn, updates: List[Dict[str, Any]]) -> int:
    """Apply a row's TIPO_GASTO to same-description rows that have none.

    Only ever FILLS blanks — it never overwrites a classification someone
    already made — and stays within the same ORIGEN.
    """
    filled = 0
    try:
        with conn.cursor() as cur:
            for u in updates:
                tipo = (u.get("TIPO_GASTO") or "").strip()
                if not tipo:
                    continue
                cur.execute(
                    """
                    UPDATE transacciones t
                    SET TIPO_GASTO = %s
                    FROM (
                        SELECT DESCRIPCION, ORIGEN FROM transacciones WHERE id = %s
                    ) src
                    WHERE t.DESCRIPCION = src.DESCRIPCION
                      AND t.ORIGEN      = src.ORIGEN
                      AND t.FACT_KAME   = 0
                      AND (t.TIPO_GASTO IS NULL OR t.TIPO_GASTO = '')
                    """,
                    (tipo, int(u["_RID_"])),
                )
                filled += cur.rowcount
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return filled


# ---------------------------------------------------------------------------
# Uploaded-statement summary (dashboard)
# ---------------------------------------------------------------------------

def fetch_archivos_resumen(conn) -> Tuple[List[str], List[tuple]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                ec.ORIGEN           AS origen,
                ec.TITULAR_NOMBRE   AS titular,
                ec.TARJETA_ULT4     AS tarjeta,
                ec.ARCHIVO_ORIGEN   AS archivo,
                ec.FECHA_ESTADO     AS fecha_estado,
                ec.PERIODO_DESDE    AS periodo_desde,
                ec.PERIODO_HASTA    AS periodo_hasta,
                ec.DEUDA_TOTAL      AS deuda_total,
                ec.MONEDA           AS moneda,
                ec.TRASPASO_ESTADO  AS traspaso_estado,
                coalesce(t.n, 0)    AS transacciones,
                coalesce(t.c, 0)    AS conciliadas
            FROM estados_cuenta ec
            LEFT JOIN (
                SELECT ARCHIVO_ORIGEN,
                       count(*)                        AS n,
                       count(*) FILTER (WHERE CONCILIADO = 1) AS c
                FROM transacciones GROUP BY ARCHIVO_ORIGEN
            ) t ON t.ARCHIVO_ORIGEN = ec.ARCHIVO_ORIGEN
            ORDER BY ec.FECHA_ESTADO_D DESC NULLS LAST
            """
        )
        cols = [d[0].upper() for d in cur.description]
        return cols, cur.fetchall()


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------

def delete_estado_cuenta(conn, archivo_origen: str) -> None:
    """Delete a statement and its transactions.

    Refuses if any transaction is already conciliada.
    """
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM transacciones "
                "WHERE ARCHIVO_ORIGEN = %s AND CONCILIADO = 1",
                (archivo_origen,),
            )
            conc = cur.fetchone()[0]
            if conc:
                raise ValueError(
                    f"No se puede eliminar: {conc} transacción(es) ya están conciliadas."
                )
            cur.execute(
                "DELETE FROM transacciones WHERE ARCHIVO_ORIGEN = %s", (archivo_origen,)
            )
            cur.execute(
                "DELETE FROM estados_cuenta WHERE ARCHIVO_ORIGEN = %s", (archivo_origen,)
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def reset_db(conn) -> None:
    try:
        with conn.cursor() as cur:
            cur.execute(
                "TRUNCATE transacciones, estados_cuenta RESTART IDENTITY CASCADE;"
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
