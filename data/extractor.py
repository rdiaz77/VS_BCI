import re
import pdfplumber

# === REGEXS ===
# Detects transaction lines
line_pattern = re.compile(
    r"(?P<fecha>\d{2}/\d{2}/\d{2})\s+"
    r"(?:\d{9,}\s+)?"
    r"(?P<desc>.+?)\s+\$\s*(-?\d{1,3}(?:\.\d{3})*)"
    r"\s+\$\s*(-?\d{1,3}(?:\.\d{3})*)"
)

# Detect header info
titular_pattern = re.compile(
    r"NOMBRE\s+DEL\s+TITULAR\s*(?P<nombre>[A-ZÁÉÍÓÚÑ\s]+)",
    re.IGNORECASE
)
fecha_estado_pattern = re.compile(
    r"FECHA\s+ESTADO\s+DE\s+CUENTA\s*(?P<fecha>\d{2}[-/]\d{2}[-/]\d{4})",
    re.IGNORECASE
)


def normalizar_monto(valor_str: str):
    """Convierte montos tipo '1.234' a int."""
    valor_str = valor_str.replace(".", "").replace("$", "").strip()
    try:
        return int(valor_str)
    except ValueError:
        return None


def leer_cartola(file_like, filename="archivo.pdf"):
    """Extrae transacciones desde una cartola PDF (subida o local)."""
    rows = []
    titular = "Desconocido"
    fecha_estado = "SinFecha"

    with pdfplumber.open(file_like) as pdf:
        # --- Buscar nombre y fecha en la primera página ---
        first_page_text = pdf.pages[0].extract_text() or ""
        match_titular = titular_pattern.search(first_page_text)
        match_fecha = fecha_estado_pattern.search(first_page_text)

        if match_titular:
            titular = match_titular.group("nombre").strip().title()
        if match_fecha:
            fecha_estado = match_fecha.group("fecha").replace("/", "-")

        # Build Archivo de origen with fallback
        if titular != "Desconocido" and fecha_estado != "SinFecha":
            archivo_origen = f"BCI_{titular.replace(' ', '_')}_{fecha_estado}"
        else:
            archivo_origen = filename  # fallback

        # --- Extraer transacciones ---
        for page in pdf.pages:
            text = page.extract_text() or ""
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith(("LUGAR", "OPERACIÓN", "TOTAL", "III.", "II.", "I.")):
                    continue
                match = line_pattern.search(line)
                if match:
                    fecha = match.group("fecha")
                    descripcion = re.sub(r"\s{2,}", " ", match.group("desc").strip())
                    monto_op_int = normalizar_monto(match.group(3))
                    monto_total_int = normalizar_monto(match.group(4))
                    rows.append({
                        "FECHA_OPERACION": fecha,
                        "DESCRIPCION": descripcion,
                        "MONTO_OPERACION": monto_op_int,
                        "MONTO_TOTAL": monto_total_int,
                        "ARCHIVO_ORIGEN": archivo_origen
                    })

    return rows
# === FIN data/extractor.py ===
