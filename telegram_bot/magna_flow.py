"""Daily MAGNA report capture; automatic Word filename from the report date."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from telegram_bot.word_magna import MAGNA_AREAS, MAGNA_DAYS, MAGNA_FIELDS, generate_magna_word, magna_filename, validate_magna_answer

INPUT_KEYBOARD = {'keyboard': [[{'text': 'No aplica'}],
              [{'text': '⬅️ Anterior'}, {'text': '❌ Cancelar reporte'}],
              [{'text': '✏️ Corregir respuesta'}]],
 'resize_keyboard': True}
DATE_KEYBOARD = {'keyboard': [[{'text': '❌ Cancelar reporte'}], [{'text': '✏️ Corregir respuesta'}]],
 'resize_keyboard': True}
CONFIRM_KEYBOARD = {'keyboard': [[{'text': '✅ Enviar reporte'}],
              [{'text': '⬅️ Anterior'}, {'text': '🔄 Empezar de nuevo'}],
              [{'text': '❌ Cancelar reporte'}],
              [{'text': '✏️ Corregir respuesta'}]],
 'resize_keyboard': True}


@dataclass
class MagnaSession:
    answers: dict[str, str] = field(default_factory=dict)
    next_field: int = 0
    awaiting_confirmation: bool = False

    def cleanup(self) -> None:
        pass


def _prompt(client, chat_id, session):
    if session.awaiting_confirmation:
        client.send_message(chat_id, f"Archivo: {magna_filename(session.answers['fecha'])}\nToca «✅ Enviar reporte» para recibir el Word.", reply_markup=CONFIRM_KEYBOARD)
        return
    key, label = MAGNA_FIELDS[session.next_field]
    if key == "fecha":
        instruction = "Los demás datos generales se conservarán como vienen en el documento."
    else:
        instruction = "Escribe la actividad o toca «No aplica» para colocar N/A."
        if key in session.answers:
            instruction += f"\nRespuesta actual: {session.answers[key]}"
    client.send_message(chat_id, f"{session.next_field + 1}/{len(MAGNA_FIELDS)} · {label}\n{instruction}", reply_markup=DATE_KEYBOARD if key == "fecha" else INPUT_KEYBOARD)


def _summary(session):
    lines = ["Resumen MAGNA", f"Fecha: {session.answers['fecha']}"]
    for day, label in MAGNA_DAYS:
        lines.extend(("", label))
        lines.extend(f"{area_label}: {session.answers[f'{day}_{area}']}" for area, area_label in MAGNA_AREAS)
    lines.extend(("", "Seguimiento, pendientes y comentarios:", session.answers["comentarios"]))
    return "\n".join(lines)


def handle_magna(client, chat_id, message, sessions, main_keyboard) -> bool:
    session = sessions.get(chat_id)
    text = message.get("text", "")
    text = text.strip() if isinstance(text, str) else ""
    command = text.split(maxsplit=1)[0].split("@", 1)[0].lower() if text else ""
    start = command == "/magna" or text == "MAGNA"
    if isinstance(session, MagnaSession) and text == "🔄 Empezar de nuevo":
        start = True
    if start:
        previous = sessions.pop(chat_id, None)
        if previous is not None:
            previous.cleanup()
        session = MagnaSession()
        sessions[chat_id] = session
        client.send_message(chat_id, "Reporte semanal MAGNA en Word. Capturaremos la fecha y las actividades día por día, de lunes a sábado.")
        _prompt(client, chat_id, session)
        return True
    if not isinstance(session, MagnaSession):
        return False
    if command in ("/nuevo", "/semanal", "/inspeccion", "/f005", "/metodo", "/f009", "/start", "/elegir", "/ayuda", "/help", "/ping") or text in ("📄 FQS-OPE-F001", "📅 FQS-OPE-F008", "TK-EPS", "📊 FQS-OPE-F005", "🔎 FQS-OPE-F009", "📝 Nuevo reporte", "↩️ Menú principal", "ℹ️ Ayuda"):
        return False
    if command == "/cancelar" or text == "❌ Cancelar reporte":
        sessions.pop(chat_id, None)
        session.cleanup()
        client.send_message(chat_id, "Reporte MAGNA cancelado.", reply_markup=main_keyboard)
        return True
    if command == "/atras" or text == "⬅️ Anterior":
        session.awaiting_confirmation = False
        session.next_field = max(0, session.next_field - 1)
        _prompt(client, chat_id, session)
        return True
    if command == "/confirmar" or text == "✅ Enviar reporte":
        if not session.awaiting_confirmation:
            client.send_message(chat_id, "Completa primero los apartados del reporte MAGNA.")
            _prompt(client, chat_id, session)
            return True
        try:
            content = generate_magna_word(session.answers)
            client.send_document(chat_id, magna_filename(session.answers["fecha"]), content, f"Reporte de trabajo Semanal MAGNA · {session.answers['fecha']}")
        except Exception:
            logging.exception("No se pudo generar o enviar el Word MAGNA.")
            client.send_message(chat_id, "No pude generar o enviar el Word. Tus respuestas y la fecha siguen guardadas; envía /confirmar para reintentar o /cancelar para salir.", reply_markup=CONFIRM_KEYBOARD)
            return True
        sessions.pop(chat_id, None)
        session.cleanup()
        client.send_message(chat_id, "Reporte MAGNA generado y enviado en Word.", reply_markup=main_keyboard)
        return True
    if not text or message.get("photo") or message.get("document") or command.startswith("/"):
        _prompt(client, chat_id, session)
        return True
    if session.awaiting_confirmation:
        _prompt(client, chat_id, session)
        return True
    key, _ = MAGNA_FIELDS[session.next_field]
    try:
        session.answers[key] = validate_magna_answer(key, text)
    except ValueError as error:
        client.send_message(chat_id, str(error))
        _prompt(client, chat_id, session)
        return True
    session.next_field += 1
    if session.next_field == len(MAGNA_FIELDS):
        session.awaiting_confirmation = True
        client.send_message(chat_id, _summary(session), reply_markup=CONFIRM_KEYBOARD)
    _prompt(client, chat_id, session)
    return True
