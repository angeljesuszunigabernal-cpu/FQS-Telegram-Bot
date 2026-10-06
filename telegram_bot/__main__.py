"""Run the Telegram bot with long polling."""

from __future__ import annotations

import logging
import os
import time

from telegram_bot.api import TelegramAPIError, TelegramClient
from telegram_bot.handlers import handle_update

POLL_RETRY_MAX_SECONDS = 30


def run_polling(client: TelegramClient) -> None:
    identity = client.call("getMe")
    username = identity.get("username", "unknown") if isinstance(identity, dict) else "unknown"
    logging.info("Connected to Telegram as @%s. Waiting for messages.", username)

    offset: int | None = None
    retry_delay = 1

    while True:
        try:
            updates = client.get_updates(offset)
            retry_delay = 1
        except TelegramAPIError as error:
            logging.warning("%s Retrying in %s second(s).", error, retry_delay)
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, POLL_RETRY_MAX_SECONDS)
            continue

        for update in updates:
            update_id = update.get("update_id")
            try:
                handle_update(client, update)
            except Exception:
                logging.exception("Could not process a Telegram update.")
            finally:
                if isinstance(update_id, int):
                    offset = max(offset or 0, update_id + 1)


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit(
            "Falta TELEGRAM_BOT_TOKEN. Agrégalo como variable de entorno y vuelve a iniciar el bot."
        )

    try:
        run_polling(TelegramClient(token))
    except KeyboardInterrupt:
        logging.info("Bot stopped.")


if __name__ == "__main__":
    main()
