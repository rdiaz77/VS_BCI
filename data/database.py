import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

# ============================================================
# Unified SQLite layer — cartola_tct_bci.db
#   transacciones      — NACIONAL (CLP) and INTERNACIONAL (USD) rows
#   estados_cuenta     — one row per statement (traspaso reconciliation)
#   archivos_procesados — upload dedup
# ============================================================

DB_NAME = "cartola_tct_bci.db"

TRANSACCIONES_COLS = [
    "ORIGEN",            # 'NACIONAL' | 'INTERNACIONAL'
    "TITULAR_NOMBRE",
    "FECHA_OPERACION",   # MM/DD/YY
    "DESCRIPCION",
    "CIUDAD",            # intl only
    "PAIS",              # intl only
    "REF_INTERNACIONAL", # intl only
    "MONTO_ORIGEN",      # intl: amount in origin currency
    "MONTO_OPERACION",   # USD (intl) or CLP (nacional)
    "MONTO_TOTAL",       # same currency as MONTO_OPERACION
    "MONEDA",            # 'USD' | 'CLP'
    "TIPO_GASTO",
    "CONCILIADO",        # 0/1
    "FACT_KAME",         # 0/1  — entered in Kame
    "TRASPASADO",        # 0/1  — intl statement transferred to national
    "ARCHIVO_ORIGEN",
]


def init_db(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)

    # WAL mode: better concurrent read performance in production
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS transacciones (
            ORIGEN            TEXT NOT NULL,
            TITULAR_NOMBRE    TEXT,
            FECHA_OPERACION   TEXT,
            DESCRIPCION       TEXT,
            CIUDAD            TEXT,
            PAIS              TEXT,
            REF_INTERNACIONAL TEXT,
            MONTO_ORIGEN      REAL,
            MONTO_OPERACION   REAL,
            MONTO_TOTAL       REAL,
            MONTO_CLP         REAL,   -- intl only: USD converted at bank traspaso rate
            MONEDA            TEXT,
            TIPO_GASTO        TEXT,
            CONCILIADO        INTEGER NOT NULL DEFAULT 0,
            FACT_KAME         INTEGER NOT NULL DEFAULT 0,
            TRASPASADO        INTEGER NOT NULL DEFAULT 0,
            ARCHIVO_ORIGEN    TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_tx_origen      ON transacciones(ORIGEN);
        CREATE INDEX IF NOT EXISTS idx_tx_fact_kame   ON transacciones(FACT_KAME);
        CREATE INDEX IF NOT EXISTS idx_tx_archivo     ON transacciones(ARCHIVO_ORIGEN);
        CREATE INDEX IF NOT EXISTS idx_tx_traspasado  ON transacciones(TRASPASADO);

        CREATE TABLE IF NOT EXISTS estados_cuenta (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
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
            TASA_CAMBIO     REAL   -- CLP per USD, derived when traspaso is reconciled
        );

        CREATE TABLE IF NOT EXISTS archivos_procesados (
            nombre          TEXT PRIMARY KEY,
            fecha_procesado TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )

    # Lightweight migrations for pre-existing DBs (ignore if column already exists)
    for table, col, decl in (
        ("transacciones",  "MONTO_CLP",   "REAL"),
        ("estados_cuenta", "TASA_CAMBIO", "REAL"),
    ):
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl};")
        except sqlite3.OperationalError:
            pass

    conn.commit()
    return conn


# ------------------------------------------------------------
# Processed-file dedup
# ------------------------------------------------------------
def archivo_ya_procesado(conn: sqlite3.Connection, filename: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM archivos_procesados WHERE nombre = ? LIMIT 1", (filename,)
    )
    return cur.fetchone() is not None


def registrar_archivo_procesado(conn: sqlite3.Connection, filename: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO archivos_procesados(nombre) VALUES (?)", (filename,)
    )
    conn.commit()


# ------------------------------------------------------------
# Transactions
# ------------------------------------------------------------
def insertar_transacciones(conn: sqlite3.Connection, rows: Iterable[Dict[str, Any]]) -> int:
    rows = list(rows)
    if not rows:
        return 0

    placeholders = ", ".join(["?"] * len(TRANSACCIONES_COLS))
    col_list = ", ".join(TRANSACCIONES_COLS)
    conn.executemany(
        f"INSERT INTO transacciones ({col_list}) VALUES ({placeholders});",
        [
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
                r.get("MONEDA", ""),
                r.get("TIPO_GASTO", ""),
                int(r.get("CONCILIADO") or 0),
                int(r.get("FACT_KAME") or 0),
                int(r.get("TRASPASADO") or 0),
                r.get("ARCHIVO_ORIGEN", ""),
            )
            for r in rows
        ],
    )
    conn.commit()
    return len(rows)


