"""Write FQS-OPE-F001 answers below their prompts on the original page."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import ceil, sqrt
from pathlib import Path
import re

import pymupdf

DEFAULT_TEMPLATE_PATH = Path(__file__).resolve().parent / "assets" / "FQS-OPE-F001.pdf"

PDF_FIELDS: tuple[tuple[str, str], ...] = (
    ("descripcion", "Descripción del problema"),
    ("proyecto", "Proyecto o programa"),
    ("reportado_por", "Reportado por"),
    ("estatus_folio", "Estatus / folio"),
    ("fecha_hora", "Fecha y hora"),
    ("ubicacion", "Ubicación"),
    ("numero_parte", "Número de parte (P/N)"),
    ("serie_rastreabilidad", "Número de serie / rastreabilidad"),
    ("vin", "Número de unidad (VIN)"),
    ("cantidad", "Cantidad de piezas afectadas"),
    ("disposicion", "Disposición del material"),
    ("evidencia", "Evidencia objetiva"),
)


@dataclass(frozen=True)
class _QuestionAnchor:
    top: float
    bottom: float


def _question_anchors(page: pymupdf.Page) -> dict[int, _QuestionAnchor]:
    anchors: dict[int, _QuestionAnchor] = {}
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue

        visible_lines: list[tuple[str, list[dict[str, object]]]] = []
        for line in block.get("lines", []):
            spans = [
                span for span in line.get("spans", []) if str(span.get("text", "")).strip()
            ]
            text = "".join(str(span["text"]) for span in spans).strip()
            if text:
                visible_lines.append((text, spans))
        if not visible_lines:
            continue

        match = re.match(r"^(\d{1,2})\.\s", visible_lines[0][0])
        if not match:
            continue

        number = int(match.group(1))
        if not 1 <= number <= len(PDF_FIELDS):
            continue

        visible_spans = [span for _, spans in visible_lines for span in spans]
        anchors[number] = _QuestionAnchor(
            top=min(float(span["bbox"][1]) for span in visible_spans),  # type: ignore[index]
            bottom=max(float(span["bbox"][3]) for span in visible_spans),  # type: ignore[index]
        )

    missing = set(range(1, len(PDF_FIELDS) + 1)) - anchors.keys()
    if missing:
        numbers = ", ".join(map(str, sorted(missing)))
        raise ValueError(f"No se localizaron las preguntas del formato: {numbers}.")
    return anchors


def _split_long_word(word: str, font: pymupdf.Font, size: float, width: float) -> list[str]:
    pieces: list[str] = []
    current = ""
    for character in word:
        candidate = current + character
        if current and font.text_length(candidate, fontsize=size) > width:
            pieces.append(current)
            current = character
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces


def _wrapped_lines(text: str, font: pymupdf.Font, size: float, width: float) -> str:
    lines: list[str] = []
    for paragraph in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not paragraph:
            lines.append("")
            continue

        current = ""
        for word in paragraph.split():
            pieces = _split_long_word(word, font, size, width)
            candidate = f"{current} {word}".strip()
            if current and font.text_length(candidate, fontsize=size) <= width:
                current = candidate
                continue
            if current:
                lines.append(current)
            lines.extend(pieces[:-1])
            current = pieces[-1]

        if current:
            lines.append(current)
    return "\n".join(lines or [""])


def _write_answer(
    page: pymupdf.Page,
    number: int,
    label: str,
    value: str,
    anchors: Mapping[int, _QuestionAnchor],
) -> None:
    left = 108.0
    right = page.rect.width - 40.0
    top = anchors[number].bottom + 1.2
    bottom = (
        anchors[number + 1].top - 1.0
        if number < len(PDF_FIELDS)
        else page.rect.height - 35.0
    )
    box = pymupdf.Rect(left, top, right, bottom)
    if box.height <= 0:
        raise ValueError(f"No hay espacio debajo de la pregunta «{label}».")

    font = pymupdf.Font("helv")
    for tenths in range(72, 35, -2):
        size = tenths / 10
        wrapped = _wrapped_lines(value, font, size, box.width - 2)
        remaining = page.insert_textbox(
            box,
            wrapped,
            fontname="helv",
            fontsize=size,
            lineheight=1.0,
            color=(0.05, 0.16, 0.29),
            overlay=True,
        )
        if remaining >= 0:
            return

    raise ValueError(
        f"La respuesta de «{label}» es demasiado larga para caber debajo de su pregunta "
        "en la única página del formato."
    )


def _append_evidence_page(
    document: pymupdf.Document,
    evidence_photos: Sequence[Path],
) -> None:
    if not evidence_photos:
        return

    form_page = document[0]
    page_width = form_page.rect.width
    page_height = form_page.rect.height
    margin = 32.0
    image_top = 58.0
    available_width = page_width - margin * 2
    available_height = page_height - image_top - margin
    photo_dimensions: list[tuple[int, int]] = []

    for number, photo_path in enumerate(evidence_photos, start=1):
        if not photo_path.is_file():
            raise FileNotFoundError(f"No se encontró la fotografía de evidencia {number}.")

        try:
            image = pymupdf.Pixmap(str(photo_path))
        except Exception as error:
            raise ValueError(f"No se pudo leer la fotografía de evidencia {number}.") from error

        if image.width <= 0 or image.height <= 0 or image.width * image.height > 50_000_000:
            raise ValueError(
                f"La fotografía de evidencia {number} tiene dimensiones no compatibles."
            )

        photo_dimensions.append((image.width, image.height))

    photo_count = len(evidence_photos)
    if photo_count == 1:
        columns = rows = 1
        cell_height = available_height / 2
    elif photo_count == 2:
        columns, rows = 1, 2
        cell_height = available_height / rows
    else:
        columns = ceil(sqrt(photo_count))
        rows = ceil(photo_count / columns)
        cell_height = available_height / rows

    cell_width = available_width / columns
    page = document.new_page(width=page_width, height=page_height)
    page.insert_text(
        (margin, 36),
        "FQS-OPE-F001 Evidencia fotográfica",
        fontname="hebo",
        fontsize=12,
        color=(0.10, 0.19, 0.29),
    )

    for index, (photo_path, (photo_width, photo_height)) in enumerate(
        zip(evidence_photos, photo_dimensions, strict=True)
    ):
        row, column = divmod(index, columns)
        cell_x = margin + column * cell_width
        cell_y = (
            image_top + (available_height - cell_height) / 2
            if photo_count == 1
            else image_top + row * cell_height
        )
        scale = min(cell_width / photo_width, cell_height / photo_height)
        image_width = photo_width * scale
        image_height = photo_height * scale
        image_rect = pymupdf.Rect(
            cell_x + (cell_width - image_width) / 2,
            cell_y + (cell_height - image_height) / 2,
            cell_x + (cell_width + image_width) / 2,
            cell_y + (cell_height + image_height) / 2,
        )
        page.insert_image(image_rect, filename=str(photo_path), keep_proportion=True)


def generate_completed_pdf(
    answers: Mapping[str, str],
    template_path: Path = DEFAULT_TEMPLATE_PATH,
    evidence_photos: Sequence[Path] = (),
) -> bytes:
    """Fill the original first page and append one page for all evidence photos."""
    if not template_path.is_file():
        raise FileNotFoundError(f"No se encontró la plantilla: {template_path}")

    document = pymupdf.open(template_path)
    try:
        if document.page_count == 0:
            raise ValueError("La plantilla PDF no contiene páginas.")

        if document.page_count > 1:
            document.select([0])

        page = document[0]
        anchors = _question_anchors(page)
        for number, (key, label) in enumerate(PDF_FIELDS, start=1):
            value = answers.get(key, "").strip()
            if not value:
                raise ValueError(f"Falta la respuesta para: {label}")
            _write_answer(page, number, label, value, anchors)

        _append_evidence_page(document, evidence_photos)
        return document.tobytes(garbage=4, deflate=True)
    finally:
        document.close()
