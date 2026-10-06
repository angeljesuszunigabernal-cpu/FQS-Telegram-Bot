"""Small standard-library client for the Telegram Bot API."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

MAX_TELEGRAM_FILE_BYTES = 20 * 1024 * 1024


class TelegramAPIError(RuntimeError):
    """Raised when Telegram returns an API or network error."""


class TelegramClient:
    def __init__(self, token: str) -> None:
        self._base_url = f"https://api.telegram.org/bot{token}"
        self._file_base_url = f"https://api.telegram.org/file/bot{token}"

    def call(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        body = json.dumps(payload or {}).encode("utf-8")
        request = Request(
            f"{self._base_url}/{method}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        long_poll_seconds = int((payload or {}).get("timeout", 0))

        try:
            with urlopen(request, timeout=max(40, long_poll_seconds + 10)) as response:
                result = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            raise TelegramAPIError(
                f"Telegram returned HTTP {error.code} for {method}."
            ) from None
        except (URLError, TimeoutError, OSError):
            raise TelegramAPIError(f"Could not reach Telegram while calling {method}.") from None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise TelegramAPIError(f"Telegram returned an invalid response for {method}.") from None

        if not isinstance(result, dict) or not result.get("ok"):
            description = result.get("description", "unknown error") if isinstance(result, dict) else "invalid response"
            raise TelegramAPIError(f"Telegram API error for {method}: {description}")

        return result.get("result")

    def get_updates(self, offset: int | None) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "timeout": 30,
            "allowed_updates": ["message"],
        }
        if offset is not None:
            payload["offset"] = offset

        result = self.call("getUpdates", payload)
        if not isinstance(result, list):
            raise TelegramAPIError("Telegram returned an invalid updates list.")
        return [update for update in result if isinstance(update, dict)]

    def download_file(self, file_id: str, destination: Path) -> None:
        result = self.call("getFile", {"file_id": file_id})
        if not isinstance(result, dict) or not isinstance(result.get("file_path"), str):
            raise TelegramAPIError("Telegram no devolvió la ruta de la fotografía.")

        file_path = quote(result["file_path"], safe="/")
        request = Request(f"{self._file_base_url}/{file_path}", method="GET")
        try:
            with urlopen(request, timeout=60) as response:
                content = response.read(MAX_TELEGRAM_FILE_BYTES + 1)
        except HTTPError as error:
            raise TelegramAPIError(
                f"Telegram devolvió el estado HTTP {error.code} al descargar la fotografía."
            ) from None
        except (URLError, TimeoutError, OSError):
            raise TelegramAPIError(
                "No se pudo conectar con Telegram para descargar la fotografía."
            ) from None

        if len(content) > MAX_TELEGRAM_FILE_BYTES:
            raise TelegramAPIError("La fotografía excede el tamaño máximo permitido por Telegram.")
        if not content:
            raise TelegramAPIError("Telegram devolvió una fotografía vacía.")

        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        except OSError:
            raise TelegramAPIError(
                "No se pudo guardar temporalmente la fotografía."
            ) from None

    def send_message(
        self,
        chat_id: int | str,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> None:
        for start in range(0, max(1, len(text)), 4096):
            payload: dict[str, Any] = {
                "chat_id": chat_id,
                "text": text[start : start + 4096],
            }
            if reply_markup is not None:
                payload["reply_markup"] = reply_markup
            self.call("sendMessage", payload)

    def send_document(
        self,
        chat_id: int | str,
        filename: str,
        content: bytes,
        caption: str,
    ) -> None:
        boundary = f"----TelegramBot{uuid.uuid4().hex}"
        parts = [
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
            f"{chat_id}\r\n",
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="caption"\r\n\r\n'
            f"{caption}\r\n",
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'
            "Content-Type: application/pdf\r\n\r\n",
        ]
        body = (
            "".join(parts).encode("utf-8")
            + content
            + f"\r\n--{boundary}--\r\n".encode("ascii")
        )
        request = Request(
            f"{self._base_url}/sendDocument",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )

        try:
            with urlopen(request, timeout=60) as response:
                result = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            raise TelegramAPIError(
                f"Telegram devolvió el estado HTTP {error.code} al enviar el PDF."
            ) from None
        except (URLError, TimeoutError, OSError):
            raise TelegramAPIError("No se pudo conectar con Telegram para enviar el PDF.") from None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise TelegramAPIError("Telegram devolvió una respuesta no válida al enviar el PDF.") from None

        if not isinstance(result, dict) or not result.get("ok"):
            description = (
                result.get("description", "error desconocido")
                if isinstance(result, dict)
                else "respuesta no válida"
            )
            raise TelegramAPIError(f"Telegram no pudo enviar el PDF: {description}")