def fetch_transacciones(
    conn: sqlite3.Connection, origen: Optional[str] = None
) -> Tuple[List[str], List[tuple]]:
    """Return (cols, rows) with rowid exposed as _RID_. Filter by ORIGEN if given."""
    # FECHA_OPERACION is MM/DD/YY — reformat to YYMMDD for correct chronological text sort
    sort_expr = (
        "substr(FECHA_OPERACION,7,2)||substr(FECHA_OPERACION,1,2)||substr(FECHA_OPERACION,4,2)"
    )
    if origen:
        cur = conn.execute(
            f"SELECT rowid AS _RID_, * FROM transacciones WHERE ORIGEN = ? ORDER BY {sort_expr}",
            (origen,),
        )
    else:
        cur = conn.execute(
            f"SELECT rowid AS _RID_, * FROM transacciones ORDER BY {sort_expr}"
        )
    cols = [d[0] for d in cur.description]
    return cols, cur.fetchall()


def update_clasificacion(conn: sqlite3.Connection, updates: List[Dict[str, Any]]) -> None:
    """Update only the user-editable classification fields (TIPO_GASTO, CONCILIADO).
    Never touches FACT_KAME — use marcar_fact_kame for that."""
    if not updates:
        return
    conn.executemany(
        "UPDATE transacciones SET TIPO_GASTO = ?, CONCILIADO = ? WHERE rowid = ?;",
        [
            (
                (u.get("TIPO_GASTO") or ""),
                int(bool(u.get("CONCILIADO"))),
                int(u["_RID_"]),
            )
            for u in updates
        ],
    )
    conn.commit()


def marcar_fact_kame(conn: sqlite3.Connection, rowids: List[int]) -> None:
    if not rowids:
        return
    conn.executemany(
        "UPDATE transacciones SET FACT_KAME = 1 WHERE rowid = ?;",
        [(int(r),) for r in rowids],
    )
    conn.commit()


