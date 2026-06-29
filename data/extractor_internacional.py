from __future__ import annotations

import io
import re
from typing import Any, Dict, List, Optional, Tuple

import pdfplumber
from unidecode import unidecode

# ============================================================
# Parser for BCI "Estado de Cuenta Internacional" (USD).
# International transactions stay in USD; the whole statement
# balance (DEUDA TOTAL) is later transferred to a national
# statement as a single "TRASPASO DEUDA INTERNACIONAL" line.
# ============================================================

DATE_RE = re.compile(r"\b\d{2}/\d{2}/\d{2}\b")
PAIS_RE = re.compile(r"^[A-Z]{2}$")
REF_RE = re.compile(r"^\d{10,}$")
# Examples: 49,44 ; -17,35 ; 49.640,00
AMOUNT_RE = re.compile(r"^-?\d{1,3}(?:\.\d{3})*(?:,\d{2})$|^-?\d+(?:,\d{2})$")


def _norm(s: str) -> str:
    return unidecode(s).upper()


def _to_float(amount_str: str) -> float:
    s = amount_str.strip().replace("US$", "").replace("$", "")
    s = s.replace(".", "").replace(",", ".")
    return float(s)


def _ddmmyy_to_mmddyy(ddmmyy: str) -> str:
    dd, mm, yy = ddmmyy.split("/")
    return f"{mm}/{dd}/{yy}"


# =========================
# Header extraction
# =========================
def _extract_header_fields(full_text: str) -> Dict[str, Any]:
    titular_first = None
    m = re.search(
        r"NOMBRE DEL TITULAR\s+(.+?)\s+N°\s*DE\s*TARJETA", full_text, re.DOTALL | re.IGNORECASE
    )
    if m:
        nombre = " ".join(m.group(1).split()).strip()
        if nombre:
            titular_first = nombre.split()[0].title()

    fecha_estado = None
    m = re.search(r"FECHA ESTADO DE CUENTA\s+(\d{2}/\d{2}/\d{4})", full_text)
    if m:
        fecha_estado = m.group(1).replace("/", "-")

    periodo_desde = periodo_hasta = None
    m = re.search(r"PER[IÍ]ODO FACTURADO DESDE\s+(\d{2}/\d{2}/\d{4})", full_text, re.IGNORECASE)
    if m:
        periodo_desde = m.group(1).replace("/", "-")
    m = re.search(r"PER[IÍ]ODO FACTURADO HASTA\s+(\d{2}/\d{2}/\d{4})", full_text, re.IGNORECASE)
    if m:
        periodo_hasta = m.group(1).replace("/", "-")

    deuda_total = None
    m = re.search(r"DEUDA TOTAL\s+US\$\s*([\d.,]+)", full_text, re.IGNORECASE)
    if m:
        try:
            deuda_total = _to_float(m.group(1))
        except Exception:
            deuda_total = None

    return {
        "TITULAR_NOMBRE": titular_first,
        "FECHA_ESTADO": fecha_estado,
        "PERIODO_DESDE": periodo_desde,
        "PERIODO_HASTA": periodo_hasta,
        "DEUDA_TOTAL": deuda_total,
    }


def _build_archivo_origen(filename: str, titular: Optional[str], fecha_estado: Optional[str]) -> str:
    if titular and fecha_estado:
        return f"BCI_INT_{titular.replace(' ', '_')}_{fecha_estado}"
    return filename


def _find_trailing_amounts(tokens: List[str]) -> List[str]:
    trailing = []
    for t in reversed(tokens):
        if AMOUNT_RE.match(t):
            trailing.append(t)
        else:
            if trailing:
                break
    return list(reversed(trailing))


def _split_desc_city_pais(tokens_after_date: List[str], pais: str) -> Tuple[str, str]:
    if not pais:
        return (" ".join(tokens_after_date).strip(), "")
    try:
        idx = len(tokens_after_date) - 1 - list(reversed(tokens_after_date)).index(pais)
    except ValueError:
        return (" ".join(tokens_after_date).strip(), "")

    before_pais = tokens_after_date[:idx]
    if not before_pais:
        return ("", "")
    if len(before_pais) <= 1:
        return (" ".join(before_pais).strip(), "")

    city_tokens = before_pais[-3:] if len(before_pais) > 3 else before_pais[-1:]
    desc_tokens = before_pais[:-len(city_tokens)] if len(before_pais) > len(city_tokens) else []

    desc = " ".join(desc_tokens).strip()
    city = " ".join(city_tokens).strip()
    if not desc:
        desc = " ".join(before_pais).strip()
        city = ""
    return desc, city


