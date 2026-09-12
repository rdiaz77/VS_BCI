import hmac
import logging

import pandas as pd
import streamlit as st

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
_log = logging.getLogger(__name__)

from data.categorias import opciones as tipo_gasto_opciones
from data.common import detectar_tipo_cartola, resumen_meta
from data.database import (
    auto_match_traspasos,
    auto_tipo_gasto,
    desmarcar_traspaso,
    estado_ya_procesado,
    fetch_estados_cuenta,
    fetch_estados_intl_pendientes,
    fetch_rango_fechas,
    fetch_tarjetas,
    fetch_tipo_gasto_map,
    fetch_transacciones,
    fetch_traspaso_nacional_disponibles,
    fetch_traspaso_suggestions,
    ingest_statement,
    init_pool,
    marcar_fact_kame,
    marcar_traspaso,
    mover_a_pendientes,
    pooled_conn,
    propagar_clasificacion,
    reset_db,
    update_clasificacion,
)
from data.extractor_internacional import leer_cartola_internacional
from data.extractor_nacional import leer_cartola_nacional
from dashboard import show_dashboard

st.set_page_config(page_title="Cartolas TCT BCI", layout="wide")

# Rows shown in the editable table at once. The archive spans years, so the
# table is filtered rather than paginated — this cap only guards the browser.
MAX_EDITOR_ROWS = 300

MONEY_COLS = ["MONTO_ORIGEN", "MONTO_OPERACION", "MONTO_TOTAL", "MONTO_CLP"]

# ============================================================
# Connection pool — one per server process, one connection per script run
# ============================================================
@st.cache_resource
def get_pool():
    db_url = st.secrets.get("supabase_db_url") or st.secrets.get("SUPABASE_DB_URL")
    if not db_url:
        st.error(
            "Falta `supabase_db_url` en los secrets. "
            "Configúrala en .streamlit/secrets.toml"
        )
        st.stop()
    pool = init_pool(str(db_url))
    _log.info("PostgreSQL pool ready")
    return pool, str(db_url)


# ============================================================
# Password gate
# ============================================================
def _secret_password() -> str | None:
    for key in ("app_password", "APP_PASSWORD"):
        if key in st.secrets:
            return str(st.secrets[key])
    return None


def require_password() -> None:
    expected = _secret_password()
    if not expected:
        return
    if st.session_state.get("authenticated"):
        return

    st.title("🔒 Acceso protegido")
    if st.session_state.get("_pw_fails", 0) >= 5:
        st.error("Demasiados intentos fallidos. Recarga la página para reintentar.")
        st.stop()

    pwd = st.text_input("Ingrese la contraseña", type="password")
    if pwd:
        # compare_digest avoids leaking the password length/prefix via timing.
        if hmac.compare_digest(pwd, expected):
            st.session_state["authenticated"] = True
            st.session_state["_pw_fails"] = 0
            st.rerun()
        else:
            st.session_state["_pw_fails"] = st.session_state.get("_pw_fails", 0) + 1
            st.error("Contraseña incorrecta")
    st.stop()


# ============================================================
# Helpers
# ============================================================
def _as_float(df: pd.DataFrame) -> pd.DataFrame:
    """NUMERIC comes back as Decimal; use float64 for display and charts.

    Storage stays exact — this only affects what pandas renders and sums.
    """
    for c in MONEY_COLS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def _fmt_money(v, is_intl: bool) -> str:
    try:
        return f"{v:,.2f}" if is_intl else f"{int(v):,}"
    except (TypeError, ValueError):
        return ""


