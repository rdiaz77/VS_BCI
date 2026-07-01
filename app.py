import logging
import pandas as pd
import streamlit as st

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
_log = logging.getLogger(__name__)

from data.database import (
    init_db,
    archivo_ya_procesado,
    registrar_archivo_procesado,
    insertar_transacciones,
    fetch_transacciones,
    update_clasificacion,
    marcar_fact_kame,
    upsert_estado_cuenta,
    fetch_estados_cuenta,
    marcar_traspaso,
    desmarcar_traspaso,
    fetch_traspaso_nacional_disponibles,
    fetch_estados_intl_pendientes,
    fetch_traspaso_suggestions,
    auto_match_traspasos,
    reset_db,
    fetch_tipo_gasto_map,
    auto_tipo_gasto,
    propagar_clasificacion,
)
from data.extractor_nacional import leer_cartola_nacional
from data.extractor_internacional import leer_cartola_internacional
from dashboard import show_dashboard

# ============================================================
# Page config — must be first Streamlit call
# ============================================================
st.set_page_config(page_title="Cartolas TCT BCI", layout="wide")

TIPO_GASTO_OPTIONS_NAC = [
        "Airbnb", "Alimentacion", "Alojamiento", "BCI Paga TC",
        "Canva", "Combustible", "Comida", "Comision Intl", "Comision Nacional",
        "Tr Deuda Intl", "Electronic", "Estacionamiento", "G. Comun", "Garantia", "Google Suite", "Hardware", "Hubspot",
        "Impuesto", "Kilometraje", "Legales", "Libro", "Marketing",
        "Materiales", "Movilizacion", "Otro", "Pasajes Aereos",
        "Peajes", "Personal CA", "Personal RD", "Personal RM",
        "Shutterstock", "Software", "Telefonos", "Transporte", "Viaticos",
]

TIPO_GASTO_OPTIONS_INTL = [
    "Airbnb", "Canva", "Food", "Google", "GSuite",
    "Hotel", "Huber", "Hubspot", "Shutterstock", "Taxi", "Ticket Fare", "Trp a Deuda Nacional", "VEED", "Yachay",
]

# ============================================================
# DB connection — cached for the lifetime of the server process
# ============================================================
@st.cache_resource
def get_conn():
    db_url = st.secrets.get("supabase_db_url") or st.secrets.get("SUPABASE_DB_URL")
    if not db_url:
        st.error("Falta `supabase_db_url` en los secrets. Configúrala en .streamlit/secrets.toml")
        st.stop()
    conn = init_db(db_url)
    _log.info("Connected to PostgreSQL")
    return conn, str(db_url)


def _ensure_conn(conn, db_url: str):
    """Return a live connection, reconnecting if Neon closed the idle connection."""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        return conn
    except Exception:
        _log.warning("Connection lost, reconnecting...")
        try:
            conn.close()
        except Exception:
            pass
        st.cache_resource.clear()
        return init_db(db_url)


# ============================================================
# Password gate
# ============================================================
def _secret_password() -> str | None:
    for key in ("app_password", "APP_PASSWORD"):
        if key in st.secrets:
            return st.secrets[key]
    return None


def require_password() -> None:
    expected = _secret_password()
    if not expected:
        return

    if st.session_state.get("authenticated"):
        return

    st.title("🔒 Acceso protegido")
    pwd = st.text_input("Ingrese la contraseña", type="password")
    if pwd and pwd == expected:
        st.session_state["authenticated"] = True
        st.rerun()
    elif pwd:
        st.error("Contraseña incorrecta")
    st.stop()


# ============================================================
# PDF save helpers
# ============================================================
# ============================================================
# Ingest (upload → DB)
# ============================================================
def _ingest(conn, uploaded, extractor, exclude_terms: list[str]) -> None:
    ingested = skipped = 0
    for f in uploaded:
        if archivo_ya_procesado(conn, f.name):
            st.warning(f"⚠️ **{f.name}** ya fue procesado anteriormente — omitido.")
            skipped += 1
            continue

        try:
            rows, meta = extractor(f.read(), filename=f.name)
        except Exception as e:
            _log.exception("PDF extraction failed: %s", f.name)
            st.error(f"Error leyendo {f.name}: {e}")
            continue

        if exclude_terms:
            rows = [
                r for r in rows
                if not any(t in r.get("DESCRIPCION", "").lower() for t in exclude_terms)
            ]

        # Auto-categorize using history + static rules (only fills empty TIPO_GASTO)
        historic = fetch_tipo_gasto_map(conn)
        for r in rows:
            if not r.get("TIPO_GASTO"):
                r["TIPO_GASTO"] = auto_tipo_gasto(
                    r.get("DESCRIPCION", ""), historic, origen=r.get("ORIGEN", "")
                )

        if rows:
            insertar_transacciones(conn, rows)
            upsert_estado_cuenta(conn, meta)
            registrar_archivo_procesado(conn, f.name)
            ingested += 1
        else:
            st.warning(f"Sin filas válidas en {f.name}. No se registra como procesado.")
            skipped += 1

    if ingested:
        st.success(f"✅ {ingested} archivo(s) procesado(s) correctamente.")


