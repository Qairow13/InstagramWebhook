import os
import json
import logging
from typing import List, Tuple, Optional

import requests
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, JSONResponse

# ---------------- НАСТРОЙКИ ----------------

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ig-webhook")

VERIFY_TOKEN   = (os.getenv("VERIFY_TOKEN", "apiapimeta") or "").strip()
PAGE_TOKEN     = (os.getenv("PAGE_TOKEN", "") or "").strip()
IG_USER_ID     = (os.getenv("IG_USER_ID", "") or "").strip()
GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY", "") or "").strip()
SYSTEM_PROMPT  = (os.getenv("SYSTEM_PROMPT", "Ты консультант. Отвечай кратко и по делу.") or "").strip()

GRAPH_BASE = "https://graph.facebook.com/v20.0"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent"

# ВАЖНО: app создаём СРАЗУ, ДО декораторов
app = FastAPI(title="IG Webhook Bot", version="1.0.0")

# ---------------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ----------------


def fetch_message_by_mid(mid: str) -> Tuple[Optional[str], Optional[str]]:
    """
    По message_id (mid) тянем текст и id отправителя из Graph API.
    Возвращаем (sender_id, text) или (None, None), если не получилось.
    """
    if not PAGE_TOKEN:
        log.error("PAGE_TOKEN пустой, не могу сходить за текстом сообщения.")
        return None, None

    url = f"{GRAPH_BASE}/{mid}"
    params = {
        "access_token": PAGE_TOKEN,
        "fields": "message,from,to"
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        log.info("GET MESSAGE BY MID %s -> %s %s", mid, r.status_code, r.text)
        if r.status_code != 200:
            return None, None

        data = r.json()
        text = (data.get("message") or "").strip()
        sender = (data.get("from") or {}).get("id")
        if not sender or not text:
            return None, None
        return sender, text
    except Exception as e:
        log.exception("Ошибка при fetch_message_by_mid: %s", e)
        return None, None


def extract_messages(payload: dict) -> List[Tuple[str, str]]:
    """
    Достаём пары (sender_id, text) из payload Instagram.
    Поддерживаем:
    - entry[].messaging[].message.text
    - entry[].messaging[].message_edit.mid (через отдельный запрос)
    - entry[].changes[].value.messages[] (старый формат)
    """
    out: List[Tuple[str, str]] = []

    # 1) Новый формат от IG (через Messenger API): entry[].messaging[]
    for entry in payload.get("entry", []) or []:
        for m in entry.get("messaging", []) or []:
            # Обычное входящее сообщение
            if "message" in m and isinstance(m["message"], dict):
                msg = m["message"]
                text = (msg.get("text") or "").strip()
                sender = (m.get("sender") or {}).get("id")
                if sender and text:
                    out.append((sender, text))

            # Событие message_edit (как в твоём логе)
            elif "message_edit" in m and isinstance(m["message_edit"], dict):
                mid = m["message_edit"].get("mid")
                if mid:
                    sender, text = fetch_message_by_mid(mid)
                    if sender and text:
                        out.append((sender, text))

    # 2) Старый формат: entry[].changes[].value.messages[]
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value") or {}
            for msg in value.get("messages", []) or []:
                sender = (msg.get("from") or {}).get("id") or (value.get("from") or {}).get("id")
                text = ""
                if isinstance(msg.get("text"), dict):
                    text = (msg["text"].get("body") or "").strip()
                else:
                    text = (msg.get("text") or "").strip()
                if sender and text:
                    out.append((sender, text))

    return out


def gemini_reply(user_text: str) -> str:
    """
    Вызов Gemini (Google AI Studio).
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
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        log.exception("Gemini error: %s", e)
        return "Извините, сейчас не могу ответить. Напишите, пожалуйста, позже."


def send_ig_text(recipient_id: str, text: str) -> None:
    """
    Отправка сообщения обратно в IG Direct.
    """
    if not PAGE_TOKEN or not IG_USER_ID:
        log.error("PAGE_TOKEN/IG_USER_ID отсутствуют. Проверь переменные окружения.")
        return

    url = f"{GRAPH_BASE}/{IG_USER_ID}/messages"
    params = {"access_token": PAGE_TOKEN}
    data = {
        "recipient": {"id": recipient_id},
        "message": {"text": text},
        "messaging_type": "RESPONSE",
    }

    log.info("➡️ SEND TO IG: %s %s", recipient_id, text)

    try:
        r = requests.post(url, params=params, json=data, timeout=20)
        log.info("SEND IG STATUS %s: %s", r.status_code, r.text)
    except Exception as e:
        log.exception("SEND IG ERROR: %s", e)


# ---------------- РОУТЫ ----------------


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
        raw = await request.body()
        raw_text = raw.decode("utf-8", errors="ignore")
        log.info("RAW BODY: %s", raw_text)
        body = json.loads(raw_text or "{}")
    except Exception:
        log.exception("Invalid JSON!")
        return JSONResponse({"status": "bad json"}, status_code=400)

    log.info("📩 incoming: %s", json.dumps(body, ensure_ascii=False))

    pairs = extract_messages(body)
    if not pairs:
        # Ничего полезного (например, служебное событие)
        return {"status": "ignored"}

    for sender_id, text in pairs:
        try:
            reply = gemini_reply(text)
            send_ig_text(sender_id, reply)
        except Exception as e:
            log.exception("Ошибка обработки сообщения от %s: %s", sender_id, e)

    return {"status": "ok"}

