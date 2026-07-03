import pandas as pd
import streamlit as st

try:
    import plotly.express as px
    _HAS_PLOTLY = True
except Exception:
    _HAS_PLOTLY = False


def _parse_dates(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["FECHA_DT"] = pd.to_datetime(out["FECHA_OPERACION"], format="%m/%d/%y", errors="coerce")
    return out


def show_archivos(conn) -> None:
    """Table of uploaded statements — shown at the top of the dashboard."""
    from data.database import fetch_archivos_resumen, delete_estado_cuenta

    cols, rows = fetch_archivos_resumen(conn)
    if not rows:
        st.info("No hay archivos cargados aún.")
        return

    df = pd.DataFrame(rows, columns=cols)

    # Sort by FECHA_ESTADO (DD-MM-YYYY) chronologically
    df["_fecha_dt"] = pd.to_datetime(df["FECHA_ESTADO"], format="%d-%m-%Y", errors="coerce")
    df = df.sort_values("_fecha_dt", ascending=False).drop(columns=["_fecha_dt"]).reset_index(drop=True)

    # Format deuda_total: CLP integer, USD 2 decimals
    def _fmt_deuda(row):
        try:
            v = float(row["DEUDA_TOTAL"])
            return f"{v:,.0f}" if row["MONEDA"] == "CLP" else f"{v:,.2f}"
        except Exception:
            return ""

    df["DEUDA_TOTAL"] = df.apply(_fmt_deuda, axis=1)
    df["TRASPASO_ESTADO"] = df["TRASPASO_ESTADO"].map(
        lambda s: "✅ Traspasado" if s == "TRASPASADO" else "⏳ Pendiente"
    )

    def _render_group(label: str, group_df: pd.DataFrame) -> None:
        st.markdown(f"**{label}** ({len(group_df)})")
        if group_df.empty:
            st.caption("Sin archivos.")
            return
        for _, row in group_df.iterrows():
            archivo = row["ARCHIVO"]
            key_confirm = f"confirm_del_{archivo}"

            c1, c2, c3, c4, c5, c6, c7 = st.columns([2, 2, 2, 2, 2, 1, 1])
            c1.write(row["FECHA_ESTADO"])
            c2.write(row["TITULAR"])
            c3.write(f"{row['DEUDA_TOTAL']} {row['MONEDA']}")
            c4.write(row["TRASPASO_ESTADO"])
            c5.write(f"{int(row['TRANSACCIONES'])} transacciones")

            if st.session_state.get(key_confirm):
                with c6:
                    if st.button("Confirmar", key=f"ok_{archivo}", type="primary"):
                        try:
                            delete_estado_cuenta(conn, archivo)
                            st.session_state.pop(key_confirm, None)
                            st.success(f"Eliminado: {archivo}")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Error al eliminar: {e}")
                with c7:
                    if st.button("Cancelar", key=f"cancel_{archivo}"):
                        st.session_state.pop(key_confirm, None)
                        st.rerun()
            else:
                with c7:
                    if st.button("🗑️", key=f"del_{archivo}", help=f"Eliminar {archivo}"):
                        st.session_state[key_confirm] = True
                        st.rerun()

    nac  = df[df["ORIGEN"] == "NACIONAL"].drop(columns=["ORIGEN"])
    intl = df[df["ORIGEN"] == "INTERNACIONAL"].drop(columns=["ORIGEN"])

    _render_group("🇨🇱 Nacional", nac)
    st.markdown("")
    _render_group("🌎 Internacional", intl)


def show_dashboard(df_db: pd.DataFrame, conn=None) -> None:
    st.header("📈 Dashboard")

    if df_db is None or df_db.empty:
        st.info("No hay transacciones aún.")
        return

    df = _parse_dates(df_db)
    df["MONTO_TOTAL"]     = pd.to_numeric(df.get("MONTO_TOTAL"),     errors="coerce").fillna(0.0)
    df["MONTO_OPERACION"] = pd.to_numeric(df.get("MONTO_OPERACION"), errors="coerce").fillna(0.0)

    # ── Filters ───────────────────────────────────────────────
    c1, c2, c3 = st.columns(3)
    with c1:
        origenes = sorted(df["ORIGEN"].dropna().unique().tolist())
        origen_sel = st.selectbox("Origen", ["Todos"] + origenes)
    with c2:
        df["MES"] = df["FECHA_DT"].dt.to_period("M").astype(str)
        meses = [m for m in sorted(df["MES"].dropna().unique()) if m != "NaT"]
        mes_sel = st.selectbox("Mes", ["Todos"] + meses)
    with c3:
        q = st.text_input("Buscar en descripción", value="")

    if origen_sel != "Todos":
        df = df[df["ORIGEN"] == origen_sel]
    if mes_sel != "Todos":
        df = df[df["MES"] == mes_sel]
    if q.strip():
        df = df[df["DESCRIPCION"].astype(str).str.contains(q.strip(), case=False, na=False)]

    if df.empty:
        st.warning("No hay transacciones con esos filtros.")
        return

    is_intl_only = origen_sel == "INTERNACIONAL"
    monto_col = "MONTO_OPERACION" if is_intl_only else "MONTO_TOTAL"
    cur = "US$" if is_intl_only else "CLP"

    # ── KPIs ──────────────────────────────────────────────────
    # Exclude payments (negative amounts) from totals — they are TC payments, not expenses
    df_gastos = df[df[monto_col] > 0]
    total = float(df_gastos[monto_col].sum())
    count = int(len(df_gastos))
    avg   = float(df_gastos[monto_col].mean()) if count else 0.0
    conc  = int((df_gastos.get("CONCILIADO", 0) == 1).sum())
    kame  = int((df_gastos.get("FACT_KAME",  0) == 1).sum())

    # File counts from estados_cuenta (unaffected by transaction filters)
    n_nac  = n_intl = 0
    if conn is not None:
        from data.database import fetch_estados_cuenta
        ec_cols, ec_rows = fetch_estados_cuenta(conn)
        ec_all = pd.DataFrame(ec_rows, columns=ec_cols)
        n_nac  = int((ec_all["ORIGEN"] == "NACIONAL").sum())
        n_intl = int((ec_all["ORIGEN"] == "INTERNACIONAL").sum())

    r1c1, r1c2, r1c3, r1c4, r1c5 = st.columns(5)
    r1c1.metric(f"Total ({cur})",    f"${total:,.2f}" if is_intl_only else f"${total:,.0f}")
    r1c2.metric("Transacciones",     str(count))
    r1c3.metric(f"Promedio ({cur})", f"${avg:,.2f}"   if is_intl_only else f"${avg:,.0f}")
    r1c4.metric("Conciliadas",       f"{conc}/{count}")
    r1c5.metric("En Kame",           f"{kame}/{count}")

    desc = df["DESCRIPCION"].str.upper()

    df_pagos      = df[df[monto_col] < 0]
    df_comisiones = df_gastos[desc.str.contains("COMISION", na=False)]
    df_intereses  = df_gastos[desc.str.contains("INTERES", na=False)]
    df_impuestos  = df_gastos[desc.str.contains("IMPUESTO", na=False)]

    total_pagado    = float(df_pagos[monto_col].sum())
    total_comision  = float(df_comisiones[monto_col].sum())
    total_interes   = float(df_intereses[monto_col].sum())
    total_impuesto  = float(df_impuestos[monto_col].sum())

    def _fmt(v):
        return f"${v:,.2f}" if is_intl_only else f"${v:,.0f}"

    r2c1, r2c2, r2c3, _ = st.columns([1, 1, 1, 2])
    r2c1.metric("Archivos Nacional",      str(n_nac))
    r2c2.metric("Archivos Internacional", str(n_intl))
    r2c3.metric("Total pagado TC",        _fmt(abs(total_pagado)))

    r3c1, r3c2, r3c3, _ = st.columns([1, 1, 1, 2])
    r3c1.metric("Comisiones",  _fmt(total_comision))
    r3c2.metric("Intereses",   _fmt(total_interes))
    r3c3.metric("Impuestos",   _fmt(total_impuesto))

    st.markdown("---")

    # ── Charts ────────────────────────────────────────────────
    if _HAS_PLOTLY:
        top = (
            df.groupby("DESCRIPCION")[monto_col].sum()
            .sort_values(ascending=False).head(10).reset_index()
        )
        fig = px.bar(
            top, x=monto_col, y="DESCRIPCION", orientation="h",
            title="🏪 Top 10 por gasto",
            labels={monto_col: f"Monto ({cur})", "DESCRIPCION": ""},
        )
        fig.update_layout(yaxis=dict(categoryorder="total ascending"))
        st.plotly_chart(fig, use_container_width=True)

        if df["MES"].nunique() > 1:
            mensual = df.groupby("MES")[monto_col].sum().reset_index()
            fig2 = px.line(
                mensual, x="MES", y=monto_col, markers=True, title="📆 Evolución mensual"
            )
            st.plotly_chart(fig2, use_container_width=True)

    # ── Resumen por Tipo de Gasto ─────────────────────────────
    st.markdown("### 🗂️ Resumen por Tipo de Gasto")

    df_con_tipo = df_gastos[df_gastos["TIPO_GASTO"].fillna("") != ""].copy()
    df_sin_tipo = df_gastos[df_gastos["TIPO_GASTO"].fillna("") == ""].copy()

    if df_con_tipo.empty:
        st.info("No hay transacciones con Tipo de Gasto asignado.")
    else:
        resumen = (
            df_con_tipo.groupby("TIPO_GASTO")
            .agg(
                Transacciones=(monto_col, "count"),
                Total=(monto_col, "sum"),
            )
            .sort_values("Total", ascending=False)
            .reset_index()
        )
        resumen.columns = ["Tipo de Gasto", "Transacciones", f"Total ({cur})"]

        # Format total
        if is_intl_only:
            resumen[f"Total ({cur})"] = resumen[f"Total ({cur})"].apply(lambda v: f"${v:,.2f}")
        else:
            resumen[f"Total ({cur})"] = resumen[f"Total ({cur})"].apply(lambda v: f"${int(v):,}")

        # Totals row
        total_row = pd.DataFrame([{
            "Tipo de Gasto": "TOTAL",
            "Transacciones": int(resumen["Transacciones"].sum()),
            f"Total ({cur})": _fmt(df_con_tipo[monto_col].sum()),
        }])
        resumen = pd.concat([resumen, total_row], ignore_index=True)

        st.dataframe(resumen, use_container_width=True, hide_index=True)

        if len(df_sin_tipo) > 0:
            st.caption(f"⚠️ {len(df_sin_tipo)} transacción(es) sin Tipo de Gasto asignado.")

    st.markdown("---")

    with st.expander("📋 Ver tabla filtrada"):
        preferred = [
            "ORIGEN", "TITULAR_NOMBRE", "FECHA_OPERACION", "DESCRIPCION",
            "CIUDAD", "PAIS", "MONTO_OPERACION", "MONTO_TOTAL", "MONEDA",
            "TIPO_GASTO", "CONCILIADO", "FACT_KAME", "TRASPASADO", "ARCHIVO_ORIGEN",
        ]
        show_cols = [c for c in preferred if c in df.columns]
        st.dataframe(
            df.sort_values("FECHA_DT", na_position="last")[show_cols],
            use_container_width=True, hide_index=True,
        )

    # ── Uploaded files table ──────────────────────────────────
    st.markdown("---")
    st.subheader("📂 Archivos cargados")
    if conn is not None:
        show_archivos(conn)
    else:
        st.info("Conexión no disponible.")
