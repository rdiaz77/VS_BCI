from __future__ import annotations

import io
import re
from typing import Any, Dict, List, Optional, Tuple

import pdfplumber

from .common import build_archivo_origen, extraer_identidad

# ============================================================
# Parser for BCI "Estado de Cuenta Nacional" (CLP).
# Transaction line shape (after the "2. PERIODO ACTUAL" header):
#   [LUGAR] FECHA CODIGO_REF DESCRIPCION $ MONTO_OP $ MONTO_TOTAL [N°CUOTA $ VALOR]
# ============================================================

# Matches a CLP transaction line, anchored on the operation date.
LINE_RE = re.compile(
    r"(?P<fecha>\d{2}/\d{2}/\d{2})\s+"
    r"(?:(?P<codigo>\d{6,})\s+)?"
    r"(?P<desc>.+?)\s+"
    r"\$\s*(?P<m1>-?\d{1,3}(?:\.\d{3})*)\s+"
    r"\$\s*(?P<m2>-?\d{1,3}(?:\.\d{3})*)"
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


def leer_cartola_nacional(
    pdf_bytes: bytes, filename: str = "archivo.pdf"
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Extract national (CLP) transactions and statement metadata.

    Returns (rows, meta) where meta describes the statement (for estados_cuenta).
    """
    rows: List[Dict[str, Any]] = []

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        page_texts = [(p.extract_text() or "") for p in pdf.pages]
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
