"""Spanish conversation flow for completing FQS-OPE-F001."""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from telegram_bot.pdf_form import DEFAULT_TEMPLATE_PATH, PDF_FIELDS, generate_completed_pdf
from telegram_bot.pdf_weekly import WEEKLY_FIELDS, WEEKLY_TEMPLATE_PATH, generate_weekly_pdf

FIELD_QUESTIONS = {
    "descripcion": "Describe la no conformidad o desviación detectada.",
    "proyecto": "¿En qué proyecto o programa se identificó?",
    "reportado_por": "¿Quién lo reporta? Indica nombre, puesto o área.",
    "estatus_folio": "¿Cuál es el estatus del reporte y/o su folio de referencia?",
    "fecha_hora": "¿Cuál fue la fecha y hora de detección o reporte?",
    "ubicacion": "¿Dónde se detectó? Indica planta, instalación, línea o área.",
    "numero_parte": "¿Cuál es el número de parte afectada (P/N)?",
    "serie_rastreabilidad": (
        "¿Cuál es el número de serie o la información de rastreabilidad "
        "(lote, batch, fecha de producción, turno, etc.)?"
    ),
    "vin": "¿Cuál es el número de unidad (VIN o identificación), si aplica?",
    "cantidad": "¿Cuántas piezas están afectadas (sospechosas, contenidas o confirmadas)?",
    "disposicion": (
        "¿Qué disposición se dio al material? Indica la acción de contención, "
        "por ejemplo: cuarentena, retrabajo, scrap, devolución o selección."
    ),
    "evidencia": "Envía una o varias fotografías. Cuando termines, escribe /listo.",
}

HELP_TEXT = (
    "Comandos disponibles:\n"
    "/nuevo — iniciar directamente FQS-OPE-F001\n"
    "/semanal — iniciar FQS-OPE-F008 (solo texto)\n"
    "/cancelar — cancelar la captura actual\n"
    "/listo — terminar de enviar las fotografías del punto 12\n"
    "/confirmar — generar el PDF después de revisar el resumen\n"
    "/ayuda — mostrar esta ayuda\n"
    "/ping — comprobar que el bot está activo"
)


class BotClient(Protocol):
    def send_message(
        self, chat_id: int | str, text: str, reply_markup: dict[str, Any] | None = None
    ) -> None: ...

    def download_file(self, file_id: str, destination: Path) -> None: ...

    def send_document(
        self, chat_id: int | str, filename: str, content: bytes, caption: str
    ) -> None: ...


# Botones visibles en Telegram. Se usa ReplyKeyboardMarkup para que funcionen
# también en Android/iPhone sin depender de comandos escritos manualmente.
MAIN_KEYBOARD = {
    "keyboard": [[{"text": "📝 Nuevo reporte"}, {"text": "ℹ️ Ayuda"}]],
    "resize_keyboard": True,
}
REPORT_KEYBOARD = {
    "keyboard": [
        [{"text": "📄 FQS-OPE-F001"}],
        [{"text": "📅 FQS-OPE-F008"}],
        [{"text": "↩️ Menú principal"}],
    ],
    "resize_keyboard": True,
}
WEEKLY_KEYBOARD = {
    "keyboard": [[{"text": "⏭️ No aplica"}], [{"text": "❌ Cancelar reporte"}]],
    "resize_keyboard": True,
}

FORM_KEYBOARD = {
    "keyboard": [[{"text": "⏭️ No aplica"}], [{"text": "❌ Cancelar reporte"}]],
    "resize_keyboard": True,
}
EVIDENCE_KEYBOARD = {
    "keyboard": [
        [{"text": "📷 Agregar otra foto"}, {"text": "✅ Terminar fotos"}],
        [{"text": "❌ Cancelar reporte"}],
    ],
    "resize_keyboard": True,
}
SUMMARY_KEYBOARD = {
    "keyboard": [
        [{"text": "✅ Generar PDF"}],
        [{"text": "🔄 Empezar de nuevo"}, {"text": "❌ Cancelar reporte"}],
    ],
    "resize_keyboard": True,
}