# ============================================================
# Ingest — auto-routes each PDF to the right parser
# ============================================================
def _ingest(conn, uploaded, exclude_terms: list[str]) -> None:
    ingested = skipped = failed = 0
    por_origen: dict[str, int] = {}

    historic = fetch_tipo_gasto_map(conn)

    for f in uploaded:
        raw = f.read()
        tipo = detectar_tipo_cartola(raw)
        if tipo is None:
            st.error(
                f"❌ **{f.name}** — no parece un estado de cuenta BCI "
                "(no se encontró el título Nacional/Internacional)."
            )
            failed += 1
            continue

        extractor = (
            leer_cartola_internacional if tipo == "INTERNACIONAL" else leer_cartola_nacional
        )
        try:
            rows, meta = extractor(raw, filename=f.name)
        except Exception as e:
            _log.exception("PDF extraction failed: %s", f.name)
            st.error(f"❌ Error leyendo **{f.name}**: {e}")
            failed += 1
            continue

        dup = estado_ya_procesado(conn, meta)
        if dup:
            st.warning(
                f"⚠️ **{f.name}** — {resumen_meta(meta)} ya estaba cargado "
                f"como `{dup['archivo']}` — omitido."
            )
            skipped += 1
            continue

        if exclude_terms:
            rows = [
                r for r in rows
                if not any(t in r.get("DESCRIPCION", "").lower() for t in exclude_terms)
            ]

        for r in rows:
            if not r.get("TIPO_GASTO"):
                r["TIPO_GASTO"] = auto_tipo_gasto(
                    r.get("DESCRIPCION", ""), historic, origen=r.get("ORIGEN", "")
                )

        if not rows:
            st.warning(f"⚠️ **{f.name}** — sin filas válidas. No se carga.")
            skipped += 1
            continue

        try:
            n = ingest_statement(conn, rows, meta)
        except Exception as e:
            _log.exception("ingest failed: %s", f.name)
            st.error(f"❌ Error guardando **{f.name}**: {e}")
            failed += 1
            continue

        if n == 0:
            st.warning(f"⚠️ **{f.name}** — {resumen_meta(meta)} ya existía — omitido.")
            skipped += 1
        else:
            ingested += 1
            por_origen[tipo] = por_origen.get(tipo, 0) + 1
            st.success(f"✅ **{f.name}** → {resumen_meta(meta)} · {n} transacciones")

    if ingested:
        detalle = ", ".join(f"{n} {o.lower()}" for o, n in sorted(por_origen.items()))
        st.success(f"✅ {ingested} estado(s) cargado(s) ({detalle}).")
    if skipped or failed:
        st.info(f"{skipped} omitido(s), {failed} con error.")


def _upload_section(conn, origen: str) -> None:
    st.subheader("1) Cargar PDFs")
    st.caption(
        "Puedes subir **varias cartolas a la vez**, de distintas tarjetas y meses. "
        "Cada PDF se envía automáticamente al lector Nacional o Internacional "
        "según su encabezado, así que no importa desde qué pestaña las subas."
    )
    uploaded = st.file_uploader(
        "Arrastra uno o más PDF",
        type=["pdf"],
        accept_multiple_files=True,
        key=f"up_{origen}",
    )
    exclude_raw = st.text_input(
        "Excluir términos en DESCRIPCION (separados por coma)",
        value="",
        key=f"ex_{origen}",
    )
    exclude_terms = [t.strip().lower() for t in exclude_raw.split(",") if t.strip()]

    if uploaded:
        sig = tuple(sorted((f.name, f.size) for f in uploaded))
        if st.session_state.get(f"_sig_{origen}") != sig:
            st.session_state[f"_sig_{origen}"] = sig
            with st.status(f"Procesando {len(uploaded)} archivo(s)…", expanded=True):
                _ingest(conn, uploaded, exclude_terms)
            st.rerun()


def _filter_bar(conn, origen: str) -> dict:
    """Card / date-range / text filters, applied in SQL."""
    tarjetas = [t for t in fetch_tarjetas(conn) if t["ult4"]]
    dmin, dmax = fetch_rango_fechas(conn)

    c1, c2, c3 = st.columns([2, 3, 3])

    with c1:
        opts = ["Todas"] + [t["ult4"] for t in tarjetas]
        labels = {t["ult4"]: f"••{t['ult4']} ({t['estados']} cartolas)" for t in tarjetas}
        sel = st.selectbox(
            "Tarjeta",
            opts,
            format_func=lambda v: "Todas las tarjetas" if v == "Todas" else labels.get(v, v),
            key=f"f_card_{origen}",
        )
        tarjeta = None if sel == "Todas" else sel

    with c2:
        desde = hasta = None
        if dmin and dmax:
            rango = st.date_input(
                "Período",
                value=(dmin, dmax),
                min_value=dmin,
                max_value=dmax,
                key=f"f_date_{origen}",
            )
            # Mid-selection the widget returns a 1-tuple; ignore until complete.
            if isinstance(rango, (tuple, list)) and len(rango) == 2:
                desde, hasta = rango[0].isoformat(), rango[1].isoformat()
        else:
            st.caption("Sin transacciones cargadas todavía.")

    with c3:
        q = st.text_input("Buscar en descripción", value="", key=f"f_q_{origen}")

    return {
        "tarjeta": tarjeta,
        "desde": desde,
        "hasta": hasta,
        "search": q.strip() or None,
    }