# ============================================================
# Transactions page — shared by Nacional / Internacional
# ============================================================
def render_transactions_page(conn, origen: str) -> None:
    is_intl = origen == "INTERNACIONAL"
    extractor = leer_cartola_internacional if is_intl else leer_cartola_nacional

    st.subheader(f"1) Cargar PDFs — {'Internacional (USD)' if is_intl else 'Nacional (CLP)'}")
    uploaded = st.file_uploader(
        "Sube uno o más PDF", type=["pdf"], accept_multiple_files=True, key=f"up_{origen}"
    )
    exclude_raw = st.text_input(
        "Excluir términos en DESCRIPCION (separados por coma)", value="", key=f"ex_{origen}"
    )
    exclude_terms = [t.strip().lower() for t in exclude_raw.split(",") if t.strip()]

    if uploaded:
        sig = tuple(sorted(f.name for f in uploaded))
        if st.session_state.get(f"_sig_{origen}") != sig:
            st.session_state[f"_sig_{origen}"] = sig
            _ingest(conn, uploaded, extractor, exclude_terms)

    # ---- Load from DB ----
    cols, rows = fetch_transacciones(conn, origen=origen)
    df = pd.DataFrame(rows, columns=cols)

    # ---- International: assign CLP cost via national traspaso match ----
    if is_intl:
        # Learned behaviour: auto-match unambiguous traspasos by amount + date
        auto_n = auto_match_traspasos(conn)
        if auto_n:
            st.toast(f"{auto_n} traspaso(s) emparejado(s) automáticamente.")
            cols, rows = fetch_transacciones(conn, origen=origen)
            df = pd.DataFrame(rows, columns=cols)

        pend_est    = fetch_estados_intl_pendientes(conn)
        disponibles = fetch_traspaso_nacional_disponibles(conn)
        suggestions, _amb = fetch_traspaso_suggestions(conn)

        if pend_est:
            st.divider()
            st.subheader("💱 Asignar Costo en CLP (traspaso)")
            if not disponibles:
                st.info(
                    "No hay líneas **TRASPASO DEUDA INTERNACIONAL** nacionales sin asignar. "
                    "Sube el estado de cuenta nacional donde aparece el traspaso."
                )
            else:
                def _fmt_opt(rid, _opts=disponibles):
                    o = next((x for x in _opts if x["rid"] == rid), None)
                    if o is None:
                        return str(rid)
                    return f"{o['fecha']} · CLP {int(o['clp']):,}"

                opt_rids = [o["rid"] for o in disponibles]
                for est in pend_est:
                    deuda = est.get("deuda")
                    with st.container(border=True):
                        deuda_str = f"US$ {deuda:,.2f}" if deuda else "—"
                        st.markdown(
                            f"**{est['archivo']}** · {est.get('titular') or ''} · "
                            f"DEUDA TOTAL: **{deuda_str}**"
                        )
                        # Pre-select the suggested national line (amount + date chain)
                        default_idx = 0
                        sug = suggestions.get(int(est["id"]))
                        if sug and sug["rid"] in opt_rids:
                            default_idx = opt_rids.index(sug["rid"])
                        sel = st.selectbox(
                            "Traspaso nacional correspondiente (CLP)",
                            options=opt_rids,
                            format_func=_fmt_opt,
                            index=default_idx,
                            key=f"clp_sel_{est['id']}",
                        )
                        # Live rate preview
                        o = next(x for x in disponibles if x["rid"] == sel)
                        if deuda:
                            tasa = abs(float(o["clp"])) / abs(float(deuda))
                            warn = "" if 800 <= tasa <= 1100 else "  ⚠️ tasa fuera de rango"
                            st.caption(f"Tasa resultante: **{tasa:,.2f} CLP/US$**{warn}")
                        if st.button("✅ Asignar costo CLP", key=f"clp_btn_{est['id']}"):
                            try:
                                marcar_traspaso(conn, int(est["id"]), int(sel), o["archivo"])
                                st.success("Costo en CLP asignado.")
                                st.rerun()
                            except Exception as e:
                                _log.exception("marcar_traspaso failed")
                                st.error(f"Error al asignar traspaso: {e}")

    st.divider()

    if not df.empty:
        import plotly.express as px
        monto_col_summary = "MONTO_OPERACION" if is_intl else "MONTO_TOTAL"
        cur_label = "US$" if is_intl else "CLP"
        st.subheader("2) Resumen por Tipo de Gasto")
        df_gastos   = df[df[monto_col_summary] > 0].copy()
        df_con_tipo = df_gastos[df_gastos["TIPO_GASTO"].fillna("") != ""]
        df_sin_tipo = df_gastos[df_gastos["TIPO_GASTO"].fillna("") == ""]

        if not df_con_tipo.empty:
            resumen = (
                df_con_tipo.groupby("TIPO_GASTO")[monto_col_summary]
                .sum().sort_values().reset_index()
            )
            fmt = (lambda v: f"${v:,.2f}") if is_intl else (lambda v: f"${int(v):,}")
            fig = px.bar(
                resumen, x=monto_col_summary, y="TIPO_GASTO", orientation="h",
                text=resumen[monto_col_summary].apply(fmt),
                labels={monto_col_summary: cur_label, "TIPO_GASTO": ""},
            )
            fig.update_traces(textposition="outside")
            fig.update_layout(
                margin=dict(l=0, r=10, t=10, b=0),
                height=max(180, len(resumen) * 28),
                xaxis_title=None,
                showlegend=False,
            )
            st.plotly_chart(fig, use_container_width=True)
            if len(df_sin_tipo) > 0:
                st.caption(f"⚠️ {len(df_sin_tipo)} transacción(es) sin Tipo de Gasto.")
        else:
            st.info("No hay transacciones clasificadas aún.")

    st.divider()
    st.subheader("3) Conciliación / Kame")

    if df.empty:
        st.info("No hay transacciones aún.")
        return

    pending = df[df["FACT_KAME"] == 0].copy()
    done    = df[df["FACT_KAME"] == 1].copy()

    monto_col = "MONTO_OPERACION" if is_intl else "MONTO_TOTAL"

    # Columns shown in the editable pending table
    display_cols = ["_RID_", "TITULAR_NOMBRE", "FECHA_OPERACION", "DESCRIPCION"]
    if is_intl:
        display_cols += ["CIUDAD", "PAIS"]
    display_cols += [monto_col]
    if is_intl:
        display_cols += ["MONTO_CLP"]
    display_cols += ["TIPO_GASTO", "CONCILIADO"]
    if is_intl:
        display_cols += ["TRASPASADO"]
    display_cols += ["FACT_KAME"]

    st.markdown("### Pendientes (no ingresadas en Kame)")
    if pending.empty:
        st.success("No hay pendientes 🎉")
    else:
        pending["_FECHA_DT"] = pd.to_datetime(pending["FECHA_OPERACION"], format="%m/%d/%y", errors="coerce")
        pending = pending.sort_values(["_FECHA_DT"], ascending=True).drop(columns=["_FECHA_DT"]).reset_index(drop=True)
        pending["FACT_KAME"] = False          # UI checkbox — selection only
        pending["CONCILIADO"] = pending["CONCILIADO"].astype(bool)
        if is_intl:
            pending["TRASPASADO"] = pending["TRASPASADO"].astype(bool)

        show_all = st.checkbox(
            "Mostrar todas las filas pendientes", value=False, key=f"all_{origen}"
        )
        view = pending[display_cols].head(None if show_all else 20).copy()

        # Pre-format the amount column as string so thousands separator is guaranteed.
        # The column is disabled (read-only) so storing it as text doesn't affect saves.
        monto_fmt_col = f"{monto_col}_FMT"

        def _fmt_amount(v):
            try:
                return f"{v:,.2f}" if is_intl else f"{int(v):,}"
            except Exception:
                return ""

        view.insert(
            view.columns.get_loc(monto_col),
            monto_fmt_col,
            view[monto_col].apply(_fmt_amount),
        )
        view = view.drop(columns=[monto_col])

        # International: pre-format the CLP-converted amount (filled after traspaso)
        if is_intl and "MONTO_CLP" in view.columns:
            def _fmt_clp(v):
                try:
                    return f"{int(v):,}" if pd.notna(v) else "—"
                except Exception:
                    return "—"
            view["MONTO_CLP"] = view["MONTO_CLP"].apply(_fmt_clp)

        col_cfg = {
            "_RID_": st.column_config.NumberColumn("ID", disabled=True),
            "TITULAR_NOMBRE":  st.column_config.TextColumn("Titular", disabled=True),
            "FECHA_OPERACION": st.column_config.TextColumn("Fecha", disabled=True),
            "DESCRIPCION":     st.column_config.TextColumn("Descripción", disabled=True),
            monto_fmt_col: st.column_config.TextColumn(f"Monto ({cur_label})", disabled=True),
            "TIPO_GASTO":  st.column_config.SelectboxColumn("Tipo gasto", options=TIPO_GASTO_OPTIONS_INTL if is_intl else TIPO_GASTO_OPTIONS_NAC),
            "CONCILIADO":  st.column_config.CheckboxColumn("Conciliado"),
            "FACT_KAME":   st.column_config.CheckboxColumn("Mover a Kame"),
        }
        if is_intl:
            col_cfg["CIUDAD"]     = st.column_config.TextColumn("Ciudad", disabled=True)
            col_cfg["PAIS"]       = st.column_config.TextColumn("País", disabled=True)
            col_cfg["MONTO_CLP"]  = st.column_config.TextColumn("Costo (CLP)", disabled=True)
            col_cfg["TRASPASADO"] = st.column_config.CheckboxColumn("Traspasado a CLP", disabled=True)

        edited = st.data_editor(
            view,
            use_container_width=True,
            hide_index=True,
            column_config=col_cfg,
            key=f"editor_{origen}",
        )

        # Selection for "Mover a Kame"
        selected = edited[edited["FACT_KAME"] == True].copy()
        all_ready = (
            not selected.empty
            and bool(selected["CONCILIADO"].all())
            and not selected["TIPO_GASTO"].fillna("").str.strip().eq("").any()
        )

        c1, c2 = st.columns(2)
        with c1:
            if st.button("💾 Guardar cambios", key=f"save_{origen}"):
                records = edited[["_RID_", "TIPO_GASTO", "CONCILIADO"]].to_dict("records")
                try:
                    update_clasificacion(conn, records)
                    propagar_clasificacion(conn, records)
                    st.success("Cambios guardados.")
                    st.rerun()
                except Exception as e:
                    _log.exception("guardar cambios failed")
                    st.error(f"Error al guardar: {e}")

        with c2:
            if st.button("➡️ Mover a Kame", disabled=not all_ready, key=f"move_{origen}"):
                records = edited[["_RID_", "TIPO_GASTO", "CONCILIADO"]].to_dict("records")
                try:
                    update_clasificacion(conn, records)
                    propagar_clasificacion(conn, records)
                    marcar_fact_kame(conn, selected["_RID_"].astype(int).tolist())
                    st.success(f"{len(selected)} transacción(es) movida(s) a Kame.")
                    st.rerun()
                except Exception as e:
                    _log.exception("mover a kame failed")
                    st.error(f"Error al mover a Kame: {e}")

            if not selected.empty and not all_ready:
                st.info("Para mover: todas deben estar CONCILIADAS y con TIPO_GASTO definido.")

    st.markdown("### ✅ Ingresado en Kame")
    if done.empty:
        st.info("Aún no hay transacciones ingresadas.")
    else:
        # Show same columns minus _RID_ and FACT_KAME, plus ARCHIVO_ORIGEN
        view_done = [c for c in display_cols if c not in ("_RID_", "FACT_KAME")] + ["ARCHIVO_ORIGEN"]
        view_done = [c for c in view_done if c in done.columns]
        done["_FECHA_DT"] = pd.to_datetime(done["FECHA_OPERACION"], format="%m/%d/%y", errors="coerce")
        done_view = done.sort_values("_FECHA_DT")[view_done].copy()
        if is_intl and "MONTO_CLP" in done_view.columns:
            done_view["MONTO_CLP"] = done_view["MONTO_CLP"].apply(
                lambda v: f"{int(v):,}" if pd.notna(v) else "—"
            )
        st.dataframe(
            done_view,
            use_container_width=True,
            hide_index=True,
            column_config={"MONTO_CLP": st.column_config.TextColumn("Costo (CLP)")} if is_intl else None,
        )