def _send(client: BotClient, chat_id: int | str, text: str, keyboard: dict[str, Any] | None = None) -> None:
    client.send_message(chat_id, text, reply_markup=keyboard)


PdfGenerator = Callable[[Mapping[str, str], Path, Sequence[Path]], bytes]


@dataclass
class FormSession:
    answers: dict[str, str] = field(default_factory=dict)
    next_field: int = 0
    awaiting_confirmation: bool = False
    evidence_photos: list[Path] = field(default_factory=list)
    _photo_temp_dir: tempfile.TemporaryDirectory | None = field(default=None, repr=False)

    def next_photo_path(self) -> Path:
        if self._photo_temp_dir is None:
            self._photo_temp_dir = tempfile.TemporaryDirectory(prefix="fqs-ope-f001-")
        return Path(self._photo_temp_dir.name) / f"evidencia-{len(self.evidence_photos) + 1:03d}.jpg"

    def cleanup(self) -> None:
        self.evidence_photos.clear()
        if self._photo_temp_dir is not None:
            self._photo_temp_dir.cleanup()
            self._photo_temp_dir = None


@dataclass
class WeeklySession:
    answers: dict[str, str] = field(default_factory=dict)
    next_field: int = 0
    awaiting_confirmation: bool = False

    def cleanup(self) -> None:
        pass


SESSIONS: dict[int | str, FormSession | WeeklySession] = {}


def _command(text: str) -> tuple[str, str]:
    parts = text.strip().split(maxsplit=1)
    if not parts:
        return "", ""
    name = parts[0].split("@", maxsplit=1)[0].lower()
    argument = parts[1] if len(parts) > 1 else ""
    return name, argument


def _question(session: FormSession) -> str:
    if session.next_field == len(PDF_FIELDS) - 1:
        return (
            f"12/12 · {PDF_FIELDS[-1][1]}\n"
            "Envía una o varias fotografías por Telegram. Cuando termines, escribe /listo."
        )
    key, label = PDF_FIELDS[session.next_field]
    number = session.next_field + 1
    return f"{number}/12 · {label}\n{FIELD_QUESTIONS[key]}"


def _summary(session: FormSession) -> str:
    lines = ["Resumen del FQS-OPE-F001:"]
    for number, (key, label) in enumerate(PDF_FIELDS[:-1], start=1):
        lines.extend((f"{number}. {label}", session.answers[key]))
    lines.extend(
        (
            "12. Evidencia objetiva",
            f"Fotografías recibidas: {len(session.evidence_photos)}",
        )
    )
    lines.extend(("", "¿Confirmas que genere el PDF? Responde /confirmar o /cancelar."))
    return "\n".join(lines)



def _weekly_question(session: WeeklySession) -> str:
    key, label = WEEKLY_FIELDS[session.next_field]
    return f"{session.next_field + 1}/7 · {label}\nEscribe el texto que deseas colocar en este apartado."


def _weekly_summary(session: WeeklySession) -> str:
    lines = ["Resumen del FQS-OPE-F008:"]
    for number, (key, label) in enumerate(WEEKLY_FIELDS, start=1):
        lines.extend((f"{number}. {label}", session.answers.get(key, "")))
    lines.extend(("", "¿Confirmas que genere el PDF?"))
    return "\n".join(lines)


def _confirm_weekly(client: BotClient, chat_id: int | str, session: WeeklySession, sessions: MutableMapping[int | str, FormSession | WeeklySession]) -> None:
    if not session.awaiting_confirmation:
        _send(client, chat_id, "Completa primero todos los apartados del reporte semanal.", WEEKLY_KEYBOARD)
        return
    try:
        content = generate_weekly_pdf(session.answers)
        client.send_document(chat_id, "FQS-OPE-F008-completado.pdf", content, "FQS-OPE-F008 Reporte de trabajo semanal completado.")
    except Exception as error:
        logging.exception("No se pudo generar el FQS-OPE-F008.")
        _send(client, chat_id, f"No pude generar el PDF: {error}. Tus respuestas siguen guardadas.", SUMMARY_KEYBOARD)
        return
    sessions.pop(chat_id, None)
    _send(client, chat_id, "PDF semanal generado y enviado.", MAIN_KEYBOARD)


