import os
import re
import csv
import pandas as pd
import streamlit as st
import platform
from io import BytesIO
from dashboard import show_dashboard

# === IMPORTS FROM NEW MODULES ===
from data.extractor import leer_cartola
from data.database import (
    init_db,
    insertar_en_db,
    leer_todo_db,
    archivo_ya_procesado,
    registrar_archivo_procesado,
)

# === SIMPLE PASSWORD PROTECTION USING SECRETS ===
def check_password():
    """Prompt for password and stop app execution until the correct one is entered."""

    def password_entered():
        """Check the entered password and update session state."""
        if st.session_state["password"] == st.secrets["app_password"]:
            st.session_state["password_correct"] = True
            del st.session_state["password"]  # Remove password from memory
        else:
            st.session_state["password_correct"] = False

    # First-time password check
    if "password_correct" not in st.session_state:
        st.text_input(
            "🔐 Ingresa la contraseña para acceder:",
            type="password",
            on_change=password_entered,
            key="password",
        )
        st.stop()

    # If password is incorrect
    elif not st.session_state["password_correct"]:
        st.text_input(
            "🔐 Ingresa la contraseña para acceder:",
            type="password",
            on_change=password_entered,
            key="password",
        )
        st.error("❌ Contraseña incorrecta")
        st.stop()


# Run password check before loading the rest of the app
check_password()

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
    persistent_dir = "/mount"  # Fallback if /mount is writable
else:
    persistent_dir = base_path or "."  # Local mode on macOS or elsewhere

# Ensure directory exists
os.makedirs(persistent_dir, exist_ok=True)

db_path = os.path.join(persistent_dir, "cartolas_bci.db")
st.write(f"📁 Base de datos en uso: `{db_path}`")

# === INICIAR DB ===
conn = init_db(db_path)

# Try adding CONCILIADO column if missing
import sqlite3
try:
    conn.execute(
        "ALTER TABLE transacciones ADD COLUMN CONCILIADO INTEGER DEFAULT 0;"
    )
    conn.commit()
except sqlite3.OperationalError:
    pass  # Column already exists

# === SUBIR O PROCESAR PDF ===
uploaded_files = st.file_uploader(
    "📤 Sube tus cartolas en PDF (puedes arrastrarlas aquí):",
    type=["pdf"],
    accept_multiple_files=True,
)

if uploaded_files:
    all_data = []
    st.info(f"Procesando {len(uploaded_files)} archivo(s)...")
    for uploaded_file in uploaded_files:
        if archivo_ya_procesado(conn, uploaded_file.name):
            st.warning(
                f"⚠️ El archivo {uploaded_file.name} ya fue procesado anteriormente. Se omitirá."
            )
            continue

        pdf_bytes = BytesIO(uploaded_file.read())
        rows = leer_cartola(pdf_bytes, uploaded_file.name)
        if not rows:
            st.warning(f"⚠️ No se encontraron transacciones en {uploaded_file.name}")
            continue
        insertar_en_db(conn, rows)
        registrar_archivo_procesado(conn, uploaded_file.name)
        all_data.extend(rows)
        st.success(
            f"✅ {len(rows)} transacciones extraídas y guardadas desde {uploaded_file.name}"
        )

    if all_data:
        df = pd.DataFrame(all_data)
        st.dataframe(df, use_container_width=True)
        csv_output = df.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="💾 Descargar CSV generado (sesión actual)",
            data=csv_output,
            file_name="cartolas_bci_extraidas.csv",
            mime="text/csv",
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
                                f"⚠️ El archivo {fname} ya fue procesado anteriormente. Se omitirá."
                            )
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
                f"✅ {len(all_data)} nuevas transacciones guardadas en la base de datos."
            )
            df = pd.DataFrame(all_data)
            st.dataframe(df, use_container_width=True)
            csv_output = df.to_csv(index=False).encode("utf-8")
            st.download_button(
                label="💾 Descargar CSV generado (sesión actual)",
                data=csv_output,
                file_name="cartolas_bci_locales.csv",
                mime="text/csv",
            )
else:
    st.info("Sube tus archivos PDF para comenzar.")

# === DESCARGAR HISTORIAL DE LA BASE DE DATOS ===
st.subheader("📦 Transacciones almacenadas en base de datos")
df_db = leer_todo_db(conn)

if not df_db.empty:
    tab1, tab2 = st.tabs(["📄 Datos", "📈 Analytics"])

    # --- TAB 1: Datos + Conciliación ---
    with tab1:
        df_view = df_db.copy()
        df_view["CONCILIADO"] = df_view["CONCILIADO"].astype(bool)
        edited_df = st.data_editor(
            df_view,
            use_container_width=True,
            hide_index=True,
            key="editable_df",
            column_config={
                "CONCILIADO": st.column_config.CheckboxColumn("✅ Conciliado", default=False)
            },
        )

        if st.button("💾 Guardar cambios de conciliación"):
            for _, row in edited_df.iterrows():
                conn.execute(
                    """
                    UPDATE transacciones
                    SET CONCILIADO = ?
                    WHERE FECHA_OPERACION = ? AND DESCRIPCION = ? AND MONTO_OPERACION = ?
                    """,
                    (
                        1 if row["CONCILIADO"] else 0,
                        row["FECHA_OPERACION"],
                        row["DESCRIPCION"],
                        row["MONTO_OPERACION"],
                    ),
                )
            conn.commit()
            st.success("✅ Cambios de conciliación guardados correctamente.")

        csv_data = df_db.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="💾 Descargar TODAS las transacciones (historial completo)",
            data=csv_data,
            file_name="cartolas_bci_db.csv",
            mime="text/csv",
        )

    # --- TAB 2: Dashboard ---
    with tab2:
        show_dashboard(df_db)
else:
    st.info("No hay transacciones almacenadas aún en la base de datos.")

# === 🔁 RESET DATABASE BUTTON ===
st.markdown("---")
st.subheader("⚙️ Administración de la base de datos")

with st.expander("🧹 Borrar todo el historial de transacciones"):
    st.warning(
        "Esta acción eliminará *todas las transacciones y registros de archivos procesados* de la base de datos (no se eliminará el archivo DB)."
    )
    confirm = st.checkbox("Confirmo que deseo borrar todo el historial")
    if st.button("🗑️ Resetear base de datos"):
        if confirm:
            conn.execute("DELETE FROM transacciones")
            conn.execute("DELETE FROM archivos_procesados")
            conn.commit()
            st.success(
                "✅ Base de datos vaciada correctamente. Recarga la página para actualizar la vista."
            )
        else:
            st.info("☑️ Marca la casilla de confirmación antes de resetear.")

conn.close()
# === END OF FILE ===
