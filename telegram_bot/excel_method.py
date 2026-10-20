"""Fill F009 and embed step photographs in the original Excel template."""

from __future__ import annotations

import io
import math
import re
import struct
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from zipfile import ZipFile

from telegram_bot.excel_inspection import _replace_cell, _row_height

METHOD_TEMPLATE_PATH = Path(__file__).resolve().parent / "assets" / "FQS-OPE-F009.xlsx"
METHOD_BASE_NAME = "FQS-OPE-009- Método de Inspección"
METHOD_FIELDS = (
    ("numero_parte", "Número de parte"),
    ("nombre_parte", "Nombre de la parte"),
    ("problema", "Descripción del problema"),
    ("tipo", "Tipo de inspección"),
    ("area", "Área de inspección"),
    ("proveedor", "Proveedor"),
    ("guantes", "¿Se requieren guantes anticorte? Responde Sí o No."),
    ("lentes", "¿Se requieren lentes de seguridad? Responde Sí o No."),
    ("chaleco", "¿Se requiere chaleco de seguridad? Responde Sí o No."),
    ("zapatos", "¿Se requieren zapatos de seguridad? Responde Sí o No."),
    ("concern", "Concern (referencia o descripción de la incidencia)"),
    ("fecha", "Fecha de elaboración del método (DD/MM/AAAA)"),
)
EPP_FIELDS = frozenset(("guantes", "lentes", "chaleco", "zapatos"))
CELL_MAP = {"numero_parte": "A5", "nombre_parte": "B5", "problema": "D5", "tipo": "E5", "area": "F5", "proveedor": "I5", "guantes": "A7", "lentes": "B7", "chaleco": "D7", "zapatos": "E7", "concern": "F7", "fecha": "I7"}
SHEET_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def validate_method_answer(key: str, text: str) -> str:
    value = text.strip()
    if not value:
        raise ValueError("Escribe una respuesta para continuar.")
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", value):
        raise ValueError("La respuesta contiene caracteres no válidos. Escríbela de nuevo.")
    if key in EPP_FIELDS:
        if value.casefold() in ("sí", "si", "s", "x", "si requerido"):
            return "Sí"
        if value.casefold() in ("no", "n", "no aplica", "n/a"):
            return "No"
        raise ValueError("Responde Sí o No para marcar el EPP requerido.")
    if value.casefold() in ("no aplica", "n/a"):
        return "No aplica"
    if key == "fecha":
        try:
            date = datetime.strptime(value, "%d/%m/%Y")
        except ValueError:
            raise ValueError("Escribe una fecha válida en formato DD/MM/AAAA.") from None
        return date.strftime("%d/%m/%Y")
    limit = 500 if key in ("problema", "concern", "descripcion") else 150
    if len(value) > limit:
        raise ValueError(f"Escribe como máximo {limit} caracteres.")
    return value


def image_info(data: bytes) -> tuple[int, int, str]:
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        if width and height:
            return width, height, "png"
    if data.startswith(b"\xff\xd8"):
        offset = 2
        while offset + 4 <= len(data):
            if data[offset] != 255:
                raise ValueError("La fotografía JPEG no es válida.")
            while offset < len(data) and data[offset] == 255:
                offset += 1
            if offset >= len(data):
                break
            marker = data[offset]
            offset += 1
            if marker in (0xD9, 0xDA):
                break
            if marker in (0x01, *range(0xD0, 0xD8)):
                continue
            length = int.from_bytes(data[offset:offset + 2], "big")
            if length < 2 or offset + length > len(data):
                break
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF) and length >= 8:
                height, width = struct.unpack(">HH", data[offset + 3:offset + 7])
                if width and height:
                    return width, height, "jpg"
            offset += length
    raise ValueError("Envía la imagen como fotografía por Telegram (JPEG o PNG).")


def _geometry(xml: str) -> tuple[dict[int, int], dict[int, float]]:
    root = ET.fromstring(xml)
    widths = {column: 101 for column in range(9)}
    for column in root.findall(f"{SHEET_NS}cols/{SHEET_NS}col"):
        pixels = round(float(column.get("width", "14.43")) * 7)
        for index in range(int(column.get("min")) - 1, int(column.get("max"))):
            widths[index] = pixels
    heights = {int(row.get("r")) - 1: float(row.get("ht", "15")) * 4 / 3 for row in root.findall(f"{SHEET_NS}sheetData/{SHEET_NS}row")}
    return widths, heights