def _receive_photo(
    client: BotClient,
    chat_id: int | str,
    session: FormSession | WeeklySession | None,
    photo_sizes: list[Any],
) -> None:
    if session is None:
        client.send_message(chat_id, "Envía /nuevo para iniciar el FQS-OPE-F001.")
        return
    if session.awaiting_confirmation:
        client.send_message(
            chat_id,
            "El resumen ya está listo. Responde /confirmar o /cancelar.",
        )
        return
    if session.next_field != len(PDF_FIELDS) - 1:
        client.send_message(
            chat_id,
            "Las fotografías se reciben en el punto 12, después de responder los puntos 1 al 11.",
        )
        return

    candidates = [
        photo
        for photo in photo_sizes
        if isinstance(photo, dict) and isinstance(photo.get("file_id"), str)
    ]
    if not candidates:
        client.send_message(chat_id, "No pude identificar la fotografía. Inténtalo de nuevo.")
        return

    photo = max(
        candidates,
        key=lambda item: (
            int(item.get("width") or 0) * int(item.get("height") or 0),
            int(item.get("file_size") or 0),
        ),
    )
    destination = session.next_photo_path()
    try:
        client.download_file(photo["file_id"], destination)
    except Exception:
        destination.unlink(missing_ok=True)
        logging.exception("No se pudo recibir una fotografía de evidencia.")
        client.send_message(
            chat_id,
            "No pude recibir esa fotografía. Inténtalo de nuevo o usa /cancelar.",
        )
        return

    session.evidence_photos.append(destination)
    _send(
        client,
        chat_id,
        f"Fotografía {len(session.evidence_photos)} recibida. "
        "Puedes agregar otra o tocar «✅ Terminar fotos».",
        EVIDENCE_KEYBOARD,
    )


def _confirm_pdf(
    client: BotClient,
    chat_id: int | str,
    session: FormSession,
    sessions: MutableMapping[int | str, FormSession],
    template_path: Path,
    pdf_generator: PdfGenerator,
) -> None:
    if not session.awaiting_confirmation:
        if session.next_field == len(PDF_FIELDS) - 1:
            response = (
                "Primero envía una o varias fotografías y escribe /listo. "
                "Puedes cancelar en cualquier momento con /cancelar."
            )
        else:
            response = (
                "Primero responde la pregunta actual. "
                "Puedes cancelar en cualquier momento con /cancelar."
            )
        client.send_message(chat_id, response)
        return

    try:
        pdf_answers = dict(session.answers)
        pdf_answers["evidencia"] = (
            f"Fotografías adjuntas: {len(session.evidence_photos)}. "
            "Consultar las páginas adicionales de evidencia."
        )
        pdf_content = pdf_generator(
            pdf_answers,
            template_path,
            tuple(session.evidence_photos),
        )
        client.send_document(
            chat_id,
            "FQS-OPE-F001-completado.pdf",
            pdf_content,
            (
                f"FQS-OPE-F001 completado con {len(session.evidence_photos)} fotografías "
                "de evidencia."
            ),
        )
    except ValueError as error:
        logging.warning("No se pudo preparar el PDF FQS-OPE-F001: %s", error)
        client.send_message(
            chat_id,
            f"No pude preparar el PDF: {error} "
            "Tus respuestas y fotografías siguen guardadas. "
            "Envía /confirmar para reintentar, /nuevo para iniciar otra captura "
            "o /cancelar para salir.",
        )
        return
    except Exception:
        logging.exception("No se pudo generar o enviar el PDF FQS-OPE-F001.")
        client.send_message(
            chat_id,
            "No pude generar o enviar el PDF. Tus respuestas y fotografías siguen guardadas; "
            "envía /confirmar para reintentar o /cancelar para salir.",
        )
        return

    sessions.pop(chat_id, None)
    session.cleanup()
    _send(
        client,
        chat_id,
        "PDF generado y enviado. Las fotografías están en páginas adicionales de evidencia.",
        MAIN_KEYBOARD,
    )