# ============================================================
# Traspaso reconciliation page
# ============================================================
def render_traspaso_page(conn) -> None:
    st.subheader("🔗 Traspasos Internacional → Nacional")
    st.caption(
        "Vista de revisión. El emparejamiento se hace en la pestaña **Internacional** "
        "(automático por fecha, o con el selector de costo en CLP). Aquí solo revisas el "
        "estado y puedes deshacer un cruce."
    )

    ec_cols, ec_rows = fetch_estados_cuenta(conn, origen="INTERNACIONAL")
    ec = pd.DataFrame(ec_rows, columns=ec_cols)

    if ec.empty:
        st.info("Aún no hay estados de cuenta internacionales cargados.")
        return

    pendientes  = ec[ec["TRASPASO_ESTADO"] != "TRASPASADO"].copy()
    traspasados = ec[ec["TRASPASO_ESTADO"] == "TRASPASADO"].copy()

    st.markdown("### ⏳ Pendientes de traspaso")
    if pendientes.empty:
        st.success("Todos los estados internacionales están traspasados 🎉")
    else:
        for _, row in pendientes.iterrows():
            deuda = row["DEUDA_TOTAL"]
            deuda_str = f"US$ {deuda:,.2f}" if deuda is not None else "—"
            st.markdown(
                f"⏳ **{row['ARCHIVO_ORIGEN']}** · {row['TITULAR_NOMBRE']} · "
                f"DEUDA TOTAL: {deuda_str}"
            )
        st.caption("➡️ Asigna su costo en CLP desde la pestaña **Internacional**.")

    st.markdown("### ✅ Ya traspasados")
    if traspasados.empty:
        st.info("Ninguno todavía.")
    else:
        for _, row in traspasados.iterrows():
            c1, c2 = st.columns([6, 1])
            with c1:
                deuda = row["DEUDA_TOTAL"]
                deuda_str = f"US$ {deuda:,.2f}" if deuda is not None else "—"
                tasa = row.get("TASA_CAMBIO")
                tasa_str = f" · Tasa: **{tasa:,.2f} CLP/US$**" if tasa else ""
                st.markdown(
                    f"✅ **{row['ARCHIVO_ORIGEN']}** · {deuda_str} → "
                    f"nacional `{row['MATCH_ARCHIVO']}`{tasa_str}"
                )
            with c2:
                if st.button("Deshacer", key=f"undo_{row['id']}"):
                    desmarcar_traspaso(conn, int(row["id"]))
                    st.rerun()


