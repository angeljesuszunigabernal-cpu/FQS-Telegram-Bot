"""Fill the FQS-OPE-F008 weekly report with text only."""
from __future__ import annotations
from collections.abc import Mapping
from pathlib import Path
import pymupdf

WEEKLY_TEMPLATE_PATH = Path(__file__).resolve().parent / "assets" / "FQS-OPE-F008.pdf"
WEEKLY_FIELDS = (
    ("fecha", "Fecha"),
    ("linea", "Línea de Ensamble"),
    ("bahias", "Bahías de reparaciones"),
    ("patios", "Patios y campañas"),
    ("cuarentena", "Área de cuarentena y mesas de rechazo"),
    ("sorteos", "Seguimiento a sorteos"),
    ("comentarios", "Seguimiento / Actividades pendientes / Comentarios"),
)

LABELS = {
    "fecha": "Fecha:",
    "linea": "Línea de Ensamble:",
    "bahias": "Bahías de reparaciones:",
    "patios": "Patios y campañas:",
    "cuarentena": "Área de cuarentena y mesas de rechazo:",
    "sorteos": "Seguimiento a sorteos:",
    "comentarios": "Seguimiento / Actividades pendientes / Comentarios:",
}

def _fit_text(page: pymupdf.Page, rect: pymupdf.Rect, text: str, label: str) -> None:
    for size in (8.5, 8, 7.5, 7, 6.5, 6, 5.5):
        shape = page.new_shape()
        remaining = shape.insert_textbox(rect, text, fontsize=size, fontname="helv", color=(0.05,0.16,0.29), lineheight=1.05)
        if remaining >= 0:
            shape.commit(overlay=True)
            return
    raise ValueError(f"La respuesta de «{label}» es demasiado larga para el espacio disponible.")

def generate_weekly_pdf(answers: Mapping[str, str], template_path: Path = WEEKLY_TEMPLATE_PATH) -> bytes:
    if not template_path.is_file():
        raise FileNotFoundError(f"No se encontró la plantilla: {template_path}")
    doc = pymupdf.open(template_path)
    try:
        if doc.page_count < 1:
            raise ValueError("La plantilla FQS-OPE-F008 no contiene páginas.")
        if doc.page_count > 1:
            doc.select([0])
        page = doc[0]
        anchors = {}
        for key, label in LABELS.items():
            hits = page.search_for(label)
            if not hits:
                raise ValueError(f"No se encontró el campo «{label}» en la plantilla.")
            anchors[key] = hits[-1]

        # Fecha: escribe a la derecha del rótulo, en la misma línea.
        date_anchor = anchors["fecha"]
        _fit_text(page, pymupdf.Rect(date_anchor.x1 + 6, date_anchor.y0 - 1, 260, date_anchor.y1 + 4), answers.get("fecha", "").strip(), "Fecha")

        activity_keys = ["linea", "bahias", "patios", "cuarentena", "sorteos", "comentarios"]
        for i, key in enumerate(activity_keys):
            value = answers.get(key, "").strip()
            if not value:
                raise ValueError(f"Falta la respuesta para: {dict(WEEKLY_FIELDS)[key]}")
            anchor = anchors[key]
            next_top = anchors[activity_keys[i+1]].y0 if i + 1 < len(activity_keys) else page.rect.height - 28
            rect = pymupdf.Rect(132, anchor.y1 + 2, page.rect.width - 38, next_top - 2)
            _fit_text(page, rect, value, dict(WEEKLY_FIELDS)[key])
        if not answers.get("fecha", "").strip():
            raise ValueError("Falta la respuesta para: Fecha")
        return doc.tobytes(garbage=4, deflate=True)
    finally:
        doc.close()
