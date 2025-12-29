import streamlit as st
import pandas as pd
import plotly.express as px


def show_dashboard(df_db: pd.DataFrame):
    st.header("📈 Dashboard de Gastos BCI")

    if df_db.empty:
        st.info("No hay datos disponibles para el análisis.")
        return

    # --- Preprocesamiento ---
    df = df_db.copy()
    df["FECHA_OPERACION"] = pd.to_datetime(
        df["FECHA_OPERACION"], format="%m/%d/%y", errors="coerce"
    )
    df.loc[df["FECHA_OPERACION"].isna(), "FECHA_OPERACION"] = pd.to_datetime(
        df.loc[df["FECHA_OPERACION"].isna(), "FECHA_OPERACION"], format="%d/%m/%y", errors="coerce"
    )
    df = df.dropna(subset=["FECHA_OPERACION"])
    df["MES"] = df["FECHA_OPERACION"].dt.to_period("M").astype(str)
    df["CONCILIADO"] = df.get("CONCILIADO", 0).astype(int)

    # === NUEVO FILTRO POR TITULAR ===
    if "TITULAR" not in df.columns:
        df["TITULAR"] = (
            df["ARCHIVO_ORIGEN"]
            .str.extract(r"BCI_([A-Za-zÁÉÍÓÚÑ_]+)_")
            .iloc[:, 0]
            .str.replace("_", " ")
            .str.title()
        )

    titulares = sorted(df["TITULAR"].dropna().unique())
    titular_seleccionado = st.selectbox(
        "👤 Selecciona titular (opcional)", ["Todos"] + titulares
    )

    if titular_seleccionado != "Todos":
        df = df[df["TITULAR"] == titular_seleccionado]

    # --- Filtros de periodo / búsqueda ---
    col1, col2 = st.columns([1, 2])
    with col1:
        meses = sorted(df["MES"].unique(), reverse=True)
        mes_seleccionado = st.selectbox("🗓️ Selecciona mes", ["Todos"] + meses)
    with col2:
        busqueda = st.text_input("🔍 Buscar comercio o descripción")

    # Aplicar filtros
    df_filtrado = df.copy()
    if mes_seleccionado != "Todos":
        df_filtrado = df_filtrado[df_filtrado["MES"] == mes_seleccionado]
    if busqueda:
        df_filtrado = df_filtrado[
            df_filtrado["DESCRIPCION"].str.contains(busqueda, case=False, na=False)
        ]

    # --- KPIs ---
    total_gasto = df_filtrado["MONTO_OPERACION"].sum()
    num_trans = len(df_filtrado)
    promedio = df_filtrado["MONTO_OPERACION"].mean() if num_trans > 0 else 0
    conciliadas = df_filtrado["CONCILIADO"].sum()
    pendientes = num_trans - conciliadas

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("💰 Gasto total", f"${total_gasto:,.0f}")
    c2.metric("🧾 N° transacciones", f"{num_trans}")
    c3.metric("💳 Promedio por compra", f"${promedio:,.0f}")
    c4.metric("✅ Conciliadas", f"{conciliadas}/{num_trans}" if num_trans > 0 else "0/0")

    st.markdown("---")

    # --- Gráficos ---
    if not df_filtrado.empty:
        # Gasto por comercio
        top_comercios = (
            df_filtrado.groupby("DESCRIPCION")["MONTO_OPERACION"]
            .sum()
            .sort_values(ascending=False)
            .head(10)
            .reset_index()
        )
        fig_top = px.bar(
            top_comercios,
            x="MONTO_OPERACION",
            y="DESCRIPCION",
            orientation="h",
            title="🏪 Top 10 Comercios por Gasto",
            labels={"MONTO_OPERACION": "Monto", "DESCRIPCION": "Comercio"},
        )
        fig_top.update_layout(yaxis=dict(categoryorder="total ascending"))
        st.plotly_chart(fig_top, use_container_width=True)

        # Gasto mensual (solo si hay varios meses)
        if df["MES"].nunique() > 1:
            mensual = df.groupby("MES")["MONTO_OPERACION"].sum().reset_index()
            fig_mes = px.line(
                mensual,
                x="MES",
                y="MONTO_OPERACION",
                markers=True,
                title="📆 Evolución Mensual del Gasto",
                labels={"MONTO_OPERACION": "Monto", "MES": "Mes"},
            )
            st.plotly_chart(fig_mes, use_container_width=True)

        # Conciliación pie chart
        st.markdown("### 🔄 Estado de conciliación")
        reconc_data = pd.DataFrame({
            "Estado": ["Conciliadas", "Pendientes"],
            "Cantidad": [conciliadas, pendientes]
        })
        fig_reconc = px.pie(
            reconc_data,
            names="Estado",
            values="Cantidad",
            color="Estado",
            title="Proporción de transacciones conciliadas",
            hole=0.4,
        )
        st.plotly_chart(fig_reconc, use_container_width=True)

        # Tabla de detalle
        with st.expander("📋 Ver transacciones filtradas"):
            st.dataframe(df_filtrado, use_container_width=True)

    else:
        st.warning("⚠️ No hay transacciones que coincidan con los filtros seleccionados.")
# === FIN dashboard.py ===