import os
import re
import pdfplumber
import pandas as pd
import streamlit as st
from io import BytesIO

# === CONFIGURACIÓN STREAMLIT ===
st.set_page_config(page_title="Cartolas BCI Extractor", layout="wide")
st.write("🚀 App loaded successfully — initializing UI...")

# === 🔐 LOGIN OPCIONAL ===
st.title("🔒 Cartolas BCI Extractor - Login")

APP_PASSWORD = None
password_source = None

# Try to get password from secrets or environment
try:
    APP_PASSWORD = st.secrets["general"]["app_password"]
    password_source = "Streamlit Cloud secrets"
except Exception:
    if "APP_PASSWORD" in os.environ:
        APP_PASSWORD = os.environ["APP_PASSWORD"]
        password_source = "environment variable"

if APP_PASSWORD:
    if "authenticated" not in st.session_state:
        st.session_state["authenticated"] = False

    if not st.session_state["authenticated"]:
        password = st.text_input("Introduce la contraseña:", type="password")
        if password == APP_PASSWORD:
            st.session_state["authenticated"] = True
            st.success("✅ Acceso concedido.")
            st.rerun()
        elif password:
            st.warning("❌ Contraseña incorrecta.")
            st.stop()
    else:
        st.info(f"🔓 Sesión activa (fuente: {password_source}).")
else:
    st.warning(
        "⚠️ No se configuró una contraseña. El acceso está abierto temporalmente.")

# === INTERFAZ PRINCIPAL ===
st.title("📊 Cartolas BCI Extractor")
st.write("Analiza tus cartolas de tarjeta de crédito BCI, sube PDFs o usa la carpeta local para generar un CSV agrupado.")

# === CONFIGURACIÓN DE RUTA LOCAL ===
base_path = st.text_input(
    "📂 Ruta base local de las cartolas (opcional para uso local)", "cartolas")
log_path = "procesados.txt"

# === REGEX PARA TRANSACCIONES ===
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


def leer_cartola(file_like, filename="archivo.pdf"):
    """Extrae transacciones desde una cartola PDF."""
    rows = []
    try:
        with pdfplumber.open(file_like) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                if not text:
                    continue
                for line in text.splitlines():
                    line = line.strip()
                    if line.startswith(("LUGAR", "OPERACIÓN", "TOTAL", "III.", "II.", "I.")) or not line:
                        continue
                    match = line_pattern.search(line)
                    if match:
                        fecha = match.group("fecha")
                        descripcion = re.sub(
                            r'\s{2,}', ' ', match.group("desc").strip())
                        monto_op_int = normalizar_monto(match.group(3))
                        monto_total_int = normalizar_monto(match.group(4))
                        rows.append({
                            "FECHA OPERACIÓN": fecha,
                            "DESCRIPCIÓN OPERACIÓN O COBRO": descripcion,
                            "MONTO OPERACIÓN O COBRO": formatear_miles(monto_op_int),
                            "MONTO TOTAL A PAGAR": formatear_miles(monto_total_int),
                            "ARCHIVO ORIGEN": filename
                        })
    except Exception as e:
        st.error(f"❌ Error leyendo {filename}: {e}")
    return rows


def procesar_dataframe(df):
    """Limpia y agrega resumen de datos."""
    df["MONTO_TOTAL_INT"] = (
        df["MONTO TOTAL A PAGAR"]
        .replace("[\$,]", "", regex=True)
        .astype(float)
    )
    df["FECHA OPERACIÓN"] = pd.to_datetime(
        df["FECHA OPERACIÓN"], format="%d/%m/%y", errors="coerce"
    )
    df.drop_duplicates(inplace=True)
    total = df["MONTO_TOTAL_INT"].sum()
    st.metric("💰 Total monto a pagar", f"${total:,.0f}")
    return df


# === SUBIR PDF ===
uploaded_files = st.file_uploader(
    "📤 Sube tus cartolas en PDF (puedes arrastrarlas aquí):",
    type=["pdf"],
    accept_multiple_files=True
)

if uploaded_files:
    all_data = []
    st.info(f"Procesando {len(uploaded_files)} archivo(s)...")

    for uploaded_file in uploaded_files:
        pdf_bytes = BytesIO(uploaded_file.read())
        rows = leer_cartola(pdf_bytes, uploaded_file.name)
        if not rows:
            st.warning(
                f"⚠️ No se encontraron transacciones en {uploaded_file.name}")
            continue
        all_data.extend(rows)
        st.success(
            f"✅ {len(rows)} transacciones extraídas de {uploaded_file.name}")

    if all_data:
        df = pd.DataFrame(all_data)
        df = procesar_dataframe(df)
        st.dataframe(
            df.drop(columns=["MONTO_TOTAL_INT"]), use_container_width=True)

        csv_output = df.drop(columns=["MONTO_TOTAL_INT"]).to_csv(
            index=False).encode("utf-8")
        st.download_button(
            label="💾 Descargar CSV generado",
            data=csv_output,
            file_name="cartolas_bci_extraidas.csv",
            mime="text/csv"
        )

else:
    st.write("O usa el siguiente botón para procesar las cartolas desde tu carpeta local (modo offline):")

    if st.button("▶️ Procesar cartolas locales"):
        if not os.path.exists(base_path):
            st.error(
                "❌ La ruta ingresada no existe o no está disponible en la nube.")
        else:
            all_data = []
            processed_files = set()

            if os.path.exists(log_path):
                with open(log_path, "r") as f:
                    processed_files = set(f.read().splitlines())

            with st.spinner("Procesando PDFs locales..."):
                for root, _, files in os.walk(base_path):
                    for fname in files:
                        if fname.lower().endswith(".pdf") and fname not in processed_files:
                            full_path = os.path.join(root, fname)
                            with open(full_path, "rb") as f:
                                rows = leer_cartola(f, fname)
                                if rows:
                                    all_data.extend(rows)
                                    with open(log_path, "a") as logf:
                                        logf.write(f"{fname}\n")

            if not all_data:
                st.warning("⚠️ No se encontraron transacciones nuevas.")
            else:
                st.success(
                    f"✅ {len(all_data)} transacciones encontradas en PDFs locales.")
                df = pd.DataFrame(all_data)
                df = procesar_dataframe(df)
                st.dataframe(
                    df.drop(columns=["MONTO_TOTAL_INT"]), use_container_width=True)

                csv_output = df.drop(columns=["MONTO_TOTAL_INT"]).to_csv(
                    index=False).encode("utf-8")
                st.download_button(
                    label="💾 Descargar CSV generado",
                    data=csv_output,
                    file_name="cartolas_bci_locales.csv",
                    mime="text/csv"
                )

# === FIN DEL SCRIPT ===
