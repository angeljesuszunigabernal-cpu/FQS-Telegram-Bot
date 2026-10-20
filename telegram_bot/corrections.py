"""Edit one captured answer or photograph without restarting the report."""

from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path

from telegram_bot.excel_inspection import INSPECTION_FIELDS, validate_answer
from telegram_bot.excel_method import METHOD_FIELDS, image_info, validate_method_answer
from telegram_bot.pdf_form import PDF_FIELDS
from telegram_bot.pdf_weekly import WEEKLY_FIELDS
from telegram_bot.word_magna import MAGNA_FIELDS, validate_magna_answer

EDIT_BUTTON = "✏️ Corregir respuesta"
BACK_BUTTON = "↩️ Volver al reporte"
VALUE_KEYBOARD = {"keyboard": [[{"text": "No aplica"}], [{"text": BACK_BUTTON}]], "resize_keyboard": True}
PHOTO_KEYBOARD = {"keyboard": [[{"text": BACK_BUTTON}]], "resize_keyboard": True}
NAVIGATION = frozenset(("/nuevo", "/semanal", "/inspeccion", "/f005", "/metodo", "/f009", "/magna", "/start", "/elegir", "/ayuda", "/help", "/ping", "/cancelar"))
NAVIGATION_BUTTONS = frozenset(("📄 FQS-OPE-F001", "📅 FQS-OPE-F008", "TK-EPS", "📊 FQS-OPE-F005", "🔎 FQS-OPE-F009", "MAGNA", "📝 Nuevo reporte", "↩️ Menú principal", "ℹ️ Ayuda", "❌ Cancelar reporte", "🔄 Empezar de nuevo"))


def _options(session):
    kind = type(session).__name__
    fields = {
        "FormSession": PDF_FIELDS[:-1], "WeeklySession": WEEKLY_FIELDS,
        "InspectionSession": INSPECTION_FIELDS, "MethodSession": METHOD_FIELDS,
        "MagnaSession": MAGNA_FIELDS,
    }.get(kind, ())
    options = [("answer", key, label) for key, label in fields if key in session.answers]
    if kind == "FormSession":
        options += [("form_photo", index, f"Fotografía de evidencia {index + 1}") for index in range(len(session.evidence_photos))]
    if kind == "MethodSession":
        for index in range(len(session.steps)):
            options += [("description", index, f"Paso {index + 1}: descripción"), ("step_photo", index, f"Paso {index + 1}: fotografía")]
        if session.pending_photo is not None:
            options.append(("pending_photo", None, f"Paso {len(session.steps) + 1}: fotografía pendiente"))
    if getattr(session, "report_name", ""):
        options.append(("name", None, "Nombre que agregaste al archivo"))
    return options


def _choose(client, chat_id, state):
    rows = [[{"text": f"{index}. {label}"[:100]}] for index, (_, _, label) in enumerate(state["options"], 1)]
    rows.append([{"text": BACK_BUTTON}])
    client.send_message(chat_id, "Elige el inciso que quieres corregir o escribe su número. Las demás respuestas y fotos se conservarán.", reply_markup={"keyboard": rows, "resize_keyboard": True})


def _validate(session, key, value):
    kind = type(session).__name__
    if kind == "InspectionSession":
        cleaned = validate_answer(key, value, session.answers)
        if key in ("fecha_inicio", "fecha_termino") and "fecha_termino" in session.answers:
            revised = {**session.answers, key: cleaned}
            validate_answer("fecha_termino", revised["fecha_termino"], revised)
        return cleaned
    if kind == "MethodSession":
        return validate_method_answer(key, value)
    if kind == "MagnaSession":
        return validate_magna_answer(key, value)
    value = value.strip()
    if not value:
        raise ValueError("La respuesta no puede estar vacía.")
    return "No aplica" if value.casefold() in ("no aplica", "⏭️ no aplica") else value


def _photo_path(session, option):
    kind, key, _ = option
    if kind == "form_photo":
        return session.evidence_photos[key]
    if kind == "step_photo":
        return session.steps[key][1]
    return session.pending_photo


