import pandas as pd
import streamlit as st

try:
    import plotly.express as px

    _HAS_PLOTLY = True
except Exception:  # pragma: no cover - plotly is in requirements
    _HAS_PLOTLY = False


def _parse_dates(df: pd.DataFrame) -> pd.DataFrame:
    """Prefer the real DATE column; fall back to the bank's MM/DD/YY text."""
    out = df.copy()
    if "FECHA_OPERACION_D" in out.columns:
        out["FECHA_DT"] = pd.to_datetime(out["FECHA_OPERACION_D"], errors="coerce")
    else:
        out["FECHA_DT"] = pd.NaT
    missing = out["FECHA_DT"].isna()
    if missing.any() and "FECHA_OPERACION" in out.columns:
        out.loc[missing, "FECHA_DT"] = pd.to_datetime(
            out.loc[missing, "FECHA_OPERACION"], format="%m/%d/%y", errors="coerce"
        )
    return out


# ============================================================
# Uploaded statements
# ============================================================
def show_archivos(conn) -> None:
    from data.database import delete_estado_cuenta, fetch_archivos_resumen

    cols, rows = fetch_archivos_resumen(conn)
    if not rows:
        st.info("No hay archivos cargados aún.")
        return

    df = pd.DataFrame(rows, columns=cols)
    df["_FECHA_DT"] = pd.to_datetime(df["FECHA_ESTADO"], format="%d-%m-%Y", errors="coerce")
    df = df.sort_values("_FECHA_DT", ascending=False).reset_index(drop=True)
    df["DEUDA_TOTAL"] = pd.to_numeric(df["DEUDA_TOTAL"], errors="coerce")

    def _fmt_deuda(row) -> str:
        v = row["DEUDA_TOTAL"]
        if pd.isna(v):
            return "—"
        return f"{v:,.0f}" if row["MONEDA"] == "CLP" else f"{v:,.2f}"

    def _render_group(label: str, group: pd.DataFrame) -> None:
        st.markdown(f"**{label}** ({len(group)})")
        if group.empty:
            st.caption("Sin archivos.")
            return

        for _, row in group.iterrows():
            archivo = row["ARCHIVO"]
            key_confirm = f"confirm_del_{archivo}"
            total_tx = int(row["TRANSACCIONES"] or 0)
            conc_tx = int(row["CONCILIADAS"] or 0)
            fully_conc = total_tx > 0 and conc_tx == total_tx
            any_conc = conc_tx > 0
            dot = "🟢" if fully_conc else "🔴"
            card = f"••{row['TARJETA']}" if row.get("TARJETA") else "—"

            c1, c2, c3, c4, c5, c6, c7 = st.columns([1.2, 1.6, 2, 2, 2, 1, 1])
            c1.write(card)
            c2.write(row["FECHA_ESTADO"])
            c3.write(row["TITULAR"] or "—")
            c4.write(f"{_fmt_deuda(row)} {row['MONEDA']}")
            c5.write(f"{dot} {conc_tx}/{total_tx} conciliadas")

            if st.session_state.get(key_confirm):
                with c6:
                    if st.button("Confirmar", key=f"ok_{archivo}", type="primary"):
                        try:
                            delete_estado_cuenta(conn, archivo)
                            st.session_state.pop(key_confirm, None)
                            st.success(f"Eliminado: {archivo}")
                            st.rerun()
                        except Exception as e:
                            st.session_state.pop(key_confirm, None)
                            st.error(str(e))
                with c7:
                    if st.button("Cancelar", key=f"cancel_{archivo}"):
                        st.session_state.pop(key_confirm, None)
                        st.rerun()
            else:
                with c7:
                    if any_conc:
                        st.button(
                            "🗑️",
                            key=f"del_{archivo}",
                            disabled=True,
                            help=(
                                f"No se puede eliminar: {conc_tx} transacción(es) "
                                "ya están conciliadas."
                            ),
                        )
                    elif st.button("🗑️", key=f"del_{archivo}", help=f"Eliminar {archivo}"):
                        st.session_state[key_confirm] = True
                        st.rerun()

    _render_group("🇨🇱 Nacional", df[df["ORIGEN"] == "NACIONAL"])
    st.markdown("")
    _render_group("🌎 Internacional", df[df["ORIGEN"] == "INTERNACIONAL"])


