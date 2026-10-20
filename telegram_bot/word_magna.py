"""Fill only the report date and activity slots in the retained MAGNA Word."""

from __future__ import annotations

import io
import re
from collections.abc import Mapping
from datetime import datetime
from html import unescape
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZipFile

MAGNA_TEMPLATE_PATH = Path(__file__).resolve().parent / "assets" / "MAGNA.docx"
MAGNA_DAYS = (("lunes", "Lunes"), ("martes", "Martes"), ("miercoles", "Miércoles"), ("jueves", "Jueves"), ("viernes", "Viernes"), ("sabado", "Sábado"))
MAGNA_AREAS = (("linea", "Línea de Ensamble"), ("bahias", "Bahías de reparaciones"), ("patios", "Patios y campañas"), ("cuarentena", "Área de cuarentena y mesas de rechazo"), ("sorteos", "Seguimiento a sorteos"))
MAGNA_FIELDS = (("fecha", "Fecha del reporte (DD/MM/AAAA)"),) + tuple((f"{day}_{area}", f"{label} · {area_label}") for day, label in MAGNA_DAYS for area, area_label in MAGNA_AREAS) + (("comentarios", "Seguimiento, actividades pendientes y comentarios"),)


def validate_magna_answer(key: str, text: str) -> str:
    value = text.strip()
    if not value:
        raise ValueError("Escribe una respuesta o toca «No aplica».")
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", value):
        raise ValueError("La respuesta contiene caracteres no válidos. Escríbela de nuevo.")
    if key == "fecha":
        if not re.fullmatch(r"\d{2}/\d{2}/\d{4}", value):
            raise ValueError("Escribe la fecha en formato DD/MM/AAAA, por ejemplo 19/10/2026.")
        try:
            return datetime.strptime(value, "%d/%m/%Y").strftime("%d/%m/%Y")
        except ValueError:
            raise ValueError("La fecha no es válida. Usa DD/MM/AAAA.") from None
    if value.casefold() in ("no aplica", "n/a", "na", "⏭️ no aplica"):
        return "N/A"
    limit = 2000 if key == "comentarios" else 1000
    if len(value) > limit:
        raise ValueError(f"Escribe como máximo {limit} caracteres para este apartado.")
    return value


def magna_filename(date: str) -> str:
    date = validate_magna_answer("fecha", date).replace("/", "-")
    return f"Reporte de trabajo Semanal MAGNA - {date}.docx"


def _text(paragraph: str) -> str:
    return "".join(unescape(text) for text in re.findall(r"<w:t\b[^>]*>(.*?)</w:t>", paragraph, flags=re.DOTALL))


def _append_text(paragraph: str, value: str) -> str:
    properties = re.search(r"<w:rPr\b[^>]*>.*?</w:rPr>", paragraph, flags=re.DOTALL)
    formatting = properties.group(0) if properties else '<w:rPr><w:sz w:val="24"/><w:szCs w:val="24"/></w:rPr>'
    content = "<w:br/>".join(f'<w:t xml:space="preserve">{escape(line)}</w:t>' for line in value.split("\n"))
    return paragraph.replace("</w:p>", f"<w:r>{formatting}{content}</w:r></w:p>")


def _fill_paragraph(table: str, label: str, value: str) -> str:
    matches = 0
    def fill(match: re.Match[str]) -> str:
        nonlocal matches
        paragraph = match.group(0)
        if _text(paragraph).strip() == label:
            matches += 1
            return _append_text(paragraph, " " + value)
        return paragraph
    result = re.sub(r"<w:p\b[^>]*>.*?</w:p>", fill, table, flags=re.DOTALL)
    if matches != 1:
        raise ValueError(f"La plantilla MAGNA no contiene un único apartado {label}.")
    return result


def generate_magna_word(answers: Mapping[str, str], template_path: Path = MAGNA_TEMPLATE_PATH) -> bytes:
    clean = {key: validate_magna_answer(key, answers.get(key, "")) for key, _ in MAGNA_FIELDS}
    with ZipFile(template_path) as source:
        xml = source.read("word/document.xml").decode("utf-8")
        count = 0
        def fill_table(match: re.Match[str]) -> str:
            nonlocal count
            table_index = count
            count += 1
            table = match.group(0)
            if table_index == 0:
                return _fill_paragraph(table, "Fecha:", clean["fecha"])
            if 1 <= table_index <= 5:
                area, _ = MAGNA_AREAS[table_index - 1]
                for day, label in MAGNA_DAYS:
                    table = _fill_paragraph(table, label + ":", clean[f"{day}_{area}"])
                return table
            if table_index == 6:
                inserted = False
                def comments(match: re.Match[str]) -> str:
                    nonlocal inserted
                    paragraph = match.group(0)
                    if not inserted and not _text(paragraph).strip():
                        inserted = True
                        return _append_text(paragraph, clean["comentarios"])
                    return paragraph
                table = re.sub(r"<w:p\b[^>]*>.*?</w:p>", comments, table, flags=re.DOTALL)
                if not inserted:
                    raise ValueError("La plantilla MAGNA no tiene espacio para comentarios.")
                return table
            raise ValueError("La estructura de la plantilla MAGNA cambió.")
        xml = re.sub(r"<w:tbl\b[^>]*>.*?</w:tbl>", fill_table, xml, flags=re.DOTALL)
        if count != 7:
            raise ValueError("La plantilla MAGNA debe contener siete tablas.")
        output = io.BytesIO()
        with ZipFile(output, "w") as target:
            for entry in source.infolist():
                target.writestr(entry, xml.encode("utf-8") if entry.filename == "word/document.xml" else source.read(entry.filename))
    return output.getvalue()
