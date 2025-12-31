import os
import pandas as pd
import streamlit as st
import platform
import sqlite3
from io import BytesIO
from dashboard import show_dashboard

# === IMPORTS FROM NEW MODULES ===
from data.extractor import leer_cartola
from data.database import (
    init_db,
    insertar_en_db,
    archivo_ya_procesado,
    registrar_archivo_procesado,
    migrar_fechas_a_mmddyyyy,
)

# ============================================================
# PASSWORD GATE
# ============================================================
def check_password():
    """Prompt for password and stop app execution until the correct one is entered."""

    def password_entered():
        if st.session_state["password"] == st.secrets["app_password"]:
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.text_input("🔐 Enter password:", type="password", on_change=password_entered, key="password")
        st.stop()
    elif not st.session_state["password_correct"]:
        st.text_input("🔐 Enter password:", type="password", on_change=password_entered, key="password")
        st.error("❌ Incorrect password")
        st.stop()


check_password()

# ============================================================
# PAGE CONFIG
# ============================================================
st.set_page_config(page_title="Cartolas BCI Extractor", layout="wide")
st.title("📊 Cartolas BCI Extractor (SQLite)")
st.write("Upload BCI credit card statements (PDF). Extracted transactions are stored in SQLite for history.")

# ============================================================
# LOCAL / CLOUD PATHS
# ============================================================
try:
    is_local_mac = platform.system() == "Darwin" and os.path.exists("/Users")
except Exception:
    is_local_mac = False

if is_local_mac:
    base_path = st.text_input(
        "📂 Local base folder for PDFs",
        "/Users/rafaeldiaz/Desktop/Python_Kame_ERP/VS_BCI/cartolas",
    )
else:
    base_path = None

if os.access("/mount/src", os.W_OK):
    persistent_dir = "/mount/src/vs_bci"
elif os.path.exists("/mount") and os.access("/mount", os.W_OK):
    persistent_dir = "/mount"
else:
    persistent_dir = base_path or "."

os.makedirs(persistent_dir, exist_ok=True)
db_path = os.path.join(persistent_dir, "cartolas_bci.db")
st.write(f"📁 DB path: `{db_path}`")

# ============================================================
# DB INIT
# ============================================================
conn = init_db(db_path)

# Add missing columns if needed (minimal, no refactor)
for ddl in [
    "ALTER TABLE transacciones ADD COLUMN CONCILIADO INTEGER DEFAULT 0;",
    "ALTER TABLE transacciones ADD COLUMN TIPO_GASTO TEXT;",
    "ALTER TABLE transacciones ADD COLUMN FACT_KAME INTEGER DEFAULT 0;",
]:
    try:
        conn.execute(ddl)
        conn.commit()
    except sqlite3.OperationalError:
        pass

migrar_fechas_a_mmddyyyy(conn)

# ============================================================
# IMPORTANT: load DB with rowid (no schema change)
# ============================================================
def leer_todo_db_with_rowid(conn_) -> pd.DataFrame:
    # rowid is stable for updates without relying on FECHA/DESCRIPCION/MONTO matching
    return pd.read_sql_query("SELECT rowid AS _RID_, * FROM transacciones", conn_)


# ============================================================
# HELPER: reorder columns
# ============================================================
def move_archivo_origen_to_end(df: pd.DataFrame) -> pd.DataFrame:
    if "ARCHIVO_ORIGEN" not in df.columns:
        return df
    cols = [c for c in df.columns if c != "ARCHIVO_ORIGEN"] + ["ARCHIVO_ORIGEN"]
    return df[cols]