def handle_correction(client, chat_id, message, sessions, resume) -> bool:
    session = sessions.get(chat_id)
    text = message.get("text", "")
    text = text.strip() if isinstance(text, str) else ""
    command = text.split(maxsplit=1)[0].split("@", 1)[0].lower() if text else ""
    state = getattr(session, "_correction", None)
    if text == EDIT_BUTTON or command == "/corregir":
        if session is None:
            client.send_message(chat_id, "No hay un reporte en captura. Inicia uno desde «📝 Nuevo reporte».")
            return True
        options = _options(session)
        if not options:
            client.send_message(chat_id, "Todavía no hay respuestas para corregir. Contesta primero el apartado actual.")
            resume(client, chat_id, session)
            return True
        state = {"options": options, "selected": None}
        session._correction = state
        _choose(client, chat_id, state)
        return True
    if not state:
        return False
    if text == BACK_BUTTON or command == "/volver":
        session._correction = None
        resume(client, chat_id, session)
        return True
    if command in NAVIGATION or text in NAVIGATION_BUTTONS:
        session._correction = None
        return False
    if command in ("/confirmar", "/listo") or text in ("✅ Enviar reporte", "✅ Generar PDF", "✅ Terminar pasos", "✅ Terminar fotos"):
        client.send_message(chat_id, "Termina la corrección o toca «↩️ Volver al reporte» antes de continuar.", reply_markup=PHOTO_KEYBOARD)
        return True
    if command.startswith("/"):
        client.send_message(chat_id, "Escribe la corrección o toca «↩️ Volver al reporte».", reply_markup=PHOTO_KEYBOARD)
        return True
    if state["selected"] is None:
        match = re.fullmatch(r"(\d+)(?:\. .*)?", text)
        if not match or not 1 <= int(match.group(1)) <= len(state["options"]):
            _choose(client, chat_id, state)
            return True
        option = state["options"][int(match.group(1)) - 1]
        state["selected"] = option
        kind, key, label = option
        if kind in ("form_photo", "step_photo", "pending_photo"):
            client.send_message(chat_id, f"{label}: envía la nueva fotografía. La anterior se conservará hasta recibirla correctamente.", reply_markup=PHOTO_KEYBOARD)
        else:
            current = session.answers[key] if kind == "answer" else session.steps[key][0] if kind == "description" else session.report_name
            client.send_message(chat_id, f"{label}\nRespuesta actual: {current}\n\nEscribe la respuesta correcta.", reply_markup=VALUE_KEYBOARD)
        return True
    option = state["selected"]
    kind, key, label = option
    if kind in ("form_photo", "step_photo", "pending_photo"):
        candidates = [photo for photo in message.get("photo", []) if isinstance(photo, dict) and isinstance(photo.get("file_id"), str)]
        if not candidates:
            client.send_message(chat_id, "Envía la nueva imagen como fotografía por Telegram.", reply_markup=PHOTO_KEYBOARD)
            return True
        old_path = Path(_photo_path(session, option))
        new_path = old_path.with_name(f"correccion-{uuid.uuid4().hex}.jpg")
        photo = max(candidates, key=lambda item: int(item.get("width") or 0) * int(item.get("height") or 0))
        try:
            client.download_file(photo["file_id"], new_path)
            data = new_path.read_bytes()
            image_info(data)
            if type(session).__name__ == "MethodSession":
                total = sum(path.stat().st_size for _, path in session.steps if path != old_path)
                if session.pending_photo is not None and session.pending_photo != old_path:
                    total += session.pending_photo.stat().st_size
                if total + len(data) > 40 * 1024 * 1024:
                    raise ValueError("Las fotografías superan 40 MB. Envía una versión más pequeña.")
        except Exception as error:
            new_path.unlink(missing_ok=True)
            logging.exception("No se pudo recibir la fotografía corregida.")
            client.send_message(chat_id, str(error) if isinstance(error, ValueError) else "No pude recibir la nueva foto. La anterior sigue guardada; vuelve a enviarla.", reply_markup=PHOTO_KEYBOARD)
            return True
        if kind == "form_photo":
            session.evidence_photos[key] = new_path
        elif kind == "step_photo":
            session.steps[key] = (session.steps[key][0], new_path)
        else:
            session.pending_photo = new_path
        # Both files stay in the session's temporary directory until cleanup.
    else:
        if not text or message.get("photo") or message.get("document"):
            client.send_message(chat_id, "Escribe la respuesta correcta con texto.", reply_markup=VALUE_KEYBOARD)
            return True
        text = "No aplica" if text == "⏭️ No aplica" else text
        try:
            if kind == "name":
                value = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", text)
                value = re.sub(r"\.(?:xlsx|pdf|docx)$", "", value, flags=re.IGNORECASE).strip(" .")[:100].rstrip(" .")
                if not value:
                    raise ValueError("Escribe un nombre válido.")
            else:
                value = _validate(session, key if kind == "answer" else "descripcion", text)
        except ValueError as error:
            client.send_message(chat_id, str(error), reply_markup=VALUE_KEYBOARD)
            return True
        if kind == "answer":
            session.answers[key] = value
        elif kind == "description":
            session.steps[key] = (value, session.steps[key][1])
        else:
            session.report_name = value
    session._correction = None
    client.send_message(chat_id, f"{label}: corregido. Los demás datos se conservaron.")
    resume(client, chat_id, session)
    return True
