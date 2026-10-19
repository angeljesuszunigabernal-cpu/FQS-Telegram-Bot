"""Fill the original F005 Excel template without rebuilding its layout."""

from __future__ import annotations

import io
import math
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZipFile

INSPECTION_TEMPLATE_PATH = Path(__file__).resolve().parent / "assets" / "FQS-OPE-F005.xlsx"
INSPECTION_BASE_NAME = "FQS-OPE-005- Reporte de Inspección"
INSPECTION_FIELDS = (
    ("numero_parte", "Número de parte"),
    ("nombre_parte", "Nombre de la parte"),
    ("defecto", "Descripción del problema (defecto)"),
    ("cliente", "Cliente"),
    ("fecha_inicio", "Fecha de inicio (DD/MM/AAAA)"),
    ("fecha_termino", "Fecha de término (DD/MM/AAAA o No aplica)"),
    ("total", "Total inspeccionado"),
    ("conformes", "Conformes (OK)"),
    ("defectuosas", "Defectuosas (NOK)"),
    ("retrabajadas", "Retrabajadas"),
    ("segregadas", "Segregadas"),
    ("reinspeccionadas", "Reinspección: piezas inspeccionadas (o No aplica)"),
    ("aprobadas", "Reinspección: aprobadas (o No aplica)"),
    ("rechazadas", "Reinspección: rechazadas (o No aplica)"),
    ("observaciones", "Observaciones (o No aplica)"),
)
COUNT_FIELDS = frozenset(("total", "conformes", "defectuosas", "retrabajadas", "segregadas", "reinspeccionadas", "aprobadas", "rechazadas"))
CELL_MAP = {
    "numero_parte": "B6", "nombre_parte": "C6", "defecto": "E6", "cliente": "F6",
    "total": "B9", "conformes": "C9", "defectuosas": "E9", "retrabajadas": "F9",
    "segregadas": "G9", "reinspeccionadas": "B13", "aprobadas": "D13",
    "rechazadas": "F13", "observaciones": "B15",
}


def validate_answer(key: str, text: str, answers: Mapping[str, str]) -> str:
    value = text.strip()
    if not value:
        raise ValueError("La respuesta no puede estar vacía.")
    if value.casefold() in ("no aplica", "n/a", "na"):
        return "No aplica"
    limit = 2000 if key == "observaciones" else 500 if key == "defecto" else 150
    if len(value) > limit:
        raise ValueError(f"Escribe como máximo {limit} caracteres para este apartado.")
    if key in COUNT_FIELDS:
        if not re.fullmatch(r"[0-9]{1,15}", value):
            raise ValueError("Escribe una cantidad entera de cero o más, sin separadores, o No aplica.")
        return str(int(value))
    if key in ("fecha_inicio", "fecha_termino"):
        try:
            date = datetime.strptime(value, "%d/%m/%Y")
        except ValueError:
            raise ValueError("Escribe una fecha válida en formato DD/MM/AAAA o No aplica.") from None
        if key == "fecha_termino" and answers.get("fecha_inicio") not in (None, "No aplica"):
            if date < datetime.strptime(answers["fecha_inicio"], "%d/%m/%Y"):
                raise ValueError("La fecha de término no puede ser anterior al inicio.")
        return date.strftime("%d/%m/%Y")
    return value


def _replace_cell(xml: str, address: str, value: str, numeric: bool = False) -> str:
    pattern = rf'<c\b(?=[^>]*\br="{address}")[^>]*?(?:/>|>.*?</c>)'
    def replacement(match: re.Match[str]) -> str:
        opening = match.group(0).split(">", 1)[0].rstrip("/")
        opening = re.sub(r'\s+t="[^"]*"', "", opening)
        if numeric:
            return f"{opening}><v>{int(value)}</v></c>"
        return f'{opening} t="inlineStr"><is><t xml:space="preserve">{escape(value)}</t></is></c>'
    updated, count = re.subn(pattern, replacement, xml, count=1, flags=re.DOTALL)
    if count != 1:
        raise ValueError(f"La plantilla F005 no contiene la celda esperada {address}.")
    return updated


def _row_height(xml: str, row: int, height: float) -> str:
    def replacement(match: re.Match[str]) -> str:
        opening = re.sub(r'\s+(?:ht|customHeight)="[^"]*"', "", match.group(0)[:-1])
        return f'{opening} ht="{height:g}" customHeight="1">'
    return re.sub(rf'<row\b(?=[^>]*\br="{row}")[^>]*>', replacement, xml, count=1)


def _lines(value: str, width: int) -> int:
    return sum(max(1, math.ceil(len(line) / width)) for line in value.split("\n"))


def generate_inspection_excel(answers: Mapping[str, str], template_path: Path = INSPECTION_TEMPLATE_PATH) -> bytes:
    cleaned = {}
    for key, _ in INSPECTION_FIELDS:
        cleaned[key] = validate_answer(key, answers.get(key, ""), cleaned)
    if not template_path.is_file():
        raise FileNotFoundError("No se encontró la plantilla Excel FQS-OPE-F005.")
    output = io.BytesIO()
    with ZipFile(template_path) as source, ZipFile(output, "w") as target:
        xml = source.read("xl/worksheets/sheet1.xml").decode("utf-8")
        for key, address in CELL_MAP.items():
            value = cleaned[key]
            xml = _replace_cell(xml, address, value, key in COUNT_FIELDS and value != "No aplica")
        # Both dates share a merged cell in the supplied form.
        xml = _replace_cell(xml, "G6", f'{cleaned["fecha_inicio"]}\n\n{cleaned["fecha_termino"]}')
        lines = max(_lines(cleaned[key], width) for key, width in (("numero_parte", 23), ("nombre_parte", 24), ("defecto", 30), ("cliente", 23)))
        xml = _row_height(xml, 6, min(409, max(81.75, lines * 15 + 12)))
        xml = _row_height(xml, 15, min(409, max(87, _lines(cleaned["observaciones"], 140) * 15 + 12)))
        for entry in source.infolist():
            target.writestr(entry, xml.encode("utf-8") if entry.filename == "xl/worksheets/sheet1.xml" else source.read(entry.filename))
    return output.getvalue()