def handle_update(
    client: BotClient,
    update: dict[str, Any],
    *,
    sessions: MutableMapping[int | str, FormSession] | None = None,
    template_path: Path = DEFAULT_TEMPLATE_PATH,
    pdf_generator: PdfGenerator = generate_completed_pdf,
) -> None:
    message = update.get("message")
    if not isinstance(message, dict):
        return

    chat = message.get("chat")
    if not isinstance(chat, dict) or "id" not in chat:
        return
    chat_id = chat["id"]
    active_sessions = SESSIONS if sessions is None else sessions
    session = active_sessions.get(chat_id)
    text = message.get("text")

    photo_sizes = message.get("photo")
    if isinstance(photo_sizes, list) and photo_sizes:
        if isinstance(session, WeeklySession):
            _send(client, chat_id, "Este formato semanal solo necesita texto. Responde la pregunta actual.", WEEKLY_KEYBOARD)
        else:
            _receive_photo(client, chat_id, session, photo_sizes)
        return

    if not isinstance(text, str):
        if (
            session
            and not session.awaiting_confirmation
            and session.next_field == len(PDF_FIELDS) - 1
        ):
            response = (
                "Envía una o varias fotografías por Telegram. "
                "Cuando termines, escribe /listo."
            )
        else:
            response = "Responde con texto o envía /ayuda para ver los comandos disponibles."
        client.send_message(chat_id, response)
        return

    # Los botones reutilizan exactamente el mismo flujo que los comandos existentes.
    button_text = text.strip()
    button_commands = {
        "📝 Nuevo reporte": "/elegir",
        "📄 FQS-OPE-F001": "/nuevo",
        "📅 FQS-OPE-F008": "/semanal",
        "↩️ Menú principal": "/start",
        "ℹ️ Ayuda": "/ayuda",
        "❌ Cancelar reporte": "/cancelar",
        "✅ Terminar fotos": "/listo",
        "✅ Generar PDF": "/confirmar",
        "🔄 Empezar de nuevo": "/nuevo",
    }
    if button_text in button_commands:
        text = button_commands[button_text]
    elif button_text == "⏭️ No aplica":
        text = "No aplica"
    elif button_text == "📷 Agregar otra foto":
        if session and not session.awaiting_confirmation and session.next_field == len(PDF_FIELDS) - 1:
            _send(client, chat_id, "Envía la siguiente fotografía por Telegram.", EVIDENCE_KEYBOARD)
        else:
            _send(client, chat_id, "La opción de agregar fotos está disponible en el punto 12.", FORM_KEYBOARD if session else MAIN_KEYBOARD)
        return

    command, _ = _command(text)
    if command == "/cancelar":
        cancelled_session = active_sessions.pop(chat_id, None)
        if cancelled_session is None:
            client.send_message(chat_id, "No hay un formulario activo para cancelar.")
        else:
            cancelled_session.cleanup()
            _send(client, chat_id, "Formulario cancelado. No se generó ningún PDF.", MAIN_KEYBOARD)
        return

    if command == "/listo":
        if session is None:
            client.send_message(chat_id, "No hay un formulario activo. Envía /nuevo para comenzar.")
        elif session.awaiting_confirmation:
            client.send_message(
                chat_id,
                "El resumen ya está listo. Responde /confirmar o /cancelar.",
            )
        elif session.next_field != len(PDF_FIELDS) - 1:
            client.send_message(
                chat_id,
                "Completa primero los puntos 1 al 11; después podrás enviar las fotografías.",
            )
        elif not session.evidence_photos:
            client.send_message(
                chat_id,
                "Aún no recibí fotografías. Envía al menos una antes de escribir /listo.",
            )
        else:
            session.awaiting_confirmation = True
            _send(client, chat_id, _summary(session), SUMMARY_KEYBOARD)
        return

    if command == "/elegir":
        _send(client, chat_id, "Selecciona el formato que deseas completar:", REPORT_KEYBOARD)
        return

    if command == "/semanal":
        previous_session = active_sessions.pop(chat_id, None)
        if previous_session is not None:
            previous_session.cleanup()
        weekly = WeeklySession()
        active_sessions[chat_id] = weekly
        _send(client, chat_id, "Vamos a completar el FQS-OPE-F008 - Reporte de trabajo semanal. Solo te pediré texto.\n\n" + _weekly_question(weekly), WEEKLY_KEYBOARD)
        return

    if command == "/nuevo":
        previous_session = active_sessions.pop(chat_id, None)
        restarted = previous_session is not None
        if previous_session is not None:
            previous_session.cleanup()
        session = FormSession()
        active_sessions[chat_id] = session
        introduction = (
            "Se descartó la captura anterior. Empezaremos un formulario nuevo."
            if restarted
            else "Vamos a completar el FQS-OPE-F001, una pregunta a la vez."
        )
        _send(
            client,
            chat_id,
            f"{introduction}\n"
            "Si un campo no aplica, toca «⏭️ No aplica». "
            "Escribiré cada respuesta debajo de su pregunta en la misma página.\n\n"
            f"{_question(session)}",
            FORM_KEYBOARD,
        )
        return

    if command in ("/ayuda", "/help"):
        _send(client, chat_id, HELP_TEXT, MAIN_KEYBOARD)
        return

    if command == "/start":
        _send(
            client,
            chat_id,
            "Hola. Puedo ayudarte a completar los formatos FQS-OPE-F001 y FQS-OPE-F008. "
            "Toca «📝 Nuevo reporte» para elegir uno.",
            MAIN_KEYBOARD,
        )
        return

    if command == "/ping":
        client.send_message(chat_id, "El bot está activo.")
        return

    if command == "/confirmar":
        if isinstance(session, WeeklySession):
            _confirm_weekly(client, chat_id, session, active_sessions)
            return
        if session is None:
            client.send_message(
                chat_id,
                "No hay un formulario pendiente. Envía /nuevo para comenzar.",
            )
            return
        _confirm_pdf(
            client,
            chat_id,
            session,
            active_sessions,
            template_path,
            pdf_generator,
        )
        return

    if text.strip().startswith("/"):
        client.send_message(
            chat_id,
            "Ese comando no está disponible. Envía /ayuda para ver las opciones.",
        )
        return

    if session is None:
        _send(client, chat_id, "Toca «📝 Nuevo reporte» para elegir el formato que deseas completar (o usa /nuevo para FQS-OPE-F001).", MAIN_KEYBOARD)
        return

    if isinstance(session, WeeklySession):
        if session.awaiting_confirmation:
            _send(client, chat_id, "Revisa el resumen y toca «✅ Generar PDF» o «❌ Cancelar reporte».", SUMMARY_KEYBOARD)
            return
        answer = text.strip()
        if not answer:
            _send(client, chat_id, "La respuesta no puede estar vacía. Usa «⏭️ No aplica» si corresponde.", WEEKLY_KEYBOARD)
            return
        key, _ = WEEKLY_FIELDS[session.next_field]
        session.answers[key] = answer
        session.next_field += 1
        if session.next_field < len(WEEKLY_FIELDS):
            _send(client, chat_id, _weekly_question(session), WEEKLY_KEYBOARD)
        else:
            session.awaiting_confirmation = True
            _send(client, chat_id, _weekly_summary(session), SUMMARY_KEYBOARD)
        return

    if session.awaiting_confirmation:
        client.send_message(
            chat_id,
            "Revisa el resumen y responde /confirmar para generar el PDF o /cancelar para salir.",
        )
        return

    if session.next_field == len(PDF_FIELDS) - 1:
        client.send_message(
            chat_id,
            "El punto 12 se completa con fotografías, no con texto. "
            "Envía una o varias fotos y luego escribe /listo.",
        )
        return

    answer = text.strip()
    if not answer:
        client.send_message(
            chat_id,
            "La respuesta no puede estar vacía. Escribe «No aplica» si el campo no corresponde.",
        )
        return

    key, _ = PDF_FIELDS[session.next_field]
    session.answers[key] = answer
    session.next_field += 1

    if session.next_field < len(PDF_FIELDS):
        keyboard = EVIDENCE_KEYBOARD if session.next_field == len(PDF_FIELDS) - 1 else FORM_KEYBOARD
        _send(client, chat_id, _question(session), keyboard)
        return

    session.awaiting_confirmation = True
    _send(client, chat_id, _summary(session), SUMMARY_KEYBOARD)
