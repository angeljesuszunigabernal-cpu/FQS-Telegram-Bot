"""Telegram capture for F009, with one photo and description per step."""

from __future__ import annotations

import logging
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from telegram_bot.excel_method import EPP_FIELDS, METHOD_BASE_NAME, METHOD_FIELDS, generate_method_excel, image_info, validate_method_answer

INPUT_KEYBOARD = {"keyboard": [[{"text": "⏭️ No aplica"}], [{"text": "❌ Cancelar reporte"}]], "resize_keyboard": True}
YES_NO_KEYBOARD = {"keyboard": [[{"text": "✅ Sí"}, {"text": "❌ No"}], [{"text": "❌ Cancelar reporte"}]], "resize_keyboard": True}
PHOTO_KEYBOARD = {"keyboard": [[{"text": "✅ Terminar pasos"}], [{"text": "❌ Cancelar reporte"}]], "resize_keyboard": True}
NEXT_KEYBOARD = {"keyboard": [[{"text": "➕ Agregar paso"}, {"text": "✅ Terminar pasos"}], [{"text": "❌ Cancelar reporte"}]], "resize_keyboard": True}
NAME_KEYBOARD = {"keyboard": [[{"text": "❌ Cancelar reporte"}]], "resize_keyboard": True}
CONFIRM_KEYBOARD = {"keyboard": [[{"text": "✅ Enviar reporte"}], [{"text": "🔄 Empezar de nuevo"}, {"text": "❌ Cancelar reporte"}]], "resize_keyboard": True}


@dataclass
class MethodSession:
    answers: dict[str, str] = field(default_factory=dict)
    next_field: int = 0
    awaiting_confirmation: bool = False
    report_name: str = ""
    stage: str = "fields"
    steps: list[tuple[str, Path]] = field(default_factory=list)
    pending_photo: Path | None = None
    _photo_temp_dir: tempfile.TemporaryDirectory | None = field(default=None, repr=False)

    def next_photo_path(self) -> Path:
        if self._photo_temp_dir is None:
            self._photo_temp_dir = tempfile.TemporaryDirectory(prefix="fqs-f009-")
        return Path(self._photo_temp_dir.name) / f"paso-{len(self.steps) + 1:02d}.jpg"

    def cleanup(self) -> None:
        self.steps.clear()
        self.pending_photo = None
        if self._photo_temp_dir is not None:
            self._photo_temp_dir.cleanup()
            self._photo_temp_dir = None


def _prompt(client, chat_id, session):
    if session.stage == "fields":
        key, label = METHOD_FIELDS[session.next_field]
        keyboard = YES_NO_KEYBOARD if key in EPP_FIELDS else INPUT_KEYBOARD
        client.send_message(chat_id, f"{session.next_field + 1}/{len(METHOD_FIELDS)} · {label}", reply_markup=keyboard)
    elif session.stage == "photo":
        client.send_message(chat_id, f"Paso {len(session.steps) + 1}/12: envía una fotografía. Puedes escribir su descripción como pie de foto o enviarla después.", reply_markup=PHOTO_KEYBOARD)
    elif session.stage == "description":
        client.send_message(chat_id, f"Escribe la descripción del paso {len(session.steps) + 1} (máximo 500 caracteres).", reply_markup=NAME_KEYBOARD)
    elif session.stage == "next":
        client.send_message(chat_id, f"Paso {len(session.steps)} guardado con su fotografía y descripción. ¿Agregas otro o terminas?", reply_markup=NEXT_KEYBOARD)
    elif session.stage == "name":
        client.send_message(chat_id, "¿Qué nombre quieres agregar al archivo del método de inspección?", reply_markup=NAME_KEYBOARD)
    else:
        client.send_message(chat_id, f"Archivo: {METHOD_BASE_NAME} - {session.report_name}.xlsx\nToca «✅ Enviar reporte» para recibirlo.", reply_markup=CONFIRM_KEYBOARD)


def _finish(client, chat_id, session):
    if session.stage == "description":
        client.send_message(chat_id, "Falta la descripción de la fotografía que acabas de enviar.")
        _prompt(client, chat_id, session)
        return
    if not session.steps:
        client.send_message(chat_id, "Agrega al menos un paso con fotografía y descripción.")
        _prompt(client, chat_id, session)
        return
    session.stage = "name"
    session.awaiting_confirmation = True
    summary = "Resumen del FQS-OPE-F009:\n" + "\n".join(f"{label}: {session.answers[key]}" for key, label in METHOD_FIELDS)
    summary += "\n\n" + "\n".join(f"Paso {index}: {description} (foto recibida)" for index, (description, _) in enumerate(session.steps, 1))
    client.send_message(chat_id, summary, reply_markup=NAME_KEYBOARD)
    _prompt(client, chat_id, session)


def _description(client, chat_id, session, text):
    try:
        description = validate_method_answer("descripcion", text)
    except ValueError as error:
        client.send_message(chat_id, str(error))
        _prompt(client, chat_id, session)
        return
    session.steps.append((description, session.pending_photo))
    session.pending_photo = None
    session.stage = "next"
    if len(session.steps) == 12:
        _finish(client, chat_id, session)
    else:
        _prompt(client, chat_id, session)


