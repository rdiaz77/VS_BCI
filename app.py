import os
import re
import csv
import pdfplumber
import pandas as pd
import streamlit as st
import sqlite3
from io import BytesIO
import platform

# === PAGE CONFIGURATION ===
st.set_page_config(page_title="Cartolas BCI Extractor", layout="wide")

# === APP TITLE ===
st.title("📊 Cartolas BCI Extractor con Base de Datos (SQLite)")
st.write(
    "Analiza tus cartolas de tarjeta de crédito BCI. "
    "Los datos extraídos se guardan en una base de datos local (SQLite) para conservar el historial."
)

# === CONFIGURACIÓN LOCAL (segura para Cloud y Mac) ===
try:
    is_local_mac = platform.system() == "Darwin" and os.path.exists("/Users")
except Exception:
    is_local_mac = False

if is_local_mac:
    base_path = st.text_input(
        "📂 Ruta base local de las cartolas",
        "/Users/rafaeldiaz/Desktop/Python_Kame_ERP/VS_BCI/cartolas",
    )
else:
    base_path = None

# === CONFIGURAR DIRECTORIO PERSISTENTE (CLOUD o LOCAL) ===
if os.access("/mount/src", os.W_OK):
    persistent_dir = "/mount/src/vs_bci"  # Writable inside Streamlit Cloud
elif os.path.exists("/mount") and os.access("/mount", os.W_OK):
    persistent_dir = "/mount"              # Fallback if /mount is writable
else:
    persistent_dir = base_path or "."      # Local mode on macOS or elsewhere

# Ensure directory exists
os.makedirs(persistent_dir, exist_ok=True)

db_path = os.path.join(persistent_dir, "cartolas_bci.db")
st.write(f"📁 Base de datos en uso: `{db_path}`")

# === REGEX ORIGINAL (FUNCIONAL) ===
line_pattern = re.compile(
    r"(?P<fecha>\d{2}/\d{2}/\d{2})\s+"
    r"(?:\d{9,}\s+)?"
    r"(?P<desc>.+?)\s+\$\s*(-?\d{1,3}(?:\.\d{3})*)"
    r"\s+\$\s*(-?\d{1,3}(?:\.\d{3})*)"
)

# === FUNCIONES AUXILIARES ===


def normalizar_monto(valor_str):
    valor_str = valor_str.replace(".", "").replace("$", "").strip()
    try:
        return int(valor_str)
    except ValueError:
        return None


def formatear_miles(valor_int):
    if valor_int is None:
        return ""
    return f"${valor_int:,}"

# === LECTURA DE CARTOLA ===


def leer_cartola(file_like, filename="archivo.pdf"):
    """Extrae transacciones desde una cartola PDF (subida o local)."""
    rows = []
    with pdfplumber.open(file_like) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith(("LUGAR", "OPERACIÓN", "TOTAL", "III.", "II.", "I.")):
                    continue
                match = line_pattern.search(line)
                if match:
                    fecha = match.group("fecha")
                    descripcion = re.sub(
                        r"\s{2,}", " ", match.group("desc").strip())
                    monto_op_int = normalizar_monto(match.group(3))
                    monto_total_int = normalizar_monto(match.group(4))
                    rows.append({
                        "FECHA_OPERACION": fecha,
                        "DESCRIPCION": descripcion,
                        "MONTO_OPERACION": monto_op_int,
                        "MONTO_TOTAL": monto_total_int,
                        "ARCHIVO_ORIGEN": filename
                    })
    return rows

# === BASE DE DATOS (SQLite) ===


def init_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transacciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            FECHA_OPERACION TEXT,
            DESCRIPCION TEXT,
            MONTO_OPERACION INTEGER,
            MONTO_TOTAL INTEGER,
            ARCHIVO_ORIGEN TEXT
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
    return pd.read_sql_query(
        "SELECT FECHA_OPERACION, DESCRIPCION, MONTO_OPERACION, MONTO_TOTAL, ARCHIVO_ORIGEN FROM transacciones ORDER BY FECHA_OPERACION",
        conn
    )


def archivo_ya_procesado(conn, filename):
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM archivos_procesados WHERE nombre = ?", (filename,))
    return cur.fetchone() is not None


def registrar_archivo_procesado(conn, filename):
    conn.execute(
        "INSERT OR IGNORE INTO archivos_procesados (nombre) VALUES (?)", (filename,))
    conn.commit()


# === INICIAR DB ===
conn = init_db(db_path)

