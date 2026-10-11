from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .common import build_archivo_origen, dividir_en_cartolas, extraer_identidad

# ============================================================
# Parser for BCI "Estado de Cuenta Nacional" (CLP).
# Transaction line shape (after the "2. PERIODO ACTUAL" header):
#   [LUGAR] FECHA CODIGO_REF DESCRIPCION $ MONTO_OP $ MONTO_TOTAL [N°CUOTA $ VALOR]
# ============================================================

# Matches a CLP transaction line, anchored on the operation date.
# Trailing "N°CUOTA $ VALOR" is present on installment purchases: the columns are
# MONTO OPERACION | MONTO TOTAL A PAGAR | N° CUOTA | VALOR CUOTA MENSUAL.
LINE_RE = re.compile(
    r"(?P<fecha>\d{2}/\d{2}/\d{2})\s+"
    r"(?:(?P<codigo>\d{6,})\s+)?"
    r"(?P<desc>.+?)\s+"
    r"\$\s*(?P<m1>-?\d{1,3}(?:\.\d{3})*)\s+"
    r"\$\s*(?P<m2>-?\d{1,3}(?:\.\d{3})*)"
    r"(?:\s+(?P<cuota>\d{2}/\d{2})\s+\$\s*(?P<m_cuota>-?\d{1,3}(?:\.\d{3})*))?"
)

PERIODO_RE = re.compile(
    r"PERIODO\s+FACTURADO\s+(?P<desde>\d{2}[-/]\d{2}[-/]\d{4})\s+(?P<hasta>\d{2}[-/]\d{2}[-/]\d{4})",
    re.IGNORECASE,
)
DEUDA_RE = re.compile(
    r"MONTO TOTAL FACTURADO A PAGAR.*?\$\s*(?P<monto>-?\d{1,3}(?:\.\d{3})*)",
    re.IGNORECASE,
)

# Lines we never treat as expense transactions.
SKIP_PREFIXES = (
    "TOTAL", "SUBTOTAL", "LUGAR", "OPERACI", "PERIODO", "SALDO",
    "MONTO", "1.", "2.", "3.", "4.", "I.", "II.", "III.", "IV.",
)

def normalizar_monto_clp(valor_str: str) -> Optional[int]:
    s = valor_str.replace("$", "").replace(".", "").strip()
    try:
        return int(s)
    except ValueError:
        return None


def _ddmmyy_to_mmddyy(ddmmyy: str) -> str:
    try:
        dd, mm, yy = ddmmyy.split("/")
        return f"{mm}/{dd}/{yy}"
    except ValueError:
        return ddmmyy


def _extract_header(full_text: str) -> Dict[str, Any]:
    """Cardholder / card / dates / total for the statement."""
    ident = extraer_identidad(full_text)

    periodo_desde = periodo_hasta = None
    m = PERIODO_RE.search(full_text)
    if m:
        periodo_desde = m.group("desde").replace("/", "-")
        periodo_hasta = m.group("hasta").replace("/", "-")

    deuda_total = None
    m = DEUDA_RE.search(full_text)
    if m:
        deuda_total = normalizar_monto_clp(m.group("monto"))

    ident.update(
        {
            "PERIODO_DESDE": periodo_desde,
            "PERIODO_HASTA": periodo_hasta,
            "DEUDA_TOTAL": float(deuda_total) if deuda_total is not None else None,
        }
    )
    return ident


def parse_nacional(
    page_texts: List[str], filename: str = "archivo.pdf"
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Parse the pages of exactly one national statement.

    Callers must pass the pages of a single statement — see
    `common.dividir_en_cartolas`. Sweeping a merged archive through here
    attributes every transaction in it to the first header's month.
    """
    rows: List[Dict[str, Any]] = []
    full_text = "\n".join(page_texts)

    hdr = _extract_header(full_text)
    titular = hdr["TITULAR_NOMBRE"]
    ult4 = hdr["TARJETA_ULT4"]
    archivo_origen = build_archivo_origen(
        "BCI_NAC", ult4, titular, hdr["FECHA_ESTADO"], filename
    )

    for text in page_texts:
        for raw in text.splitlines():
            line = " ".join(raw.split())
            if not line:
                continue
            if line.upper().startswith(SKIP_PREFIXES):
                continue

            m = LINE_RE.search(line)
            if not m:
                continue

            desc = re.sub(r"\s{2,}", " ", m.group("desc").strip())
            if not desc or desc.upper().startswith("TOTAL"):
                continue

            monto_op = normalizar_monto_clp(m.group("m1"))
            # On an installment purchase only the monthly cuota is billed
            # this period; m2 is the whole remaining debt for that purchase.
            # Single-payment lines print as 01/01 with the same amount.
            cuota_num = m.group("cuota") or ""
            cuota_val = m.group("m_cuota")
            if cuota_num.startswith("00/"):
                # Cuota zero: the purchase is on the statement but its first
                # installment is not billed until next period. Charging it now
                # overstates the month and breaks the statement's own total.
                monto_total = 0
            elif cuota_val is not None:
                monto_total = normalizar_monto_clp(cuota_val)
            else:
                monto_total = normalizar_monto_clp(m.group("m2"))

            rows.append(
                {
                    "ORIGEN": "NACIONAL",
                    "TITULAR_NOMBRE": titular,
                    "TARJETA_ULT4": ult4,
                    "FECHA_OPERACION": _ddmmyy_to_mmddyy(m.group("fecha")),
                    "DESCRIPCION": desc,
                    "CIUDAD": "",
                    "PAIS": "",
                    "REF_INTERNACIONAL": m.group("codigo") or "",
                    "MONTO_ORIGEN": None,
                    "MONTO_OPERACION": monto_op,
                    "MONTO_TOTAL": monto_total,
                    "MONEDA": "CLP",
                    "TIPO_GASTO": "",
                    "CONCILIADO": 0,
                    "FACT_KAME": 0,
                    "TRASPASADO": 0,
                    "ARCHIVO_ORIGEN": archivo_origen,
                    "CUOTA": m.group("cuota") or "",
                }
            )

    meta = {
        "ORIGEN": "NACIONAL",
        "TITULAR_NOMBRE": titular,
        "TITULAR_COMPLETO": hdr["TITULAR_COMPLETO"],
        "TARJETA_ULT4": ult4,
        "ARCHIVO_ORIGEN": archivo_origen,
        "FECHA_ESTADO": hdr["FECHA_ESTADO"],
        "PERIODO_DESDE": hdr["PERIODO_DESDE"],
        "PERIODO_HASTA": hdr["PERIODO_HASTA"],
        "DEUDA_TOTAL": hdr["DEUDA_TOTAL"],
        "MONEDA": "CLP",
    }
    return rows, meta


def leer_cartola_nacional(
    pdf_bytes: bytes, filename: str = "archivo.pdf"
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Extract the first national statement in the PDF, with its metadata."""
    for tipo, page_texts in dividir_en_cartolas(pdf_bytes):
        if tipo == "NACIONAL":
            return parse_nacional(page_texts, filename)
    return [], {}
