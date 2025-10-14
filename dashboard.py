import streamlit as st
import pandas as pd


def show_dashboard(df_db: pd.DataFrame):
    """Render analytics dashboard based on database transactions."""
    if df_db.empty:
        st.info("No hay datos disponibles para el análisis aún.")
        return

    with st.expander("📊 Análisis de transacciones"):
        st.subheader("💰 Gasto mensual")

        df_db["FECHA_OPERACION"] = pd.to_datetime(
            df_db["FECHA_OPERACION"], format="%d/%m/%y", errors="coerce")
        df_monthly = (
            df_db.groupby(df_db["FECHA_OPERACION"].dt.to_period("M"))[
                "MONTO_OPERACION"]
            .sum()
            .reset_index()
        )
        df_monthly["FECHA_OPERACION"] = df_monthly["FECHA_OPERACION"].astype(
            str)
        st.bar_chart(df_monthly, x="FECHA_OPERACION", y="MONTO_OPERACION")

        st.subheader("🏬 Top 10 comercios o descripciones")
        df_top = (
            df_db.groupby("DESCRIPCION")["MONTO_OPERACION"]
            .sum()
            .sort_values(ascending=False)
            .head(10)
            .reset_index()
        )
        st.dataframe(df_top, use_container_width=True)