# ------------------------------------------------------------
# Statements (estados_cuenta) + traspaso reconciliation
# ------------------------------------------------------------
def upsert_estado_cuenta(conn: sqlite3.Connection, meta: Dict[str, Any]) -> None:
    """Insert a statement record; ignored if ARCHIVO_ORIGEN already exists."""
    if not meta.get("ARCHIVO_ORIGEN"):
        return
    conn.execute(
        """
        INSERT OR IGNORE INTO estados_cuenta
            (ORIGEN, TITULAR_NOMBRE, ARCHIVO_ORIGEN, FECHA_ESTADO,
             PERIODO_DESDE, PERIODO_HASTA, DEUDA_TOTAL, MONEDA)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?);
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
    conn: sqlite3.Connection, origen: Optional[str] = None
) -> Tuple[List[str], List[tuple]]:
    if origen:
        cur = conn.execute(
            "SELECT * FROM estados_cuenta WHERE ORIGEN = ? ORDER BY FECHA_ESTADO", (origen,)
        )
    else:
        cur = conn.execute("SELECT * FROM estados_cuenta ORDER BY ORIGEN, FECHA_ESTADO")
    cols = [d[0] for d in cur.description]
    return cols, cur.fetchall()


def marcar_traspaso(
    conn: sqlite3.Connection,
    estado_id: int,
    match_rid: Optional[int],
    match_archivo: Optional[str],
) -> None:
    """Mark an international statement as transferred; flags its transactions
    TRASPASADO=1 and back-fills MONTO_CLP using the bank's blended exchange rate
    (national traspaso CLP ÷ international DEUDA TOTAL USD)."""
    row = conn.execute(
        "SELECT ARCHIVO_ORIGEN, DEUDA_TOTAL FROM estados_cuenta WHERE id = ?", (estado_id,)
    ).fetchone()

    # Derive the exchange rate from the matched national CLP line
    tasa = None
    if match_rid is not None and row and row[1]:
        clp_row = conn.execute(
            "SELECT MONTO_TOTAL FROM transacciones WHERE rowid = ?", (int(match_rid),)
        ).fetchone()
        deuda_usd = row[1]
        if clp_row and clp_row[0] and deuda_usd:
            try:
                tasa = abs(float(clp_row[0])) / abs(float(deuda_usd))
            except (ZeroDivisionError, ValueError):
                tasa = None

    conn.execute(
        """
        UPDATE estados_cuenta
        SET TRASPASO_ESTADO = 'TRASPASADO', MATCH_RID = ?, MATCH_ARCHIVO = ?, TASA_CAMBIO = ?
        WHERE id = ?;
        """,
        (match_rid, match_archivo, tasa, int(estado_id)),
    )
    if row and row[0]:
        if tasa is not None:
            conn.execute(
                """
                UPDATE transacciones
                SET TRASPASADO = 1, MONTO_CLP = ROUND(MONTO_OPERACION * ?)
                WHERE ARCHIVO_ORIGEN = ?;
                """,
                (tasa, row[0]),
            )
        else:
            conn.execute(
                "UPDATE transacciones SET TRASPASADO = 1 WHERE ARCHIVO_ORIGEN = ?;", (row[0],)
            )
    conn.commit()


def desmarcar_traspaso(conn: sqlite3.Connection, estado_id: int) -> None:
    row = conn.execute(
        "SELECT ARCHIVO_ORIGEN FROM estados_cuenta WHERE id = ?", (estado_id,)
    ).fetchone()
    conn.execute(
        """
        UPDATE estados_cuenta
        SET TRASPASO_ESTADO = 'PENDIENTE', MATCH_RID = NULL, MATCH_ARCHIVO = NULL, TASA_CAMBIO = NULL
        WHERE id = ?;
        """,
        (int(estado_id),),
    )
    if row and row[0]:
        conn.execute(
            "UPDATE transacciones SET TRASPASADO = 0, MONTO_CLP = NULL WHERE ARCHIVO_ORIGEN = ?;",
            (row[0],),
        )
    conn.commit()


def fetch_traspaso_nacional_disponibles(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """National TRASPASO DEUDA INTERNACIONAL lines not yet linked to any statement."""
    cur = conn.execute(
        """
        SELECT t.rowid AS rid, t.FECHA_OPERACION AS fecha,
               t.MONTO_TOTAL AS clp, t.ARCHIVO_ORIGEN AS archivo
        FROM transacciones t
        WHERE t.ORIGEN = 'NACIONAL'
          AND UPPER(t.DESCRIPCION) LIKE '%TRASPASO DEUDA INTERNAC%'
          AND t.rowid NOT IN (
              SELECT MATCH_RID FROM estados_cuenta WHERE MATCH_RID IS NOT NULL
          )
        ORDER BY substr(t.FECHA_OPERACION,7,2)||substr(t.FECHA_OPERACION,1,2)||substr(t.FECHA_OPERACION,4,2)
        """
    )
    return [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]


def fetch_estados_intl_pendientes(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """International statements not yet reconciled to a national traspaso line."""
    cur = conn.execute(
        """
        SELECT ec.id AS id, ec.ARCHIVO_ORIGEN AS archivo, ec.TITULAR_NOMBRE AS titular,
               ec.DEUDA_TOTAL AS deuda, ec.PERIODO_DESDE AS desde, ec.PERIODO_HASTA AS hasta
        FROM estados_cuenta ec
        WHERE ec.ORIGEN = 'INTERNACIONAL' AND ec.TRASPASO_ESTADO != 'TRASPASADO'
        """
    )
    return [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]


def fetch_traspaso_suggestions(
    conn: sqlite3.Connection,
) -> Tuple[Dict[int, Dict[str, Any]], set]:
    """Suggest, for each pending international statement, which national TRASPASO
    line settles it.

    Matching chain (the bank's real flow):
        national line (date D, CLP)
          -> international credit line dated D with |USD| == statement DEUDA TOTAL
          -> that international statement (whose debt is being paid)

    The credit line appears in the *next* statement, so its amount — not its host
    statement — identifies which debt it pays.

    Returns (suggestions, ambiguous):
        suggestions = {estado_id: {"rid", "archivo", "clp", "tasa"}}
        ambiguous   = {estado_id, ...} that had more than one candidate
    """
    nac = fetch_traspaso_nacional_disponibles(conn)
    nac_by_date: Dict[str, List[Dict[str, Any]]] = {}
    for n in nac:
        nac_by_date.setdefault(n["fecha"], []).append(n)

    # International credit/traspaso lines: (date, |USD|)
    cur = conn.execute(
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
        # dates of credit lines whose magnitude equals this statement's deuda
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


def auto_match_traspasos(conn: sqlite3.Connection) -> int:
    """Apply every unambiguous traspaso suggestion (see fetch_traspaso_suggestions).
    Applies one match at a time and recomputes, since each match consumes a national
    line and may resolve a previously-ambiguous case. Returns matches applied."""
    applied = 0
    for _ in range(1000):  # guard against any pathological loop
        suggestions, _amb = fetch_traspaso_suggestions(conn)
        if not suggestions:
            break
        est_id, s = next(iter(suggestions.items()))
        marcar_traspaso(conn, est_id, s["rid"], s["archivo"])
        applied += 1
    return applied


# ------------------------------------------------------------
# Auto-categorization
# ------------------------------------------------------------

# Well-known BCI descriptions that always map to the same TIPO_GASTO.
# Matched case-insensitively as a substring of DESCRIPCION.
STATIC_TIPO_GASTO_NAC: list[tuple[str, str]] = [
    ("COMISION COMPRA INTERNACIONAL", "Comision Intl"),
    ("IMPUESTO DECRETO LEY",          "Impuesto"),
    ("COBRO ADM MENSUAL",             "Comision Nacional"),
    ("INTERESES ROTATIVOS",           "Comision Nacional"),
    ("TRASPASO DEUDA INTERNACIONAL",  "Tr Deuda Intl"),
    ("PAGO PAC EN PESOS",             "BCI Paga TC"),
]

STATIC_TIPO_GASTO_INTL: list[tuple[str, str]] = [
    ("HUBSPOT",                       "Hubspot"),
    ("GOOGLE *WORKSPACE",             "GSuite"),
    ("GOOGLE *",                      "Google"),
    ("GODADDY",                       "GSuite"),
    ("FACEBK",                        "Marketing"),
    ("AIRBNB",                        "Airbnb"),
    ("SHUTTERSTOCK",                  "Shutterstock"),
    ("CANVA",                         "Canva"),
    ("TRASPASO DEUDA INTERNAC",       "Trp a Deuda Nacional"),
    ("UBER",                          "Huber"),
]


def fetch_tipo_gasto_map(conn: sqlite3.Connection) -> dict[str, str]:
    """Return {DESCRIPCION: TIPO_GASTO} using the most recently saved categorization
    per description (highest rowid with a non-empty TIPO_GASTO)."""
    cur = conn.execute(
        """
        SELECT DESCRIPCION, TIPO_GASTO
        FROM transacciones
        WHERE TIPO_GASTO IS NOT NULL AND TIPO_GASTO != ''
          AND rowid IN (
              SELECT MAX(rowid)
              FROM transacciones
              WHERE TIPO_GASTO IS NOT NULL AND TIPO_GASTO != ''
              GROUP BY DESCRIPCION
          )
        """
    )
    return {row[0]: row[1] for row in cur.fetchall()}


def auto_tipo_gasto(descripcion: str, historic_map: dict[str, str], origen: str = "") -> str:
    """Return the best-guess TIPO_GASTO for a description, or '' if unknown.

    Priority: 1) exact history match  2) static rules for the given origen.
    """
    if descripcion in historic_map:
        return historic_map[descripcion]

    desc_upper = descripcion.upper()
    rules = STATIC_TIPO_GASTO_INTL if origen == "INTERNACIONAL" else STATIC_TIPO_GASTO_NAC
    for keyword, tipo in rules:
        if keyword in desc_upper:
            return tipo

    return ""


def propagar_clasificacion(conn: sqlite3.Connection, updates: list[dict]) -> None:
    """After saving edits, propagate each TIPO_GASTO change to all other pending
    rows with the same DESCRIPCION so future uploads stay consistent."""
    for u in updates:
        tipo = u.get("TIPO_GASTO") or ""
        if not tipo:
            continue
        conn.execute(
            """
            UPDATE transacciones
            SET TIPO_GASTO = ?
            WHERE DESCRIPCION = (SELECT DESCRIPCION FROM transacciones WHERE rowid = ?)
              AND FACT_KAME = 0
              AND (TIPO_GASTO IS NULL OR TIPO_GASTO = '' OR TIPO_GASTO != ?)
            """,
            (tipo, int(u["_RID_"]), tipo),
        )
    conn.commit()


# ------------------------------------------------------------
# Uploaded-files summary (for dashboard)
# ------------------------------------------------------------
def fetch_archivos_resumen(conn: sqlite3.Connection) -> Tuple[List[str], List[tuple]]:
    """One row per uploaded statement with metadata and transaction count."""
    cur = conn.execute(
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
            -- FECHA_ESTADO is DD-MM-YYYY; reformat to YYYYMMDD for correct sort
            substr(ec.FECHA_ESTADO,7,4)||substr(ec.FECHA_ESTADO,4,2)||substr(ec.FECHA_ESTADO,1,2) DESC
        """
    )
    cols = [d[0] for d in cur.description]
    return cols, cur.fetchall()


# ------------------------------------------------------------
# Admin
# ------------------------------------------------------------
def reset_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DELETE FROM transacciones;
        DELETE FROM estados_cuenta;
        DELETE FROM archivos_procesados;
        VACUUM;
        """
    )
