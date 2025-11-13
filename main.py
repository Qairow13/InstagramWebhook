import os
import json
import logging
from typing import Any, Dict

import requests
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, JSONResponse

# ----------------- НАСТРОЙКА ЛОГГЕРА -----------------
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ig-webhook")

# ----------------- ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ -----------------
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "apiapimeta")
PAGE_TOKEN = os.getenv("PAGE_TOKEN", "")      # свежий токен страницы!
IG_USER_ID = os.getenv("IG_USER_ID", "")      # id бизнес-аккаунта Instagram

if not PAGE_TOKEN:
    log.warning("⚠ PAGE_TOKEN не задан! Ответы в Instagram работать не будут.")
if not IG_USER_ID:
    log.warning("⚠ IG_USER_ID не задан! Ответы в Instagram работать не будут.")

# ----------------- FASTAPI ПРИЛОЖЕНИЕ -----------------
app = FastAPI()


@app.get("/")
def home():
    """Простой health-check."""
    return {
        "ok": True,
        "service": "Instagram webhook",
        "message": "Сервис работает. /webhook используется для Instagram."
    }


# ----------------- ВЕБХУК: ПОДТВЕРЖДЕНИЕ -----------------
@app.get("/webhook")
async def verify_webhook(request: Request):
    """
    Meta (Facebook) вызывает этот GET, когда ты настраиваешь Webhook.
    Мы должны вернуть hub.challenge, если hub.verify_token совпадает.
    """
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    log.info("VERIFY -> mode=%s token=%s", mode, token)

    if mode == "subscribe" and token == VERIFY_TOKEN and challenge:
        return PlainTextResponse(challenge)

    return PlainTextResponse("forbidden", status_code=403)


# ----------------- ФУНКЦИЯ ОТВЕТА В INSTAGRAM -----------------
def send_ig_message(recipient_id: str, text: str) -> None:
    """
    Отправляем ответ в директ Instagram.
    recipient_id — IG_USER_ID пользователя (того, кто написал боту).
    """
    if not PAGE_TOKEN or not IG_USER_ID:
        log.warning("❌ Нет PAGE_TOKEN или IG_USER_ID — не могу отправить сообщение.")
        return

    url = f"https://graph.facebook.com/v24.0/{IG_USER_ID}/messages"
    params = {"access_token": PAGE_TOKEN}
    payload = {
        "messaging_product": "instagram",
        "recipient": {"id": recipient_id},
        "message": {"text": text},
    }

    log.info("➡️ SEND TO IG: %s %s", recipient_id, text)
    resp = requests.post(url, params=params, json=payload)

    try:
        resp.raise_for_status()
        log.info("✅ IG SEND STATUS %s: %s", resp.status_code, resp.text)
    except requests.HTTPError:
        log.error("❌ IG SEND STATUS %s: %s", resp.status_code, resp.text)


# ----------------- ВРЕМЕННАЯ ФУНКЦИЯ “AI” -----------------
def ask_ai_stub(user_text: str) -> str:
    """
    Вместо настоящего Gemini — просто простейший ответ.
    Когда починим Gemini, заменим эту функцию.
    """
    return f"Ты написал(а): {user_text}"


# ----------------- ВЕБХУК: ОСНОВНОЙ POST -----------------
@app.post("/webhook")
async def webhook_event(request: Request):
    """
    Meta присылает сюда события:
    - новые сообщения
    - редактирование сообщений
    и т.д.
    """
    raw_body = await request.body()
    log.info("RAW BODY: %s", raw_body.decode("utf-8", errors="ignore"))

    # Парсим JSON
    try:
        data: Dict[str, Any] = json.loads(raw_body.decode("utf-8"))
    except json.JSONDecodeError:
        log.error("Invalid JSON: %s", raw_body)
        return JSONResponse({"status": "bad json"}, status_code=400)

    log.info("📩 incoming: %s", json.dumps(data, ensure_ascii=False))

    # Разбираем структуру Instagram webhook
    for entry in data.get("entry", []):
        messaging_events = entry.get("messaging") or []

        for event in messaging_events:
            sender_id = event.get("sender", {}).get("id")
            message = event.get("message")

            # Нас интересуют только обычные сообщения с текстом
            if sender_id and message and "text" in message:
                user_text = message["text"]
                log.info("Получено сообщение от %s: %s", sender_id, user_text)

                # ВРЕМЕННЫЙ AI
                reply_text = ask_ai_stub(user_text)

                # Отправляем ответ
                send_ig_message(sender_id, reply_text)

    return {"status": "ok"}


