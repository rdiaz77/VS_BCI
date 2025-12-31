import sqlite3
import pandas as pd
from datetime import datetime

def _transformar_fecha_ddmm_a_mmdd(fecha: str):
    """Convierte fecha DD/MM/YY(YY) -> MM/DD/YY(YY). Si no calza, devuelve original."""
    if not fecha or not isinstance(fecha, str):
        return fecha
    s = fecha.strip()

    for fmt_in, fmt_out in (("%d/%m/%y", "%m/%d/%y"), ("%d/%m/%Y", "%m/%d/%Y")):
        try:
            dt = datetime.strptime(s, fmt_in)
            return dt.strftime(fmt_out)
        except Exception:
            continue

    return fecha

def _transformar_fecha(fecha: str):
    return _transformar_fecha_ddmm_a_mmdd(fecha)

def migrar_fechas_a_mmddyyyy(conn):
    """
    Migra FECHA_OPERACION existente en la tabla transacciones desde DD/MM/YY(YY) a MM/DD/YY(YY).
    Usa rowid para actualizar sin depender de una columna id.
    """
    try:
        rows = conn.execute("SELECT rowid, FECHA_OPERACION FROM transacciones").fetchall()
    except Exception:
        return

    updates = []
    for rowid, old in rows:
        new = _transformar_fecha_ddmm_a_mmdd(old)
        if new != old:
            updates.append((new, rowid))

    if updates:
        conn.executemany("UPDATE transacciones SET FECHA_OPERACION = ? WHERE rowid = ?", updates)
        conn.commit()

def init_db(db_path: str):
    """Crea (si no existe) y conecta a la base de datos SQLite."""
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transacciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            FECHA_OPERACION TEXT,
            DESCRIPCION TEXT,
            MONTO_OPERACION INTEGER,
            MONTO_TOTAL INTEGER,
            TIPO_GASTO TEXT,
            FACT_KAME INTEGER DEFAULT 0,
            ARCHIVO_ORIGEN TEXT,
            CONCILIADO INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS archivos_procesados (
            nombre TEXT PRIMARY KEY,
            fecha_procesado TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn


def insertar_en_db(conn, rows):
    """Inserta múltiples transacciones en la base de datos."""
    if not rows:
        return
    conn.executemany("""
        INSERT INTO transacciones
        (FECHA_OPERACION, DESCRIPCION, MONTO_OPERACION, MONTO_TOTAL, TIPO_GASTO, FACT_KAME, ARCHIVO_ORIGEN)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, [
        (
            _transformar_fecha(row.get("FECHA_OPERACION")),
            row["DESCRIPCION"],
            row["MONTO_OPERACION"],
            row["MONTO_TOTAL"],
            row.get("TIPO_GASTO"),
            int(row.get("FACT_KAME", 0)),
            row["ARCHIVO_ORIGEN"]
        )
        for row in rows
    ])
    conn.commit()


def leer_todo_db(conn):
    """Lee todas las transacciones de la base de datos."""
    return pd.read_sql_query(
        "SELECT FECHA_OPERACION, DESCRIPCION, MONTO_OPERACION, MONTO_TOTAL, TIPO_GASTO, FACT_KAME, ARCHIVO_ORIGEN, CONCILIADO "
        "FROM transacciones ORDER BY FECHA_OPERACION",
        conn
    )


def archivo_ya_procesado(conn, filename: str) -> bool:
    """Verifica si el archivo ya fue procesado previamente."""
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM archivos_procesados WHERE nombre = ?", (filename,))
    return cur.fetchone() is not None


def registrar_archivo_procesado(conn, filename: str):
    """Registra un archivo como procesado."""
    conn.execute(
        "INSERT OR IGNORE INTO archivos_procesados (nombre) VALUES (?)", (filename,))
    conn.commit()
# === FIN data/database.py ===