def _anchor(step: int, width: int, height: int, relation_id: str, picture_id: int, widths: Mapping[int, int], heights: Mapping[int, float]) -> str:
    block, slot = divmod(step, 3)
    first, last = ((0, 2), (3, 4), (5, 8))[slot]
    row = 8 + block * 3
    box_width, box_height = sum(widths[column] for column in range(first, last + 1)), heights[row]
    scale = min((box_width - 12) / width, (box_height - 12) / height)
    drawn_width, drawn_height = width * scale, height * scale
    x, y = (box_width - drawn_width) / 2, (box_height - drawn_height) / 2
    column = first
    while column < last and x >= widths[column]:
        x -= widths[column]
        column += 1
    cx, cy = round(drawn_width * 9525), round(drawn_height * 9525)
    return (
        f'<xdr:oneCellAnchor><xdr:from><xdr:col>{column}</xdr:col><xdr:colOff>{round(x * 9525)}</xdr:colOff>'
        f'<xdr:row>{row}</xdr:row><xdr:rowOff>{round(y * 9525)}</xdr:rowOff></xdr:from><xdr:ext cx="{cx}" cy="{cy}"/>'
        f'<xdr:pic><xdr:nvPicPr><xdr:cNvPr id="{picture_id}" name="Paso {step + 1}"/>'
        '<xdr:cNvPicPr><a:picLocks noChangeAspect="1"/></xdr:cNvPicPr></xdr:nvPicPr>'
        f'<xdr:blipFill><a:blip r:embed="{relation_id}"/><a:stretch><a:fillRect/></a:stretch></xdr:blipFill>'
        f'<xdr:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></xdr:spPr></xdr:pic><xdr:clientData/></xdr:oneCellAnchor>'
    )


def generate_method_excel(answers: Mapping[str, str], steps: Sequence[tuple[str, Path]], template_path: Path = METHOD_TEMPLATE_PATH) -> bytes:
    if not 1 <= len(steps) <= 12:
        raise ValueError("El método debe tener entre 1 y 12 pasos con fotografía y descripción.")
    clean = {key: validate_method_answer(key, answers.get(key, "")) for key, _ in METHOD_FIELDS}
    descriptions = [validate_method_answer("descripcion", description) for description, _ in steps]
    with ZipFile(template_path) as source:
        sheet = source.read("xl/worksheets/sheet1.xml").decode("utf-8")
        widths, heights = _geometry(sheet)
        drawing = source.read("xl/drawings/drawing1.xml").decode("utf-8")
        relations = source.read("xl/drawings/_rels/drawing1.xml.rels").decode("utf-8")
        types = source.read("[Content_Types].xml").decode("utf-8")
        for key, address in CELL_MAP.items():
            value = ("X" if clean[key] == "Sí" else "") if key in EPP_FIELDS else clean[key]
            sheet = _replace_cell(sheet, address, value)
        # Grow only rows containing responses when their text needs extra room.
        data_lines = max(math.ceil(len(clean[key]) / 22) for key in ("numero_parte", "nombre_parte", "problema", "tipo", "area", "proveedor"))
        sheet = _row_height(sheet, 5, max(30.75, data_lines * 12 + 8))
        if len(clean["concern"]) > 70:
            sheet = _row_height(sheet, 7, max(30.75, math.ceil(len(clean["concern"]) / 28) * 12 + 8 - 16.5))
        next_relation = max((int(number) for number in re.findall(r'Id="rId(\d+)"', relations)), default=0) + 1
        next_picture = max((int(number) for number in re.findall(r'<xdr:cNvPr\b[^>]*\bid="(\d+)"', drawing)), default=0) + 1
        media = {}
        for index in range(12):
            block, slot = divmod(index, 3)
            column = ("A", "D", "F")[slot]
            image_row = 9 + block * 3
            sheet = _replace_cell(sheet, f"{column}{image_row}", "")
            sheet = _replace_cell(sheet, f"{column}{image_row + 1}", f"Paso {index + 1}:" if index < len(steps) else "")
            sheet = _replace_cell(sheet, f"{column}{image_row + 2}", "Descripción:\n" + descriptions[index] if index < len(steps) else "")
            if index >= len(steps):
                continue
            data = Path(steps[index][1]).read_bytes()
            width, height, extension = image_info(data)
            name = f"f009-step-{index + 1:02d}.{extension}"
            media["xl/media/" + name] = data
            relation = f"rId{next_relation + index}"
            drawing = drawing.replace("</xdr:wsDr>", _anchor(index, width, height, relation, next_picture + index, widths, heights) + "</xdr:wsDr>")
            relations = relations.replace("</Relationships>", f'<Relationship Id="{relation}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="../media/{name}"/></Relationships>')
            if f'Extension="{extension}"' not in types:
                mime = "image/png" if extension == "png" else "image/jpeg"
                types = types.replace("</Types>", f'<Default Extension="{extension}" ContentType="{mime}"/></Types>')
        for block in range(4):
            values = descriptions[block * 3:block * 3 + 3]
            if values:
                lines = max(sum(max(1, math.ceil(len(line) / 36)) for line in value.split("\n")) + 1 for value in values)
                row = 11 + block * 3
                sheet = _row_height(sheet, row, max(heights[row - 1] * 3 / 4, lines * 12 + 8))
        replacements = {"xl/worksheets/sheet1.xml": sheet, "xl/drawings/drawing1.xml": drawing, "xl/drawings/_rels/drawing1.xml.rels": relations, "[Content_Types].xml": types}
        output = io.BytesIO()
        with ZipFile(output, "w") as target:
            for entry in source.infolist():
                target.writestr(entry, replacements[entry.filename].encode("utf-8") if entry.filename in replacements else source.read(entry.filename))
            for name, data in media.items():
                target.writestr(name, data)
    return output.getvalue()
