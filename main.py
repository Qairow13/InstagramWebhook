import os
import json
import logging
from typing import List, Tuple

import requests
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, JSONResponse

# ------------------ Конфиг ------------------
VERIFY_TOKEN   = (os.getenv("VERIFY_TOKEN", "apiapimeta") or "").strip()
PAGE_TOKEN     = (os.getenv("PAGE_TOKEN", "") or "").strip()          # Page Access Token (EAAG...)
IG_USER_ID     = (os.getenv("IG_USER_ID", "") or "").strip()          # например: 17841470729274967
GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY", "") or "").strip()
SYSTEM_PROMPT  = (os.getenv("SYSTEM_PROMPT", "Ты помощник. Отвечай кратко.") or "").strip()

GRAPH_BASE = "https://graph.facebook.com/v24.0"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent"

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ig-webhook")

app = FastAPI(title="IG Webhook Bot", version="1.0.0")


# ------------------ Вспомогательные функции ------------------
def extract_messages(payload: dict) -> List[Tuple[str, str]]:
    """
    Достаём пары (sender_id, text) из разных форматов webhook IG.
    Возвращает список (sender_id, text).
    """
    out: List[Tuple[str, str]] = []

    # Вариант 1: entry[].changes[].value.messages[]
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {}) or {}
            # Новые IG webhook-и часто приходят в этом формате
            # Пример: value = { "messages": [{ "from": {"id": "IGSID"}, "text": {"body": "hi"} }], ... }
            for m in value.get("messages", []) or []:
                sender = (m.get("from") or {}).get("id") or (value.get("from") or {}).get("id")
                text = ""
                if isinstance(m.get("text"), dict):
                    text = (m["text"].get("body") or "").strip()
                else:
                    text = (m.get("text") or "").strip()
                if sender and text:
                    out.append((sender, text))

    # Вариант 2: entry[].messaging[] (иногда встречается)
    for entry in payload.get("entry", []):
        for msg in entry.get("messaging", []) or []:
            sender = (msg.get("sender") or {}).get("id")
            message = msg.get("message") or {}
            if sender and message and not message.get("is_echo"):
                text = (message.get("text") or "").strip()
                if text:
                    out.append((sender, text))

    return out


def gemini_reply(user_text: str) -> str:
    """
    Минимальный вызов Gemini (Google AI Studio).
    """
    if not GEMINI_API_KEY:
        return "Извините, мой ИИ-ключ не настроен."

    try:
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": f"{SYSTEM_PROMPT}\n\nВопрос клиента: {user_text}"}
                    ],
                }
            ]
        }
        r = requests.post(
            GEMINI_URL,
            params={"key": GEMINI_API_KEY},
            json=payload,
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        # Извлекаем первый ответ
        return (
            data["candidates"][0]["content"]["parts"][0]["text"]
            .strip()
        )
    except Exception as e:
        log.exception("Gemini error: %s", getattr(e, "message", e))
        return "Извините, сейчас не могу ответить. Напишите, пожалуйста, позже."


def send_ig_text(recipient_id: str, text: str) -> None:
    """
    Отправка сообщения обратно в IG Direct.
    """
    if not PAGE_TOKEN or not IG_USER_ID:
        log.error("PAGE_TOKEN/IG_USER_ID отсутствуют. Проверьте переменные окружения.")
        return

    url = f"{GRAPH_BASE}/{IG_USER_ID}/messages"
    params = {"access_token": PAGE_TOKEN}
    data = {
        "recipient": {"id": recipient_id},
        "message": {"text": text},
        "messaging_type": "RESPONSE",  # стандартный тип ответа
    }

    try:
        r = requests.post(url, params=params, json=data, timeout=20)
        log.info("SEND IG STATUS %s: %s", r.status_code, r.text)
    except Exception as e:
        log.exception("SEND IG ERROR: %s", getattr(e, "message", e))


# ------------------ Роуты ------------------
@app.get("/")
def health():
    return {"ok": True, "service": "IG webhook", "verify_token": VERIFY_TOKEN}


@app.get("/webhook")
async def verify_webhook(request: Request):
    """
    Проверка URL от Meta.
    Должны вернуть hub.challenge как text/plain при корректном маркере.
    """
    p = request.query_params
    mode = (p.get("hub.mode") or "").strip()
    token = (p.get("hub.verify_token") or "").strip()
    challenge = p.get("hub.challenge")

    log.info("VERIFY -> mode=%s token=%s", mode, token)

    if mode == "subscribe" and token == VERIFY_TOKEN and challenge:
        return PlainTextResponse(str(challenge))
    return PlainTextResponse("forbidden", status_code=403)


@app.post("/webhook")
async def webhook_event(request: Request):
    """
    Основной вход: Instagram шлёт сюда события.
    Достаём входящие сообщения, генерим ответ (Gemini) и отправляем в Direct.
    """
    try:
        body = await request.json()
    except Exception:
        text = await request.body()
        log.error("Invalid JSON: %s", text)
        return JSONResponse({"status": "bad json"}, status_code=400)

    log.info("📩 incoming: %s", json.dumps(body, ensure_ascii=False))

    # Достаём все (sender_id, text) и отвечаем каждому
    pairs = extract_messages(body)
    for sender_id, text in pairs:
        try:
            reply = gemini_reply(text)
            send_ig_text(sender_id, reply)
        except Exception as e:
            log.exception("Handle error for sender %s: %s", sender_id, e)

    return {"status": "ok"}
