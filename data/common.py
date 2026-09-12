"""Header parsing shared by the national and international BCI parsers.

The statement header is identical in both documents:

    ESTADO DE CUENTA {NACIONAL|INTERNACIONAL} DE TARJETA DE CREDITO
    NOMBRE DEL TITULAR          RAFAEL P. DIAZ
    N° DE TARJETA DE CRÉDITO    XXXXXXXXXXXX4364
    FECHA ESTADO DE CUENTA      23/01/2026

The card's last four digits are the only stable identity: the same card is
printed as "RAFAEL DIAZ" on one statement and "RAFAEL P. DIAZ" on the next,
so the name cannot be used for deduplication.
"""
from __future__ import annotations

import io
import re
from typing import Any, Dict, Optional

import pdfplumber

TITULAR_RE = re.compile(
    r"NOMBRE DEL TITULAR\s+(?P<nombre>.+?)\s+N[°º]\s*DE\s*TARJETA",
    re.IGNORECASE | re.DOTALL,
)
TARJETA_RE = re.compile(
    r"N[°º]\s*DE\s*TARJETA\s*DE\s*CR[EÉ]DITO\s+[X\*\s\-]*(?P<ult4>\d{4})\b",
    re.IGNORECASE,
)
FECHA_ESTADO_RE = re.compile(
    r"FECHA ESTADO DE CUENTA\s+(?P<fecha>\d{2}[-/]\d{2}[-/]\d{4})",
    re.IGNORECASE,
)


def flatten(text: str) -> str:
    """Collapse all whitespace so header fields split across lines still match."""
    return " ".join(text.split())


def extraer_identidad(full_text: str) -> Dict[str, Optional[str]]:
    """Pull cardholder, card last-4 and statement date out of the header."""
    flat = flatten(full_text)

    titular_completo = None
    titular_first = None
    m = TITULAR_RE.search(flat)
    if m:
        nombre = " ".join(m.group("nombre").split()).strip()
        if nombre:
            titular_completo = nombre
            titular_first = nombre.split()[0].title()

    ult4 = None
    m = TARJETA_RE.search(flat)
    if m:
        ult4 = m.group("ult4")

    fecha_estado = None
    m = FECHA_ESTADO_RE.search(flat)
    if m:
        fecha_estado = m.group("fecha").replace("/", "-")

    return {
        "TITULAR_NOMBRE": titular_first,
        "TITULAR_COMPLETO": titular_completo,
        "TARJETA_ULT4": ult4,
        "FECHA_ESTADO": fecha_estado,
    }


def build_archivo_origen(
    prefix: str,
    ult4: Optional[str],
    titular: Optional[str],
    fecha_estado: Optional[str],
    filename: str,
) -> str:
    """Stable per-statement key: BCI_NAC_4364_23-01-2026.

    Falls back to the cardholder's name, then the filename, so a statement
    whose header failed to parse still gets a unique-ish key instead of
    silently colliding with another one.
    """
    if ult4 and fecha_estado:
        return f"{prefix}_{ult4}_{fecha_estado}"
    if titular and fecha_estado:
        return f"{prefix}_{titular.replace(' ', '_')}_{fecha_estado}"
    return filename


def detectar_tipo_cartola(pdf_bytes: bytes) -> Optional[str]:
    """Return 'NACIONAL' | 'INTERNACIONAL' by reading the title on page 1.

    Lets a mixed batch of uploads be routed to the right parser instead of
    relying on which tab the user happened to be on.
    """
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            if not pdf.pages:
                return None
            head = flatten(pdf.pages[0].extract_text() or "").upper()
    except Exception:
        return None

    if "INTERNACIONAL DE TARJETA" in head:
        return "INTERNACIONAL"
    if "NACIONAL DE TARJETA" in head:
        return "NACIONAL"
    return None


def resumen_meta(meta: Dict[str, Any]) -> str:
    """Human label for a statement, e.g. 'Rafael ••4364 · 23-01-2026'."""
    card = f"••{meta['TARJETA_ULT4']}" if meta.get("TARJETA_ULT4") else "tarjeta ?"
    who = meta.get("TITULAR_NOMBRE") or "?"
    return f"{who} {card} · {meta.get('FECHA_ESTADO') or '?'}"
