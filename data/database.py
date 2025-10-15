import sqlite3
import pandas as pd


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
        (FECHA_OPERACION, DESCRIPCION, MONTO_OPERACION, MONTO_TOTAL, ARCHIVO_ORIGEN)
        VALUES (?, ?, ?, ?, ?)
    """, [
        (
            row["FECHA_OPERACION"],
            row["DESCRIPCION"],
            row["MONTO_OPERACION"],
            row["MONTO_TOTAL"],
            row["ARCHIVO_ORIGEN"]
        )
        for row in rows
    ])
    conn.commit()


def leer_todo_db(conn):
    """Lee todas las transacciones de la base de datos."""
    return pd.read_sql_query(
        "SELECT FECHA_OPERACION, DESCRIPCION, MONTO_OPERACION, MONTO_TOTAL, ARCHIVO_ORIGEN, CONCILIADO "
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
