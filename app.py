import os
import platform
import sqlite3
from io import BytesIO
import json
import hashlib

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from dashboard import show_dashboard

# === IMPORTS FROM NEW MODULES ===
from data.extractor import leer_cartola  # ✅ DO NOT TOUCH (document reading works well)
from data.database import (
    init_db,
    insertar_en_db,
    archivo_ya_procesado,
    registrar_archivo_procesado,
    migrar_fechas_a_mmddyyyy,
)

# ============================================================
# PAGE CONFIG (must be the first Streamlit command)
# ============================================================
st.set_page_config(page_title="Cartolas BCI Extractor", layout="wide")

# ============================================================
# PASSWORD GATE
# ============================================================
def check_password():
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
# PAGE HEADER
# ============================================================
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

# NOTE: Date migration can flip ambiguous dates like "06/10/25" -> "10/06/25".
# Keep it OFF unless you are sure your stored dates are DD/MM/YY and non-ambiguous.
run_date_migration = st.sidebar.checkbox("Run date migration (DD/MM → MM/DD)", value=False)
if run_date_migration:
    migrar_fechas_a_mmddyyyy(conn)

# ============================================================
# IMPORTANT: load DB with rowid (no schema change)
# ============================================================
def leer_todo_db_with_rowid(conn_) -> pd.DataFrame:
    return pd.read_sql_query("SELECT rowid AS _RID_, * FROM transacciones", conn_)

# ============================================================
# HELPERS: column ordering (minimal)
# ============================================================
def move_archivo_origen_to_end(df: pd.DataFrame) -> pd.DataFrame:
    if "ARCHIVO_ORIGEN" not in df.columns:
        return df
    cols = [c for c in df.columns if c != "ARCHIVO_ORIGEN"] + ["ARCHIVO_ORIGEN"]
    return df[cols]

def reorder_pending_columns(df: pd.DataFrame) -> pd.DataFrame:
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

    x = take("FACT_KAME")
    if x:
        ordered.append(x)

    x = take("TIPO_GASTO")
    if x:
        ordered.append(x)

    ordered.extend(cols)
    return df[ordered]

