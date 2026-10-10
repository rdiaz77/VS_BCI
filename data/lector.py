"""Read every statement contained in a PDF.

Scanned archives merge dozens of statements into one file, and national and
international statements for the same month sit side by side, so a PDF is a
list of statements rather than one.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .common import dividir_en_cartolas
from .extractor_internacional import parse_internacional
from .extractor_nacional import parse_nacional


def leer_todas_las_cartolas(
    pdf_bytes: bytes, filename: str = "archivo.pdf"
) -> List[Tuple[List[Dict[str, Any]], Dict[str, Any]]]:
    """Return [(rows, meta), ...] — one entry per statement, in document order.

    Statements whose header did not parse into a card and date are skipped:
    without those, `build_archivo_origen` falls back to the filename and every
    statement in a merged file collides under one key.
    """
    out = []
    for tipo, page_texts in dividir_en_cartolas(pdf_bytes):
        parse = parse_internacional if tipo == "INTERNACIONAL" else parse_nacional
        rows, meta = parse(page_texts, filename)
        if not rows:
            continue
        if not (meta.get("TARJETA_ULT4") and meta.get("FECHA_ESTADO")):
            continue
        out.append((rows, meta))
    return out