def handle_method(client, chat_id, message, sessions, main_keyboard) -> bool:
    session = sessions.get(chat_id)
    text = message.get("text", "")
    text = text.strip() if isinstance(text, str) else ""
    command = text.split(maxsplit=1)[0].split("@", 1)[0].lower() if text else ""
    start = command in ("/metodo", "/f009") or text == "🔎 FQS-OPE-F009"
    if isinstance(session, MethodSession) and text == "🔄 Empezar de nuevo":
        start = True
    if start:
        previous = sessions.pop(chat_id, None)
        if previous is not None:
            previous.cleanup()
        session = MethodSession()
        sessions[chat_id] = session
        client.send_message(chat_id, "Vamos a completar el FQS-OPE-F009 · Método de Inspección en Excel. Capturaremos los datos generales y hasta 12 pasos, cada uno con su fotografía y descripción.")
        _prompt(client, chat_id, session)
        return True
    if not isinstance(session, MethodSession):
        return False
    if command in ("/nuevo", "/semanal", "/inspeccion", "/f005", "/start", "/elegir", "/ayuda", "/help", "/ping") or text in ("📄 FQS-OPE-F001", "📅 FQS-OPE-F008", "TK-EPS", "📊 FQS-OPE-F005", "📝 Nuevo reporte", "↩️ Menú principal", "ℹ️ Ayuda"):
        return False
    if command == "/cancelar" or text == "❌ Cancelar reporte":
        sessions.pop(chat_id, None)
        session.cleanup()
        client.send_message(chat_id, "Método de inspección cancelado.", reply_markup=main_keyboard)
        return True
    if command == "/confirmar" or text == "✅ Enviar reporte":
        if session.stage != "confirm":
            client.send_message(chat_id, "Completa primero los datos, los pasos y el nombre del archivo.")
            _prompt(client, chat_id, session)
            return True
        try:
            content = generate_method_excel(session.answers, session.steps)
            client.send_document(chat_id, f"{METHOD_BASE_NAME} - {session.report_name}.xlsx", content, f"FQS-OPE-F009 · {session.report_name} · {len(session.steps)} pasos")
        except Exception:
            logging.exception("No se pudo generar o enviar el Excel F009.")
            client.send_message(chat_id, "No pude generar o enviar el Excel. Tus datos, fotografías y nombre siguen guardados; envía /confirmar para reintentar o /cancelar para salir.", reply_markup=CONFIRM_KEYBOARD)
            return True
        sessions.pop(chat_id, None)
        session.cleanup()
        client.send_message(chat_id, "Excel del método de inspección generado y enviado con sus fotografías.", reply_markup=main_keyboard)
        return True
    if command == "/listo" or text == "✅ Terminar pasos":
        if session.stage in ("photo", "description", "next"):
            _finish(client, chat_id, session)
        else:
            _prompt(client, chat_id, session)
        return True
    if text == "➕ Agregar paso":
        if session.stage == "next":
            session.stage = "photo"
        _prompt(client, chat_id, session)
        return True
    photos = message.get("photo")
    if isinstance(photos, list) and photos:
        if session.stage not in ("photo", "next"):
            client.send_message(chat_id, "Recibo una fotografía por paso. Responde primero el apartado actual.")
            _prompt(client, chat_id, session)
            return True
        candidates = [photo for photo in photos if isinstance(photo, dict) and isinstance(photo.get("file_id"), str)]
        if not candidates:
            client.send_message(chat_id, "No pude identificar la fotografía. Inténtalo de nuevo.")
            return True
        photo = max(candidates, key=lambda item: int(item.get("width") or 0) * int(item.get("height") or 0))
        destination = session.next_photo_path()
        try:
            client.download_file(photo["file_id"], destination)
            data = destination.read_bytes()
            image_info(data)
            if sum(path.stat().st_size for _, path in session.steps) + len(data) > 40 * 1024 * 1024:
                raise ValueError("Las fotos superan 40 MB en total. Envía una versión más pequeña de esta fotografía.")
        except Exception as error:
            destination.unlink(missing_ok=True)
            logging.exception("No se pudo recibir la foto del método de inspección.")
            client.send_message(chat_id, str(error) if isinstance(error, ValueError) else "No pude recibir esa fotografía. Reenvíala para continuar.")
            return True
        session.pending_photo = destination
        session.stage = "description"
        caption = message.get("caption")
        if isinstance(caption, str) and caption.strip():
            _description(client, chat_id, session, caption.strip())
        else:
            _prompt(client, chat_id, session)
        return True
    if not text or command.startswith("/"):
        _prompt(client, chat_id, session)
        return True
    if session.stage == "fields":
        key, _ = METHOD_FIELDS[session.next_field]
        answer = {"✅ Sí": "Sí", "❌ No": "No", "⏭️ No aplica": "No aplica"}.get(text, text)
        try:
            session.answers[key] = validate_method_answer(key, answer)
        except ValueError as error:
            client.send_message(chat_id, str(error))
            _prompt(client, chat_id, session)
            return True
        session.next_field += 1
        if session.next_field == len(METHOD_FIELDS):
            session.stage = "photo"
        _prompt(client, chat_id, session)
    elif session.stage == "description":
        _description(client, chat_id, session, text)
    elif session.stage == "name":
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", text)
        name = re.sub(r"\.(?:xlsx|pdf)$", "", name, flags=re.IGNORECASE).strip(" .")[:100].rstrip(" .")
        if not name:
            client.send_message(chat_id, "Escribe un nombre válido para el archivo.")
        else:
            session.report_name = name
            session.stage = "confirm"
        _prompt(client, chat_id, session)
    else:
        _prompt(client, chat_id, session)
    return True