# === SUBIR O PROCESAR PDF ===
uploaded_files = st.file_uploader(
    "📤 Sube tus cartolas en PDF (puedes arrastrarlas aquí):",
    type=["pdf"],
    accept_multiple_files=True
)

if uploaded_files:
    all_data = []
    st.info(f"Procesando {len(uploaded_files)} archivo(s)...")
    for uploaded_file in uploaded_files:
        if archivo_ya_procesado(conn, uploaded_file.name):
            st.warning(
                f"⚠️ El archivo {uploaded_file.name} ya fue procesado anteriormente. Se omitirá.")
            continue

        pdf_bytes = BytesIO(uploaded_file.read())
        rows = leer_cartola(pdf_bytes, uploaded_file.name)
        if not rows:
            st.warning(
                f"⚠️ No se encontraron transacciones en {uploaded_file.name}")
            continue
        insertar_en_db(conn, rows)
        registrar_archivo_procesado(conn, uploaded_file.name)
        all_data.extend(rows)
        st.success(
            f"✅ {len(rows)} transacciones extraídas y guardadas desde {uploaded_file.name}")

    if all_data:
        df = pd.DataFrame(all_data)
        st.dataframe(df, use_container_width=True)
        csv_output = df.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="💾 Descargar CSV generado (sesión actual)",
            data=csv_output,
            file_name="cartolas_bci_extraidas.csv",
            mime="text/csv"
        )

elif base_path and st.button("▶️ Procesar cartolas locales"):
    if not os.path.exists(base_path):
        st.error("❌ La ruta ingresada no existe.")
    else:
        all_data = []
        with st.spinner("Procesando PDFs locales..."):
            for root, _, files in os.walk(base_path):
                for fname in files:
                    if fname.lower().endswith(".pdf"):
                        if archivo_ya_procesado(conn, fname):
                            st.warning(
                                f"⚠️ El archivo {fname} ya fue procesado anteriormente. Se omitirá.")
                            continue
                        full_path = os.path.join(root, fname)
                        with open(full_path, "rb") as f:
                            rows = leer_cartola(f, fname)
                            if rows:
                                insertar_en_db(conn, rows)
                                registrar_archivo_procesado(conn, fname)
                                all_data.extend(rows)
        if not all_data:
            st.warning("⚠️ No se encontraron transacciones nuevas.")
        else:
            st.success(
                f"✅ {len(all_data)} nuevas transacciones guardadas en la base de datos.")
            df = pd.DataFrame(all_data)
            st.dataframe(df, use_container_width=True)
            csv_output = df.to_csv(index=False).encode("utf-8")
            st.download_button(
                label="💾 Descargar CSV generado (sesión actual)",
                data=csv_output,
                file_name="cartolas_bci_locales.csv",
                mime="text/csv"
            )
else:
    st.info("Sube tus archivos PDF para comenzar.")

# === DESCARGAR HISTORIAL DE LA BASE DE DATOS ===
st.subheader("📦 Transacciones almacenadas en base de datos")
df_db = leer_todo_db(conn)

if not df_db.empty:
    df_view = df_db.copy()
    for col in ["MONTO_OPERACION", "MONTO_TOTAL"]:
        df_view[col] = df_view[col].apply(
            lambda x: f"${x:,}" if pd.notnull(x) else "")
    st.dataframe(df_view, use_container_width=True)
    csv_data = df_db.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="💾 Descargar TODAS las transacciones (historial completo)",
        data=csv_data,
        file_name="cartolas_bci_db.csv",
        mime="text/csv"
    )
else:
    st.info("No hay transacciones almacenadas aún en la base de datos.")

# === 🔁 RESET DATABASE BUTTON ===
st.markdown("---")
st.subheader("⚙️ Administración de la base de datos")

with st.expander("🧹 Borrar todo el historial de transacciones"):
    st.warning("Esta acción eliminará *todas las transacciones y registros de archivos procesados* de la base de datos (no se eliminará el archivo DB).")
    confirm = st.checkbox("Confirmo que deseo borrar todo el historial")
    if st.button("🗑️ Resetear base de datos"):
        if confirm:
            conn.execute("DELETE FROM transacciones")
            conn.execute("DELETE FROM archivos_procesados")
            conn.commit()
            st.success(
                "✅ Base de datos vaciada correctamente. Recarga la página para actualizar la vista.")
        else:
            st.info("☑️ Marca la casilla de confirmación antes de resetear.")

conn.close()
# === END OF FILE ===
