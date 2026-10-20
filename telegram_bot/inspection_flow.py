"""Telegram conversation for the data-only F005 Excel report."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from telegram_bot.excel_inspection import (
    INSPECTION_BASE_NAME, INSPECTION_FIELDS, generate_inspection_excel, validate_answer,
)

INPUT_KEYBOARD = {'keyboard': [[{'text': '⏭️ No aplica'}],
              [{'text': '❌ Cancelar reporte'}],
              [{'text': '✏️ Corregir respuesta'}]],
 'resize_keyboard': True}
NAME_KEYBOARD = {'keyboard': [[{'text': '❌ Cancelar reporte'}], [{'text': '✏️ Corregir respuesta'}]],
 'resize_keyboard': True}
CONFIRM_KEYBOARD = {'keyboard': [[{'text': '✅ Enviar reporte'}],
              [{'text': '🔄 Empezar de nuevo'}, {'text': '❌ Cancelar reporte'}],
              [{'text': '✏️ Corregir respuesta'}]],
 'resize_keyboard': True}


@dataclass
class InspectionSession:
    answers: dict[str, str] = field(default_factory=dict)
    next_field: int = 0
    awaiting_confirmation: bool = False
    report_name: str = ""

    def cleanup(self) -> None:
        pass


def _question(session: InspectionSession) -> str:
    _, label = INSPECTION_FIELDS[session.next_field]
    return f"{session.next_field + 1}/{len(INSPECTION_FIELDS)} · {label}\nEscribe el dato de este apartado."


def _prompt(client, chat_id, session):
    if not session.report_name:
        client.send_message(chat_id, "¿Qué nombre quieres agregar al reporte de inspección?", reply_markup=NAME_KEYBOARD)
    else:
        client.send_message(chat_id, f"Nombre del archivo: {INSPECTION_BASE_NAME} - {session.report_name}.xlsx\nToca «✅ Enviar reporte» para recibir el Excel.", reply_markup=CONFIRM_KEYBOARD)


def handle_inspection(client, chat_id, message, sessions, main_keyboard) -> bool:
    session = sessions.get(chat_id)
    text = message.get("text", "")
    text = text.strip() if isinstance(text, str) else ""
    command = text.split(maxsplit=1)[0].split("@", 1)[0].lower() if text else ""
    start = command in ("/inspeccion", "/f005") or text == "📊 FQS-OPE-F005"
    if isinstance(session, InspectionSession) and text == "🔄 Empezar de nuevo":
        start = True
    if start:
        previous = sessions.pop(chat_id, None)
        if previous is not None:
            previous.cleanup()
        session = InspectionSession()
        sessions[chat_id] = session
        client.send_message(chat_id, "Vamos a completar el FQS-OPE-F005 · Reporte de Inspección. Solo datos, en Excel.\n\n" + _question(session), reply_markup=INPUT_KEYBOARD)
        return True
    if not isinstance(session, InspectionSession):
        return False
    if command in ("/nuevo", "/semanal", "/start", "/elegir", "/ayuda", "/help", "/ping") or text in ("📄 FQS-OPE-F001", "📅 FQS-OPE-F008", "TK-EPS", "📝 Nuevo reporte", "↩️ Menú principal", "ℹ️ Ayuda"):
        return False
    if command == "/cancelar" or text == "❌ Cancelar reporte":
        sessions.pop(chat_id, None)
        client.send_message(chat_id, "Reporte de inspección cancelado.", reply_markup=main_keyboard)
        return True
    if command == "/confirmar" or text in ("✅ Enviar reporte", "✅ Generar PDF"):
        if not session.awaiting_confirmation:
            client.send_message(chat_id, "Completa primero las preguntas del reporte.\n" + _question(session), reply_markup=INPUT_KEYBOARD)
        elif not session.report_name:
            _prompt(client, chat_id, session)
        else:
            try:
                content = generate_inspection_excel(session.answers)
                client.send_document(chat_id, f"{INSPECTION_BASE_NAME} - {session.report_name}.xlsx", content, f"FQS-OPE-F005 · {session.report_name}")
            except Exception:
                logging.exception("No se pudo generar o enviar el Excel F005.")
                client.send_message(chat_id, "No pude generar o enviar el Excel. Tus respuestas y el nombre siguen guardados; envía /confirmar para reintentar o /cancelar para salir.", reply_markup=CONFIRM_KEYBOARD)
                return True
            sessions.pop(chat_id, None)
            client.send_message(chat_id, "Excel generado y enviado con el nombre que elegiste.", reply_markup=main_keyboard)
        return True
    if text == "⏭️ No aplica":
        text = "No aplica"
    if not text or message.get("photo") or message.get("document"):
        client.send_message(chat_id, "Este formato solo utiliza datos escritos. " + ("Escribe el nombre del reporte." if session.awaiting_confirmation and not session.report_name else "Usa los botones del reporte o responde la pregunta actual."), reply_markup=(NAME_KEYBOARD if not session.report_name else CONFIRM_KEYBOARD) if session.awaiting_confirmation else INPUT_KEYBOARD)
        return True
    if command.startswith("/"):
        client.send_message(chat_id, "Responde el apartado actual o usa /cancelar.")
        return True
    if session.awaiting_confirmation:
        if not session.report_name:
            name = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", text)
            name = re.sub(r"\.(?:xlsx|pdf)$", "", name, flags=re.IGNORECASE).strip(" .")[:100].rstrip(" .")
            if not name:
                client.send_message(chat_id, "Escribe un nombre válido.", reply_markup=NAME_KEYBOARD)
                return True
            session.report_name = name
        _prompt(client, chat_id, session)
        return True
    key, _ = INSPECTION_FIELDS[session.next_field]
    try:
        answer = validate_answer(key, text, session.answers)
    except ValueError as error:
        client.send_message(chat_id, str(error) + "\n" + _question(session), reply_markup=INPUT_KEYBOARD)
        return True
    session.answers[key] = answer
    session.next_field += 1
    if session.next_field < len(INSPECTION_FIELDS):
        client.send_message(chat_id, _question(session), reply_markup=INPUT_KEYBOARD)
    else:
        session.awaiting_confirmation = True
        summary = "Resumen del FQS-OPE-F005:\n" + "\n".join(f"{label}: {session.answers[key]}" for key, label in INSPECTION_FIELDS)
        client.send_message(chat_id, summary, reply_markup=NAME_KEYBOARD)
        _prompt(client, chat_id, session)
    return True