def _parse_transaction_line(
    line: str, archivo_origen: str, titular_first_name: Optional[str]
) -> Optional[Dict[str, Any]]:
    tokens = line.split()
    try:
        date_idx = next(i for i, t in enumerate(tokens) if DATE_RE.fullmatch(t))
    except StopIteration:
        return None

    trailing = _find_trailing_amounts(tokens)
    if not trailing:
        return None

    monto_origen = trailing[-2] if len(trailing) >= 2 else None
    monto_usd = trailing[-1]
    desc_tokens = tokens[date_idx + 1 : len(tokens) - len(trailing)]

    pais = ""
    ciudad = ""
    if desc_tokens and PAIS_RE.match(desc_tokens[-1]):
        pais = desc_tokens[-1]
        desc, ciudad = _split_desc_city_pais(desc_tokens, pais)
    else:
        desc = " ".join(desc_tokens).strip()

    if not desc or desc.upper().startswith("TOTAL"):
        return None

    ref = ""
    if date_idx >= 2 and REF_RE.match(tokens[date_idx - 1]):
        ref = tokens[date_idx - 1]

    try:
        monto_usd_f = _to_float(monto_usd)
        monto_origen_f = _to_float(monto_origen) if monto_origen else None
    except Exception:
        return None

    return {
        "ORIGEN": "INTERNACIONAL",
        "TITULAR_NOMBRE": titular_first_name,
        "FECHA_OPERACION": _ddmmyy_to_mmddyy(tokens[date_idx]),
        "DESCRIPCION": desc,
        "CIUDAD": ciudad,
        "PAIS": pais,
        "REF_INTERNACIONAL": ref,
        "MONTO_ORIGEN": monto_origen_f,
        "MONTO_OPERACION": monto_usd_f,
        "MONTO_TOTAL": monto_usd_f,
        "MONEDA": "USD",
        "TIPO_GASTO": "",
        "CONCILIADO": 0,
        "FACT_KAME": 0,
        "TRASPASADO": 0,
        "ARCHIVO_ORIGEN": archivo_origen,
    }


def leer_cartola_internacional(
    pdf_bytes: bytes, filename: str = "archivo.pdf"
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        page_texts = [(p.extract_text() or "") for p in pdf.pages]
        full_text = "\n".join(page_texts)

        header = _extract_header_fields(full_text)
        titular_first = header["TITULAR_NOMBRE"]
        archivo_origen = _build_archivo_origen(filename, titular_first, header["FECHA_ESTADO"])

        in_transacciones = False
        in_comisiones = False

        for t in page_texts:
            for raw_line in t.splitlines():
                line = " ".join(raw_line.split())
                if not line:
                    continue
                u = _norm(line)

                if "2. INFORMACION DE TRANSACCIONES" in u:
                    in_transacciones = True
                    in_comisiones = False
                    continue
                if "COMISIONES, OTROS CARGOS Y ABONOS" in u:
                    in_comisiones = True
                    in_transacciones = False
                    continue
                if u.startswith("TOTAL TARJETA"):
                    in_transacciones = False
                    continue
                if u.startswith(
                    ("NUMERO", "FECHA", "DESCRIPCION", "CIUDAD", "PAIS",
                     "MONTO", "TOTAL DE PAGOS", "TOTAL DE COMPRAS")
                ):
                    continue
                if not (in_transacciones or in_comisiones):
                    continue
                if not DATE_RE.search(line):
                    continue

                row = _parse_transaction_line(line, archivo_origen, titular_first)
                if row:
                    rows.append(row)

    # Deduplicate
    uniq = {}
    for r in rows:
        key = (
            r["TITULAR_NOMBRE"], r["FECHA_OPERACION"], r["DESCRIPCION"],
            r.get("PAIS", ""), r["MONTO_OPERACION"], r["ARCHIVO_ORIGEN"],
        )
        uniq[key] = r
    rows = list(uniq.values())

    meta = {
        "ORIGEN": "INTERNACIONAL",
        "TITULAR_NOMBRE": titular_first,
        "ARCHIVO_ORIGEN": archivo_origen,
        "FECHA_ESTADO": header["FECHA_ESTADO"],
        "PERIODO_DESDE": header["PERIODO_DESDE"],
        "PERIODO_HASTA": header["PERIODO_HASTA"],
        "DEUDA_TOTAL": header["DEUDA_TOTAL"],
        "MONEDA": "USD",
    }
    return rows, meta