# ============================================================
# EDITOR DELTA -> BUFFER (working fix)
# + scroll restore to pending table after rerun
# ============================================================
def _delta_signature(editor_state: dict) -> str:
    if not isinstance(editor_state, dict):
        return ""
    edited_rows = editor_state.get("edited_rows", {}) or {}
    raw = json.dumps(edited_rows, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()

def _apply_editor_delta_and_maybe_rerun(buffer_key: str, editor_key: str, sig_key: str):
    """
    Applies editor delta into buffer. If new delta detected, set a flag to scroll back
    to the pending table and rerun exactly once (prevents 'select twice').
    """
    df_buf = st.session_state.get(buffer_key)
    editor_state = st.session_state.get(editor_key)

    if not isinstance(df_buf, pd.DataFrame):
        return

    if not isinstance(editor_state, dict) or "edited_rows" not in editor_state:
        return

    sig = _delta_signature(editor_state)
    last_sig = st.session_state.get(sig_key, "")

    if sig == last_sig:
        return

    edited_rows = editor_state.get("edited_rows", {}) or {}

    # Apply by row index (works because we do NOT sort/rebuild df passed to editor)
    for row_idx_str, changes in edited_rows.items():
        try:
            row_idx = int(row_idx_str)
        except Exception:
            continue

        if row_idx < 0 or row_idx >= len(df_buf):
            continue

        for col, val in (changes or {}).items():
            if col in df_buf.columns:
                df_buf.at[df_buf.index[row_idx], col] = val

    st.session_state[buffer_key] = df_buf
    st.session_state[sig_key] = sig

    # Flag: after rerun, scroll back to pending table
    st.session_state["scroll_to_pending"] = True

    st.rerun()

# ============================================================
# DB LOAD CONTROL
# ============================================================
if "db_dirty" not in st.session_state:
    st.session_state["db_dirty"] = True  # first load

# ============================================================
# PDF UPLOAD / PROCESS  (DO NOT CHANGE leer_cartola)
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
        rows = leer_cartola(pdf_bytes, uploaded_file.name)  # ✅ keep as-is

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
        st.session_state["db_dirty"] = True
        st.session_state["last_upload_df"] = pd.DataFrame(all_data)

        # Clear pending buffer so new DB rows show immediately
        for k in ["pending_buffer", "pending_buffer_ctx", "pending_editor", "pending_editor_sig"]:
            if k in st.session_state:
                del st.session_state[k]

        st.rerun()

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
                        rows = leer_cartola(f, fname)  # ✅ keep as-is
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

# Show last upload
if "last_upload_df" in st.session_state and isinstance(st.session_state["last_upload_df"], pd.DataFrame):
    with st.expander("🧾 Last uploaded transactions (this session)", expanded=False):
        _df_last = st.session_state["last_upload_df"].copy()
        st.dataframe(_df_last, use_container_width=True)
        st.download_button(
            label="💾 Download last upload CSV",
            data=_df_last.to_csv(index=False).encode("utf-8"),
            file_name="cartolas_bci_extraidas.csv",
            mime="text/csv",
        )

# ============================================================
# HISTORY (DB) + STABLE EDITING
# ============================================================
st.subheader("📦 Transactions stored in DB")

if st.button("🔄 Refresh from DB"):
    st.session_state["db_dirty"] = True
    for k in ["pending_buffer", "pending_buffer_ctx", "pending_editor", "pending_editor_sig"]:
        if k in st.session_state:
            del st.session_state[k]

if st.session_state.get("db_dirty", True) or ("df_db" not in st.session_state):
    st.session_state["df_db"] = leer_todo_db_with_rowid(conn)
    st.session_state["db_dirty"] = False

df_db = st.session_state["df_db"]

if df_db.empty:
    st.info("No transactions stored yet.")
    conn.close()
    st.stop()

# TITULAR derived column
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
    # If we just reran due to editor delta, scroll back to pending section
    # (this prevents the page jumping to the top)
    if st.session_state.get("scroll_to_pending", False):
        st.session_state["scroll_to_pending"] = False
        components.html(
            """
            <script>
              const el = window.parent.document.getElementById("pending-anchor");
              if (el) { el.scrollIntoView({behavior: "instant", block: "start"}); }
            </script>
            """,
            height=0,
        )

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

    # Deterministic order (prevents random row shuffle)
    sort_cols = [c for c in ["FECHA_OPERACION", "DESCRIPCION", "MONTO_OPERACION", "_RID_"] if c in df_pendiente.columns]
    if sort_cols:
        df_pendiente = df_pendiente.sort_values(by=sort_cols, kind="mergesort").reset_index(drop=True)

    # IMPORTANT: DB FACT_KAME preserved internally; UI FACT_KAME is selection ONLY
    df_pendiente = df_pendiente.rename(columns={"FACT_KAME": "_FACT_KAME_DB"})
    df_pendiente["FACT_KAME"] = False  # UI selection checkbox

    # Move ARCHIVO_ORIGEN to end
    df_pendiente = move_archivo_origen_to_end(df_pendiente)
    df_ingresado = move_archivo_origen_to_end(df_ingresado)

    # Reorder columns for pending
    df_pendiente = reorder_pending_columns(df_pendiente)

    # Buffer context: rebuild buffer only if filter changes
    buffer_context = f"pending::{titular_seleccionado}"
    if st.session_state.get("pending_buffer_ctx") != buffer_context:
        st.session_state["pending_buffer_ctx"] = buffer_context
        st.session_state["pending_buffer"] = df_pendiente.copy()
        for k in ["pending_editor", "pending_editor_sig"]:
            if k in st.session_state:
                del st.session_state[k]

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

    INTERNAL_COLS = ["_RID_", "_FACT_KAME_DB", "Row_Hash", "File_Flash", "_SEL_FACT_"]

    # Anchor used to scroll back here after rerun
    st.markdown('<div id="pending-anchor"></div>', unsafe_allow_html=True)
    st.markdown("### 🧾 Pending to enter in Kame")

    # Render editor
    st.data_editor(
        st.session_state["pending_buffer"],
        use_container_width=True,
        hide_index=True,
        key="pending_editor",
        disabled=[c for c in INTERNAL_COLS if c in st.session_state["pending_buffer"].columns],
        column_config={
            "CONCILIADO": st.column_config.CheckboxColumn("✅ CONCILIADO", default=False),
            "FACT_KAME": st.column_config.CheckboxColumn("📤 FACT_KAME (select)", default=False),
            "TIPO_GASTO": st.column_config.SelectboxColumn("🗂️ TIPO_GASTO", options=TIPO_GASTO_OPTS),
        },
    )

    # Apply delta and rerun ONCE so changes stick on first click (working fix)
    _apply_editor_delta_and_maybe_rerun(
        buffer_key="pending_buffer",
        editor_key="pending_editor",
        sig_key="pending_editor_sig",
    )

    edited_pendiente = st.session_state["pending_buffer"].copy()

    # Metric AFTER editor
    if "MONTO_OPERACION" in edited_pendiente.columns and "CONCILIADO" in edited_pendiente.columns:
        _m = edited_pendiente.loc[edited_pendiente["CONCILIADO"] == True, "MONTO_OPERACION"]
        _m_sum = int(pd.to_numeric(_m, errors="coerce").fillna(0).sum())
        st.metric("Σ MONTO_OPERACION (CONCILIADO)", f"{_m_sum:,.0f}")
    else:
        st.metric("Σ MONTO_OPERACION (CONCILIADO)", "—")

    c1, c2, c3 = st.columns([1, 1, 2])

    with c1:
        if st.button("↩️ Reset pending edits (discard UI changes)"):
            for k in ["pending_buffer", "pending_buffer_ctx", "pending_editor", "pending_editor_sig"]:
                if k in st.session_state:
                    del st.session_state[k]
            st.rerun()

    with c2:
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
    if "FACT_KAME" in edited_pendiente.columns:
        selected = edited_pendiente[edited_pendiente["FACT_KAME"] == True].copy()
    else:
        selected = edited_pendiente.iloc[0:0].copy()

    if len(selected) > 0:
        ok_conc = selected.get("CONCILIADO", False) == True
        ok_tipo = selected.get("TIPO_GASTO", "").fillna("").astype(str).str.strip() != ""
        all_ready = bool((ok_conc & ok_tipo).all())
    else:
        all_ready = False

    with c3:
        mover = st.button("➡️ Move selected to 'Ingresado en Kame'", disabled=not all_ready)

    if mover:
        try:
            updates = []
            for _, row in selected.iterrows():
                updates.append(
                    (
                        1 if bool(row.get("CONCILIADO")) else 0,
                        str(row.get("TIPO_GASTO") or ""),
                        1,  # FACT_KAME = 1 (DB)
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

            st.success("✅ Moved to 'Ingresado en Kame'.")

            # Clear buffer so it rebuilds from DB snapshot on rerun
            for k in ["pending_buffer", "pending_buffer_ctx", "pending_editor", "pending_editor_sig"]:
                if k in st.session_state:
                    del st.session_state[k]

            st.session_state["db_dirty"] = True
            st.rerun()

        except Exception as e:
            st.error(f"❌ Move failed: {e}")

    st.markdown("### ✅ Ingresado en Kame (read-only)")
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
            st.success("✅ Database cleared.")

            for k in [
                "pending_buffer",
                "pending_buffer_ctx",
                "pending_editor",
                "pending_editor_sig",
                "df_db",
                "db_dirty",
            ]:
                if k in st.session_state:
                    del st.session_state[k]

            st.rerun()
        else:
            st.info("Please check the confirmation box before resetting.")

conn.close()