def reorder_pending_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Target order (if columns exist):
      FECHA_OPERACION, DESCRIPCION, MONTO_OPERACION, MONTO_TOTAL, CONCILIADO,
      FACT_KAME (select), TIPO_GASTO,
      ...rest..., ARCHIVO_ORIGEN at end (handled separately)
    """
    cols = list(df.columns)

    def take(name: str):
        if name in cols:
            cols.remove(name)
            return name
        return None

    ordered = []
    for c in ["FECHA_OPERACION", "DESCRIPCION", "MONTO_OPERACION", "MONTO_TOTAL", "CONCILIADO"]:
        x = take(c)
        if x:
            ordered.append(x)

    # Place selection checkbox between CONCILIADO and TIPO_GASTO
    x = take("FACT_KAME")
    if x:
        ordered.append(x)

    x = take("TIPO_GASTO")
    if x:
        ordered.append(x)

    # remaining columns follow
    ordered.extend(cols)
    return df[ordered]


# ============================================================
# DB LOAD CONTROL (prevents editor resets)
# ============================================================
if "db_dirty" not in st.session_state:
    st.session_state["db_dirty"] = True  # first load


# ============================================================
# PDF UPLOAD / PROCESS
# ============================================================
uploaded_files = st.file_uploader(
    "📤 Upload PDF statements:",
    type=["pdf"],
    accept_multiple_files=True,
)

excluir_terms_raw = st.text_input("🚫 Exclude terms in DESCRIPCION (comma separated)", value="")
excluir_terms = [t.strip().lower() for t in excluir_terms_raw.split(",") if t.strip()]

if uploaded_files:
    all_data = []
    st.info(f"Processing {len(uploaded_files)} file(s)...")

    for uploaded_file in uploaded_files:
        if archivo_ya_procesado(conn, uploaded_file.name):
            st.warning(f"⚠️ {uploaded_file.name} was already processed. Skipping.")
            continue

        pdf_bytes = BytesIO(uploaded_file.read())
        rows = leer_cartola(pdf_bytes, uploaded_file.name)

        if excluir_terms:
            rows = [
                r for r in rows
                if not any(t in str(r.get("DESCRIPCION", "")).lower() for t in excluir_terms)
            ]

        if not rows:
            st.warning(f"⚠️ No transactions found in {uploaded_file.name}")
            continue

        insertar_en_db(conn, rows)
        registrar_archivo_procesado(conn, uploaded_file.name)
        all_data.extend(rows)
        st.success(f"✅ Saved {len(rows)} transactions from {uploaded_file.name}")

    if all_data:
        # Mark DB dirty so next run reloads once
        st.session_state["db_dirty"] = True

        df = pd.DataFrame(all_data)
        st.dataframe(df, use_container_width=True)
        st.download_button(
            label="💾 Download session CSV",
            data=df.to_csv(index=False).encode("utf-8"),
            file_name="cartolas_bci_extraidas.csv",
            mime="text/csv",
        )

elif base_path and st.button("▶️ Process local PDFs"):
    if not os.path.exists(base_path):
        st.error("❌ Base path does not exist.")
    else:
        all_data = []
        with st.spinner("Processing local PDFs..."):
            for root, _, files in os.walk(base_path):
                for fname in files:
                    if not fname.lower().endswith(".pdf"):
                        continue
                    if archivo_ya_procesado(conn, fname):
                        st.warning(f"⚠️ {fname} already processed. Skipping.")
                        continue
                    full_path = os.path.join(root, fname)
                    with open(full_path, "rb") as f:
                        rows = leer_cartola(f, fname)
                        if rows:
                            insertar_en_db(conn, rows)
                            registrar_archivo_procesado(conn, fname)
                            all_data.extend(rows)

        if not all_data:
            st.warning("⚠️ No new transactions found.")
        else:
            st.success(f"✅ Saved {len(all_data)} new transactions.")
            st.session_state["db_dirty"] = True

            df = pd.DataFrame(all_data)
            st.dataframe(df, use_container_width=True)
            st.download_button(
                label="💾 Download session CSV",
                data=df.to_csv(index=False).encode("utf-8"),
                file_name="cartolas_bci_locales.csv",
                mime="text/csv",
            )
else:
    st.info("Upload PDF files to begin.")


# ============================================================
# HISTORY (DB) + STABLE EDITING
# ============================================================
st.subheader("📦 Transactions stored in DB")

if st.button("🔄 Refresh from DB"):
    st.session_state["db_dirty"] = True

if st.session_state.get("db_dirty", True) or ("df_db" not in st.session_state):
    st.session_state["df_db"] = leer_todo_db_with_rowid(conn)
    st.session_state["db_dirty"] = False

df_db = st.session_state["df_db"]


if not df_db.empty:
    # TITULAR derived column (kept as you had it)
    if "TITULAR" not in df_db.columns and "ARCHIVO_ORIGEN" in df_db.columns:
        df_db["TITULAR"] = (
            df_db["ARCHIVO_ORIGEN"]
            .str.extract(r"BCI_([A-Za-zÁÉÍÓÚÑ_]+)_")
            .iloc[:, 0]
            .str.replace("_", " ")
            .str.title()
        )

    titulares = sorted(df_db["TITULAR"].dropna().unique()) if "TITULAR" in df_db.columns else []
    titular_seleccionado = st.selectbox("👤 Filter by cardholder", ["All"] + titulares, index=0)

    if titular_seleccionado != "All" and "TITULAR" in df_db.columns:
        df_db_f = df_db[df_db["TITULAR"] == titular_seleccionado].copy()
    else:
        df_db_f = df_db.copy()

    tab1, tab2 = st.tabs(["📄 Data", "📈 Analytics"])

    with tab1:
        df_view = df_db_f.copy()

        # Normalize types for UI
        if "CONCILIADO" in df_view.columns:
            df_view["CONCILIADO"] = df_view["CONCILIADO"].astype(int).astype(bool)
        else:
            df_view["CONCILIADO"] = False

        if "FACT_KAME" in df_view.columns:
            df_view["FACT_KAME"] = df_view["FACT_KAME"].astype(int).astype(bool)
        else:
            df_view["FACT_KAME"] = False

        if "TIPO_GASTO" in df_view.columns:
            df_view["TIPO_GASTO"] = df_view["TIPO_GASTO"].fillna("").astype(str)
        else:
            df_view["TIPO_GASTO"] = ""

        # Split by DB FACT_KAME ONLY (DB truth)
        df_pendiente = df_view[df_view["FACT_KAME"] == False].copy()
        df_ingresado = df_view[df_view["FACT_KAME"] == True].copy()

        # Deterministic order (prevents row shuffling)
        sort_cols = [c for c in ["FECHA_OPERACION", "DESCRIPCION", "MONTO_OPERACION"] if c in df_pendiente.columns]
        if sort_cols:
            df_pendiente = df_pendiente.sort_values(by=sort_cols, kind="mergesort").reset_index(drop=True)

        # --- Selection persistence for FACT_KAME checkbox (UI selection) ---
        if "fact_sel" not in st.session_state:
            st.session_state["fact_sel"] = {}

        # Inject selection state from session map keyed by _RID_
        df_pendiente["_SEL_FACT_"] = df_pendiente["_RID_"].map(st.session_state["fact_sel"]).fillna(False).astype(bool)

        # Preserve DB FACT_KAME internally, and use UI checkbox as FACT_KAME
        df_pendiente = df_pendiente.rename(columns={"FACT_KAME": "_FACT_KAME_DB"})
        df_pendiente["FACT_KAME"] = df_pendiente["_SEL_FACT_"]

        # Move ARCHIVO_ORIGEN to end (both views)
        df_pendiente = move_archivo_origen_to_end(df_pendiente)
        df_ingresado = move_archivo_origen_to_end(df_ingresado)

        # Reorder pending columns so FACT_KAME is between CONCILIADO and TIPO_GASTO
        df_pendiente = reorder_pending_columns(df_pendiente)

        # Buffer context: reset buffer only if filter changes
        buffer_context = f"pending::{titular_seleccionado}"
        if st.session_state.get("pending_buffer_ctx") != buffer_context:
            st.session_state["pending_buffer_ctx"] = buffer_context
            st.session_state["pending_buffer"] = df_pendiente.copy()

        if "pending_buffer" not in st.session_state:
            st.session_state["pending_buffer"] = df_pendiente.copy()

        TIPO_GASTO_OPTS = [
            "Alimentacion",
            "Alojamiento",
            "Combustible",
            "Estacionamiento",
            "Kilometraje",
            "Legales",
            "Marketing",
            "Materiales",
            "Otro",
            "Pasajes Aereos",
            "Peajes",
            "Telefonos",
            "Transporte",
            "Viaticos",
            "Personal RD",
            "Personal CA",
            "Personal RM",
            "BCI Paga TC",
        ]

        # Internal columns we want hidden / not editable
        INTERNAL_COLS = ["_RID_", "_FACT_KAME_DB", "_SEL_FACT_", "Row_Hash", "File_Flash"]

        st.markdown("### 🧾 Pending to enter in Kame")

        edited_pendiente = st.data_editor(
            st.session_state["pending_buffer"],
            use_container_width=True,
            hide_index=True,
            key="editable_df_pendiente",
            disabled=[c for c in INTERNAL_COLS if c in st.session_state["pending_buffer"].columns],
            column_config={
                "CONCILIADO": st.column_config.CheckboxColumn("✅ CONCILIADO", default=False),
                "FACT_KAME": st.column_config.CheckboxColumn("📤 FACT_KAME (select)", default=False),
                "TIPO_GASTO": st.column_config.SelectboxColumn("🗂️ TIPO_GASTO", options=TIPO_GASTO_OPTS),
            },
        )

        # Persist buffer after edits
        st.session_state["pending_buffer"] = edited_pendiente.copy()

        # Persist FACT_KAME selection state (requested)
        for _, r in edited_pendiente[["_RID_", "FACT_KAME"]].iterrows():
            st.session_state["fact_sel"][int(r["_RID_"])] = bool(r["FACT_KAME"])

        # Reset pending edits (buffer only, does not touch DB)
        if st.button("↩️ Reset pending edits (discard UI changes)"):
            for k in ["pending_buffer", "pending_buffer_ctx", "fact_sel"]:
                if k in st.session_state:
                    del st.session_state[k]
            st.rerun()

        # Explicit save button (no background autosave)
        if st.button("💾 Save edits (TIPO_GASTO / CONCILIADO)"):
            try:
                updates = []
                for _, row in edited_pendiente.iterrows():
                    updates.append(
                        (
                            1 if bool(row.get("CONCILIADO")) else 0,
                            str(row.get("TIPO_GASTO") or ""),
                            int(row["_RID_"]),
                        )
                    )
                conn.executemany(
                    """
                    UPDATE transacciones
                    SET CONCILIADO = ?, TIPO_GASTO = ?
                    WHERE rowid = ?
                    """,
                    updates,
                )
                conn.commit()
                st.success("✅ Saved edits to DB.")
                st.session_state["db_dirty"] = True
            except Exception as e:
                st.error(f"❌ Save failed: {e}")

        # Move rows ONLY when selected + ready + explicit click
        selected = edited_pendiente[edited_pendiente["FACT_KAME"] == True].copy()
        ready_mask = (selected["CONCILIADO"] == True) & (
            selected["TIPO_GASTO"].fillna("").astype(str).str.strip() != ""
        )
        all_ready = (len(selected) > 0) and ready_mask.all()

        mover = st.button("➡️ Move selected to 'Ingresado en Kame'", disabled=not all_ready)

        if mover:
            try:
                updates = []
                for _, row in selected.iterrows():
                    updates.append(
                        (
                            1 if bool(row.get("CONCILIADO")) else 0,
                            str(row.get("TIPO_GASTO") or ""),
                            1,  # FACT_KAME = 1
                            int(row["_RID_"]),
                        )
                    )

                conn.executemany(
                    """
                    UPDATE transacciones
                    SET CONCILIADO = ?, TIPO_GASTO = ?, FACT_KAME = ?
                    WHERE rowid = ?
                    """,
                    updates,
                )
                conn.commit()

                # Clear selection state for moved rows
                for rid in selected["_RID_"].astype(int).tolist():
                    st.session_state["fact_sel"][rid] = False

                st.success("✅ Moved to 'Ingresado en Kame'.")

                # Clear buffer so it rebuilds from fresh DB snapshot
                for k in ["pending_buffer", "pending_buffer_ctx"]:
                    if k in st.session_state:
                        del st.session_state[k]

                st.session_state["db_dirty"] = True
                st.rerun()

            except Exception as e:
                st.error(f"❌ Move failed: {e}")

        st.markdown("### ✅ Ingresado en Kame (read-only)")
        if not df_ingresado.empty:
            sort_cols_i = [c for c in ["FECHA_OPERACION", "DESCRIPCION", "MONTO_OPERACION"] if c in df_ingresado.columns]
            if sort_cols_i:
                df_ingresado = df_ingresado.sort_values(by=sort_cols_i, kind="mergesort").reset_index(drop=True)

        # Hide internal helper columns from the read-only view too
        df_ingresado_view = df_ingresado.drop(columns=[c for c in INTERNAL_COLS if c in df_ingresado.columns], errors="ignore")
        st.dataframe(df_ingresado_view, use_container_width=True)

        st.download_button(
            label="💾 Download full DB as CSV",
            data=st.session_state["df_db"].to_csv(index=False).encode("utf-8"),
            file_name="cartolas_bci_db.csv",
            mime="text/csv",
        )

    with tab2:
        show_dashboard(df_db_f)

else:
    st.info("No transactions stored yet.")

# ============================================================
# RESET DB
# ============================================================
st.markdown("---")
st.subheader("⚙️ Database admin")

with st.expander("🧹 Delete all history"):
    st.warning("This deletes ALL transactions and processed-file records (DB file remains).")
    confirm = st.checkbox("I confirm I want to delete all history")
    if st.button("🗑️ Reset database"):
        if confirm:
            conn.execute("DELETE FROM transacciones")
            conn.execute("DELETE FROM archivos_procesados")
            conn.commit()
            st.success("✅ Database cleared. Reload the page to refresh.")

            for k in [
                "pending_buffer",
                "pending_buffer_ctx",
                "fact_sel",
                "df_db",
                "db_dirty",
            ]:
                if k in st.session_state:
                    del st.session_state[k]
        else:
            st.info("Please check the confirmation box before resetting.")

conn.close()
# === END OF FILE ===