# ============================================================
# Dashboard
# ============================================================
def show_dashboard(df_db: pd.DataFrame, conn=None) -> None:
    st.header("📈 Dashboard")

    if df_db is None or df_db.empty:
        st.info("No hay transacciones aún.")
        return

    df = _parse_dates(df_db)
    for c in ("MONTO_TOTAL", "MONTO_OPERACION"):
        df[c] = pd.to_numeric(df.get(c), errors="coerce").fillna(0.0)

    # ---- Filters ----
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        origenes = sorted(df["ORIGEN"].dropna().unique().tolist())
        origen_sel = st.selectbox("Origen", ["Todos"] + origenes)
    with c2:
        tarjetas = sorted(df["TARJETA_ULT4"].dropna().unique().tolist())
        card_sel = st.selectbox(
            "Tarjeta",
            ["Todas"] + tarjetas,
            format_func=lambda v: v if v == "Todas" else f"••{v}",
        )
    with c3:
        df["MES"] = df["FECHA_DT"].dt.to_period("M").astype(str)
        meses = [m for m in sorted(df["MES"].dropna().unique()) if m != "NaT"]
        mes_sel = st.selectbox("Mes", ["Todos"] + meses)
    with c4:
        q = st.text_input("Buscar en descripción", value="")

    if origen_sel != "Todos":
        df = df[df["ORIGEN"] == origen_sel]
    if card_sel != "Todas":
        df = df[df["TARJETA_ULT4"] == card_sel]
    if mes_sel != "Todos":
        df = df[df["MES"] == mes_sel]
    if q.strip():
        df = df[df["DESCRIPCION"].astype(str).str.contains(q.strip(), case=False, na=False)]

    if df.empty:
        st.warning("No hay transacciones con esos filtros.")
        return

    # ---- KPIs, separated by currency ----
    n_nac = n_intl = 0
    if conn is not None:
        from data.database import fetch_estados_cuenta

        ec_cols, ec_rows = fetch_estados_cuenta(conn)
        ec_all = pd.DataFrame(ec_rows, columns=ec_cols)
        if not ec_all.empty:
            n_nac = int((ec_all["ORIGEN"] == "NACIONAL").sum())
            n_intl = int((ec_all["ORIGEN"] == "INTERNACIONAL").sum())

    def _kpis(label: str, subset: pd.DataFrame, monto_col: str, fmt, n_files: int) -> None:
        gastos = subset[subset[monto_col] > 0]
        pagos = subset[subset[monto_col] < 0]
        count = int(len(gastos))
        conc = int((gastos.get("CONCILIADO", 0) == 1).sum())
        kame = int((gastos.get("FACT_KAME", 0) == 1).sum())

        st.markdown(f"**{label}** · {n_files} cartolas")
        k = st.columns(6)
        k[0].metric("Total", fmt(float(gastos[monto_col].sum())))
        k[1].metric("Transacciones", str(count))
        k[2].metric(
            "Promedio", fmt(float(gastos[monto_col].mean()) if count else 0.0)
        )
        k[3].metric("Conciliadas", f"{conc}/{count}")
        k[4].metric("En Kame", f"{kame}/{count}")
        k[5].metric("Pagado TC", fmt(abs(float(pagos[monto_col].sum()))))

    df_nac = df[df["ORIGEN"] == "NACIONAL"]
    df_intl = df[df["ORIGEN"] == "INTERNACIONAL"]

    if not df_nac.empty:
        _kpis("🇨🇱 Nacional (CLP)", df_nac, "MONTO_TOTAL", lambda v: f"${v:,.0f}", n_nac)
        st.markdown("")
    if not df_intl.empty:
        _kpis(
            "🌎 Internacional (US$)",
            df_intl,
            "MONTO_OPERACION",
            lambda v: f"${v:,.2f}",
            n_intl,
        )
        st.markdown("")

    is_intl_only = origen_sel == "INTERNACIONAL"
    monto_col = "MONTO_OPERACION" if is_intl_only else "MONTO_TOTAL"
    cur = "US$" if is_intl_only else "CLP"
    df_gastos = df[df[monto_col] > 0]

    def _fmt(v) -> str:
        return f"${v:,.2f}" if is_intl_only else f"${v:,.0f}"

    st.markdown("---")

    # ---- Charts ----
    if _HAS_PLOTLY and not df_gastos.empty:
        top = (
            df_gastos.groupby("DESCRIPCION")[monto_col]
            .sum()
            .sort_values(ascending=False)
            .head(10)
            .reset_index()
        )
        fig = px.bar(
            top,
            x=monto_col,
            y="DESCRIPCION",
            orientation="h",
            title="🏪 Top 10 por gasto",
            labels={monto_col: f"Monto ({cur})", "DESCRIPCION": ""},
        )
        fig.update_layout(yaxis=dict(categoryorder="total ascending"))
        st.plotly_chart(fig, use_container_width=True)

        if df["MES"].nunique() > 1:
            mensual = df_gastos.groupby("MES")[monto_col].sum().reset_index()
            fig2 = px.line(
                mensual, x="MES", y=monto_col, markers=True, title="📆 Evolución mensual"
            )
            st.plotly_chart(fig2, use_container_width=True)

    # ---- Spend by category ----
    st.markdown("### 🗂️ Resumen por Tipo de Gasto")
    df_con_tipo = df_gastos[df_gastos["TIPO_GASTO"].fillna("") != ""]
    df_sin_tipo = df_gastos[df_gastos["TIPO_GASTO"].fillna("") == ""]

    if df_con_tipo.empty:
        st.info("No hay transacciones con Tipo de Gasto asignado.")
    else:
        resumen = (
            df_con_tipo.groupby("TIPO_GASTO")
            .agg(Transacciones=(monto_col, "count"), Total=(monto_col, "sum"))
            .sort_values("Total", ascending=False)
            .reset_index()
        )
        resumen.columns = ["Tipo de Gasto", "Transacciones", f"Total ({cur})"]
        resumen[f"Total ({cur})"] = resumen[f"Total ({cur})"].apply(_fmt)
        total_row = pd.DataFrame(
            [
                {
                    "Tipo de Gasto": "TOTAL",
                    "Transacciones": int(resumen["Transacciones"].sum()),
                    f"Total ({cur})": _fmt(df_con_tipo[monto_col].sum()),
                }
            ]
        )
        st.dataframe(
            pd.concat([resumen, total_row], ignore_index=True),
            use_container_width=True,
            hide_index=True,
        )
        if len(df_sin_tipo) > 0:
            st.caption(f"⚠️ {len(df_sin_tipo)} transacción(es) sin Tipo de Gasto asignado.")

    st.markdown("---")
    with st.expander("📋 Ver tabla filtrada"):
        preferred = [
            "ORIGEN", "TARJETA_ULT4", "FECHA_OPERACION", "DESCRIPCION",
            "CIUDAD", "PAIS", "MONTO_OPERACION", "MONTO_TOTAL", "MONEDA",
            "TIPO_GASTO", "CONCILIADO", "FACT_KAME", "TRASPASADO", "ARCHIVO_ORIGEN",
        ]
        show_cols = [c for c in preferred if c in df.columns]
        st.dataframe(
            df.sort_values("FECHA_DT", na_position="last")[show_cols],
            use_container_width=True,
            hide_index=True,
        )

    st.markdown("---")
    st.subheader("📂 Archivos cargados")
    if conn is not None:
        show_archivos(conn)
    else:
        st.info("Conexión no disponible.")