# ============================================================
# Admin page
# ============================================================
def render_admin(conn, db_path: str) -> None:
    st.subheader("⚙️ Admin")

    cols, rows = fetch_transacciones(conn)
    df = pd.DataFrame(rows, columns=cols)

    st.download_button(
        "💾 Descargar CSV completo",
        df.drop(columns=["_RID_"], errors="ignore").to_csv(index=False).encode("utf-8"),
        file_name="cartola_tct_bci.csv",
    )

    # Show host only — never expose credentials
    import urllib.parse as _up
    try:
        _p = _up.urlparse(db_path)
        st.markdown(f"Base de datos: `{_p.hostname}:{_p.port or 5432}/{_p.path.lstrip('/')}`")
    except Exception:
        st.markdown("Base de datos: Supabase PostgreSQL")

    with st.expander("🧹 Reset database (borra TODO)"):
        st.warning("Esta acción elimina todas las transacciones, estados y archivos procesados.")
        if st.checkbox("Confirmo que quiero borrar todo el historial", key="confirm_reset"):
            if st.button("🗑️ RESET DB", type="primary"):
                reset_db(conn)
                # Clear cached connection so next request re-initialises cleanly
                st.cache_resource.clear()
                st.success("DB reseteada.")
                st.rerun()


# ============================================================
# Main
# ============================================================
def main() -> None:
    require_password()

    conn, db_path = get_conn()
    conn = _ensure_conn(conn, db_path)

    st.title("📊 Cartolas TCT BCI")

    page = st.sidebar.radio(
        "Sección",
        [
            "📄 Nacional (CLP)",
            "🌎 Internacional (USD)",
            "🔗 Conciliación Traspaso",
            "📈 Dashboard",
            "⚙️ Admin",
        ],
    )

    if page == "📄 Nacional (CLP)":
        render_transactions_page(conn, "NACIONAL")
    elif page == "🌎 Internacional (USD)":
        render_transactions_page(conn, "INTERNACIONAL")
    elif page == "🔗 Conciliación Traspaso":
        render_traspaso_page(conn)
    elif page == "📈 Dashboard":
        cols, rows = fetch_transacciones(conn)
        show_dashboard(pd.DataFrame(rows, columns=cols), conn=conn)
    elif page == "⚙️ Admin":
        render_admin(conn, db_path)


try:
    main()
except Exception as e:
    _log.exception("Unhandled exception in main()")
    st.error(f"Error inesperado: {e}. Revisa los logs del servidor.")
    st.stop()