# ============================================================
# Transactions page — shared by Nacional / Internacional
# ============================================================
def render_transactions_page(conn, origen: str) -> None:
    is_intl = origen == "INTERNACIONAL"
    cur_label = "US$" if is_intl else "CLP"

    _upload_section(conn, origen)

    # ---- International: reconcile the statement balance to a CLP line ----
    if is_intl:
        pend_est = fetch_estados_intl_pendientes(conn)
        if pend_est:
            # Only worth querying when something is actually pending.
            auto_n = auto_match_traspasos(conn)
            if auto_n:
                st.toast(f"{auto_n} traspaso(s) emparejado(s) automáticamente.")
                pend_est = fetch_estados_intl_pendientes(conn)

        if pend_est:
            disponibles = fetch_traspaso_nacional_disponibles(conn)
            suggestions, _amb = fetch_traspaso_suggestions(conn)
            st.divider()
            st.subheader("💱 Asignar Costo en CLP (traspaso)")
            if not disponibles:
                st.info(
                    "No hay líneas **TRASPASO DEUDA INTERNACIONAL** nacionales sin "
                    "asignar. Sube el estado de cuenta nacional donde aparece el traspaso."
                )
            else:
                opt_rids = [o["rid"] for o in disponibles]
                by_rid = {o["rid"]: o for o in disponibles}

                def _fmt_opt(rid):
                    o = by_rid.get(rid)
                    if o is None:
                        return str(rid)
                    card = f" ••{o['ult4']}" if o.get("ult4") else ""
                    return f"{o['fecha']}{card} · CLP {int(o['clp']):,}"

                for est in pend_est:
                    deuda = est.get("deuda")
                    with st.container(border=True):
                        card = f" ••{est['ult4']}" if est.get("ult4") else ""
                        deuda_str = f"US$ {deuda:,.2f}" if deuda else "—"
                        st.markdown(
                            f"**{est['archivo']}**{card} · {est.get('titular') or ''} · "
                            f"DEUDA TOTAL: **{deuda_str}**"
                        )
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
                        o = by_rid.get(sel)
                        if o and deuda:
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
    st.subheader("2) Filtrar")
    filt = _filter_bar(conn, origen)

    cols, rows = fetch_transacciones(conn, origen=origen, **filt)
    df = _as_float(pd.DataFrame(rows, columns=cols))

    if df.empty:
        st.info("No hay transacciones con estos filtros.")
        return

    monto_col = "MONTO_OPERACION" if is_intl else "MONTO_TOTAL"
    if monto_col not in df.columns:
        monto_col = next(
            (c for c in MONEY_COLS if c in df.columns), None
        )
    if monto_col is None:
        st.error("No hay columna de monto en los datos.")
        return

    # ---- Summary chart ----
    st.subheader("3) Resumen por Tipo de Gasto")
    df_gastos = df[df[monto_col] > 0]
    df_con_tipo = df_gastos[df_gastos["TIPO_GASTO"].fillna("") != ""]
    df_sin_tipo = df_gastos[df_gastos["TIPO_GASTO"].fillna("") == ""]

    if df_con_tipo.empty:
        st.info("No hay transacciones clasificadas con estos filtros.")
    else:
        import plotly.express as px

        resumen = (
            df_con_tipo.groupby("TIPO_GASTO")[monto_col].sum().sort_values().reset_index()
        )
        fig = px.bar(
            resumen,
            x=monto_col,
            y="TIPO_GASTO",
            orientation="h",
            text=resumen[monto_col].apply(lambda v: _fmt_money(v, is_intl)),
            labels={monto_col: cur_label, "TIPO_GASTO": ""},
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

    st.divider()
    st.subheader("4) Conciliación / Kame")

    pending = df[df["FACT_KAME"] == 0].copy()
    done = df[df["FACT_KAME"] == 1].copy()

    display_cols = ["_RID_", "TARJETA_ULT4", "FECHA_OPERACION", "DESCRIPCION"]
    if is_intl:
        display_cols += ["CIUDAD", "PAIS"]
    display_cols += [monto_col]
    if is_intl:
        display_cols += ["MONTO_CLP"]
    display_cols += ["TIPO_GASTO", "CONCILIADO"]
    if is_intl:
        display_cols += ["TRASPASADO"]
    display_cols += ["FACT_KAME"]
    display_cols = [c for c in display_cols if c in df.columns]

    _render_pending(conn, pending, display_cols, monto_col, origen, is_intl, cur_label)
    _render_done(conn, done, display_cols, monto_col, origen, is_intl, cur_label)


def _render_pending(conn, pending, display_cols, monto_col, origen, is_intl, cur_label):
    st.markdown("### Pendientes (no ingresadas en Kame)")
    if pending.empty:
        st.success("No hay pendientes con estos filtros 🎉")
        return

    total_pend = len(pending)
    pending = pending.reset_index(drop=True)
    pending["FACT_KAME"] = False  # UI checkbox — selection only
    pending["CONCILIADO"] = pending["CONCILIADO"].astype(bool)
    if is_intl and "TRASPASADO" in pending.columns:
        pending["TRASPASADO"] = pending["TRASPASADO"].astype(bool)

    truncated = total_pend > MAX_EDITOR_ROWS
    if truncated:
        st.warning(
            f"Mostrando las primeras {MAX_EDITOR_ROWS} de {total_pend:,} filas "
            "pendientes. Acota el período o la tarjeta para verlas todas."
        )
    shown = pending.head(MAX_EDITOR_ROWS)

    _conc_placeholder = st.empty()

    view = shown[display_cols].copy()
    monto_fmt_col = f"{monto_col}_FMT"
    view.insert(
        view.columns.get_loc(monto_col),
        monto_fmt_col,
        view[monto_col].apply(lambda v: _fmt_money(v, is_intl)),
    )
    view = view.drop(columns=[monto_col])

    if is_intl and "MONTO_CLP" in view.columns:
        view["MONTO_CLP"] = view["MONTO_CLP"].apply(
            lambda v: f"{int(v):,}" if pd.notna(v) else "—"
        )

    col_cfg = {
        "_RID_": st.column_config.NumberColumn("ID", disabled=True),
        "TARJETA_ULT4": st.column_config.TextColumn("Tarjeta", disabled=True),
        "FECHA_OPERACION": st.column_config.TextColumn("Fecha", disabled=True),
        "DESCRIPCION": st.column_config.TextColumn("Descripción", disabled=True),
        monto_fmt_col: st.column_config.TextColumn(f"Monto ({cur_label})", disabled=True),
        "TIPO_GASTO": st.column_config.SelectboxColumn(
            "Tipo gasto", options=tipo_gasto_opciones(origen)
        ),
        "CONCILIADO": st.column_config.CheckboxColumn("Conciliado"),
        "FACT_KAME": st.column_config.CheckboxColumn("Mover a Kame"),
    }
    if is_intl:
        col_cfg["CIUDAD"] = st.column_config.TextColumn("Ciudad", disabled=True)
        col_cfg["PAIS"] = st.column_config.TextColumn("País", disabled=True)
        col_cfg["MONTO_CLP"] = st.column_config.TextColumn("Costo (CLP)", disabled=True)
        col_cfg["TRASPASADO"] = st.column_config.CheckboxColumn(
            "Traspasado a CLP", disabled=True
        )

    edited = st.data_editor(
        view,
        use_container_width=True,
        hide_index=True,
        height=600,
        column_config=col_cfg,
        key=f"editor_{origen}",
    )

    # ---- Live total of the rows ticked as conciliadas ----
    conc_rows = edited[edited["CONCILIADO"] == True]  # noqa: E712
    if not conc_rows.empty:
        amounts = shown.loc[shown["_RID_"].isin(conc_rows["_RID_"].tolist()), monto_col]
        gastos = float(amounts[amounts > 0].sum())
        abonos = float(amounts[amounts < 0].sum())
        neto = float(amounts.sum())
        f = (lambda v: f"US$ {v:,.2f}") if is_intl else (lambda v: f"${v:,.0f} CLP")
        _conc_placeholder.markdown(
            "<div style='text-align:right;font-size:0.8rem;line-height:1.6'>"
            f"✅ <b>Gastos:</b> {f(gastos)} &nbsp;|&nbsp;"
            f"<b>Abonos:</b> {f(abs(abonos))} &nbsp;|&nbsp;"
            f"<b>Neto:</b> {f(neto)}"
            "</div>",
            unsafe_allow_html=True,
        )

    selected = edited[edited["FACT_KAME"] == True].copy()  # noqa: E712
    all_ready = (
        not selected.empty
        and bool(selected["CONCILIADO"].all())
        and not selected["TIPO_GASTO"].fillna("").str.strip().eq("").any()
    )
    records = edited[["_RID_", "TIPO_GASTO", "CONCILIADO"]].to_dict("records")

    c1, c2 = st.columns(2)
    with c1:
        if st.button("💾 Guardar cambios", key=f"save_{origen}"):
            try:
                update_clasificacion(conn, records)
                n = propagar_clasificacion(conn, records)
                msg = "Cambios guardados."
                if n:
                    msg += f" Se completaron {n} fila(s) sin clasificar."
                st.success(msg)
                st.rerun()
            except Exception as e:
                _log.exception("guardar cambios failed")
                st.error(f"Error al guardar: {e}")

    with c2:
        if st.button("➡️ Mover a Kame", disabled=not all_ready, key=f"move_{origen}"):
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


def _render_done(conn, done, display_cols, monto_col, origen, is_intl, cur_label):
    st.markdown("### ✅ Ingresado en Kame")
    if done.empty:
        st.info("Aún no hay transacciones ingresadas con estos filtros.")
        return

    view_cols = [c for c in display_cols if c != "FACT_KAME"]
    if "ARCHIVO_ORIGEN" in done.columns:
        view_cols = view_cols + ["ARCHIVO_ORIGEN"]
    done_view = done[view_cols].head(MAX_EDITOR_ROWS).copy()

    if len(done) > MAX_EDITOR_ROWS:
        st.caption(f"Mostrando {MAX_EDITOR_ROWS} de {len(done):,} filas.")

    done_view[monto_col] = done_view[monto_col].apply(lambda v: _fmt_money(v, is_intl))
    if is_intl and "MONTO_CLP" in done_view.columns:
        done_view["MONTO_CLP"] = done_view["MONTO_CLP"].apply(
            lambda v: f"{int(v):,}" if pd.notna(v) else "—"
        )
    # CheckboxColumn needs real booleans, not the 0/1 integers the DB stores.
    for flag in ("CONCILIADO", "TRASPASADO"):
        if flag in done_view.columns:
            done_view[flag] = done_view[flag].astype(bool)
    done_view["_DEVOLVER_"] = False

    # Everything except the checkbox is read-only: edits here were silently
    # discarded before, which looked like the app losing data.
    col_cfg = {c: st.column_config.TextColumn(c.title(), disabled=True) for c in view_cols}
    col_cfg["_RID_"] = st.column_config.NumberColumn("ID", disabled=True)
    col_cfg[monto_col] = st.column_config.TextColumn(f"Monto ({cur_label})", disabled=True)
    col_cfg["TARJETA_ULT4"] = st.column_config.TextColumn("Tarjeta", disabled=True)
    col_cfg["CONCILIADO"] = st.column_config.CheckboxColumn("Conciliado", disabled=True)
    if is_intl and "TRASPASADO" in done_view.columns:
        col_cfg["TRASPASADO"] = st.column_config.CheckboxColumn(
            "Traspasado", disabled=True
        )
    col_cfg["_DEVOLVER_"] = st.column_config.CheckboxColumn("Devolver")

    edited_done = st.data_editor(
        done_view,
        use_container_width=True,
        hide_index=True,
        column_config=col_cfg,
        key=f"done_editor_{origen}",
    )

    to_move = edited_done[edited_done["_DEVOLVER_"] == True]  # noqa: E712
    if not to_move.empty:
        if st.button(
            f"↩️ Devolver {len(to_move)} fila(s) a Pendientes",
            key=f"undo_kame_{origen}",
            type="primary",
        ):
            try:
                mover_a_pendientes(conn, to_move["_RID_"].astype(int).tolist())
                st.success(f"{len(to_move)} transacción(es) devuelta(s) a Pendientes.")
                st.rerun()
            except Exception as e:
                _log.exception("mover_a_pendientes failed")
                st.error(f"Error: {e}")


# ============================================================
# Traspaso reconciliation page
# ============================================================
def render_traspaso_page(conn) -> None:
    st.subheader("🔗 Traspasos Internacional → Nacional")
    st.caption(
        "Vista de revisión. El emparejamiento se hace en la pestaña "
        "**Internacional**. Aquí revisas el estado y puedes deshacer un cruce."
    )

    ec_cols, ec_rows = fetch_estados_cuenta(conn, origen="INTERNACIONAL")
    ec = pd.DataFrame(ec_rows, columns=ec_cols)
    if ec.empty:
        st.info("Aún no hay estados de cuenta internacionales cargados.")
        return

    pendientes = ec[ec["TRASPASO_ESTADO"] != "TRASPASADO"]
    traspasados = ec[ec["TRASPASO_ESTADO"] == "TRASPASADO"]

    st.markdown("### ⏳ Pendientes de traspaso")
    if pendientes.empty:
        st.success("Todos los estados internacionales están traspasados 🎉")
    else:
        for _, row in pendientes.iterrows():
            deuda = row["DEUDA_TOTAL"]
            card = f" ••{row['TARJETA_ULT4']}" if row.get("TARJETA_ULT4") else ""
            st.markdown(
                f"⏳ **{row['ARCHIVO_ORIGEN']}**{card} · {row['TITULAR_NOMBRE']} · "
                f"DEUDA TOTAL: {f'US$ {deuda:,.2f}' if deuda is not None else '—'}"
            )
        st.caption("➡️ Asigna su costo en CLP desde la pestaña **Internacional**.")

    st.markdown("### ✅ Ya traspasados")
    if traspasados.empty:
        st.info("Ninguno todavía.")
        return
    for _, row in traspasados.iterrows():
        c1, c2 = st.columns([6, 1])
        with c1:
            deuda = row["DEUDA_TOTAL"]
            tasa = row.get("TASA_CAMBIO")
            card = f" ••{row['TARJETA_ULT4']}" if row.get("TARJETA_ULT4") else ""
            st.markdown(
                f"✅ **{row['ARCHIVO_ORIGEN']}**{card} · "
                f"{f'US$ {deuda:,.2f}' if deuda is not None else '—'} → "
                f"nacional `{row['MATCH_ARCHIVO']}`"
                + (f" · Tasa: **{tasa:,.2f} CLP/US$**" if tasa else "")
            )
        with c2:
            if st.button("Deshacer", key=f"undo_{row['ID']}"):
                try:
                    desmarcar_traspaso(conn, int(row["ID"]))
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")


# ============================================================
# Admin page
# ============================================================
def render_admin(conn, db_url: str) -> None:
    st.subheader("⚙️ Admin")

    tarjetas = fetch_tarjetas(conn)
    if tarjetas:
        st.markdown("**Tarjetas en la base**")
        st.dataframe(
            pd.DataFrame(tarjetas).rename(
                columns={
                    "ult4": "Tarjeta",
                    "titular": "Titular",
                    "estados": "Cartolas",
                    "desde": "Desde",
                    "hasta": "Hasta",
                }
            ),
            use_container_width=True,
            hide_index=True,
        )

    cols, rows = fetch_transacciones(conn)
    df = _as_float(pd.DataFrame(rows, columns=cols))
    st.caption(f"{len(df):,} transacciones en total.")

    st.download_button(
        "💾 Descargar CSV completo",
        df.drop(columns=["_RID_"], errors="ignore").to_csv(index=False).encode("utf-8"),
        file_name="cartola_tct_bci.csv",
        mime="text/csv",
    )

    import urllib.parse as _up

    try:
        p = _up.urlparse(db_url)  # host only — never render credentials
        st.markdown(f"Base de datos: `{p.hostname}:{p.port or 5432}/{p.path.lstrip('/')}`")
    except Exception:
        st.markdown("Base de datos: PostgreSQL")

    with st.expander("🧹 Reset database (borra TODO)"):
        st.warning("Elimina todas las transacciones y estados de cuenta.")
        if st.checkbox("Confirmo que quiero borrar todo el historial", key="confirm_reset"):
            if st.button("🗑️ RESET DB", type="primary"):
                try:
                    reset_db(conn)
                    st.success("DB reseteada.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")


# ============================================================
# Main
# ============================================================
def main() -> None:
    require_password()
    pool, db_url = get_pool()

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

    # One pooled connection per script run, always returned.
    with pooled_conn(pool) as conn:
        if page == "📄 Nacional (CLP)":
            render_transactions_page(conn, "NACIONAL")
        elif page == "🌎 Internacional (USD)":
            render_transactions_page(conn, "INTERNACIONAL")
        elif page == "🔗 Conciliación Traspaso":
            render_traspaso_page(conn)
        elif page == "📈 Dashboard":
            cols, rows = fetch_transacciones(conn)
            show_dashboard(_as_float(pd.DataFrame(rows, columns=cols)), conn=conn)
        elif page == "⚙️ Admin":
            render_admin(conn, db_url)


# Streamlit execs this file with __name__ == "__main__"; the guard keeps
# `import app` (tests, tooling) from launching the app and opening a connection.
if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        _log.exception("Unhandled exception in main()")
        st.error(f"Error inesperado: {e}. Revisa los logs del servidor.")
        st.stop()
