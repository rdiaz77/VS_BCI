import re
import pdfplumber

# === REGEX ORIGINAL (FUNCIONAL) ===
line_pattern = re.compile(
    r"(?P<fecha>\d{2}/\d{2}/\d{2})\s+"
    r"(?:\d{9,}\s+)?"
    r"(?P<desc>.+?)\s+\$\s*(-?\d{1,3}(?:\.\d{3})*)"
    r"\s+\$\s*(-?\d{1,3}(?:\.\d{3})*)"
)


def normalizar_monto(valor_str: str):
    """Convierte montos tipo '1.234' a int."""
    valor_str = valor_str.replace(".", "").replace("$", "").strip()
    try:
        return int(valor_str)
    except ValueError:
        return None


def formatear_miles(valor_int: int):
    """Da formato con separador de miles."""
    if valor_int is None:
        return ""
    return f"${valor_int:,}"


def leer_cartola(file_like, filename="archivo.pdf"):
    """Extrae transacciones desde una cartola PDF (subida o local)."""
    rows = []
    with pdfplumber.open(file_like) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith(("LUGAR", "OPERACIÓN", "TOTAL", "III.", "II.", "I.")):
                    continue
                match = line_pattern.search(line)
                if match:
                    fecha = match.group("fecha")
                    descripcion = re.sub(
                        r"\s{2,}", " ", match.group("desc").strip())
                    monto_op_int = normalizar_monto(match.group(3))
                    monto_total_int = normalizar_monto(match.group(4))
                    rows.append({
                        "FECHA_OPERACION": fecha,
                        "DESCRIPCION": descripcion,
                        "MONTO_OPERACION": monto_op_int,
                        "MONTO_TOTAL": monto_total_int,
                        "ARCHIVO_ORIGEN": filename
                    })
    return rows
# === FIN REGEX ORIGINAL (FUNCIONAL) ===
