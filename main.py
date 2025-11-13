import os
import json
import logging
from typing import List, Tuple

import requests
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, JSONResponse

# ... твои константы VERIFY_TOKEN, PAGE_TOKEN, IG_USER_ID, GEMINI_API_KEY и т.д.

log = logging.getLogger("ig-webhook")


def fetch_message_by_mid(mid: str) -> Tuple[str | None, str | None]:
    """
    По message_id (mid) тянем текст и id отправителя из Graph API.
    Возвращаем (sender_id, text) или (None, None), если не получилось.
    """
    if not PAGE_TOKEN:
        log.error("PAGE_TOKEN пустой, не могу сходить за текстом сообщения.")
        return None, None

    url = f"https://graph.facebook.com/v20.0/{mid}"
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
    - (на всякий случай) entry[].changes[].value.messages[]
    """
    out: List[Tuple[str, str]] = []

    # 1) Новый формат от IG через Messenger API: entry[].messaging[]
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


@app.post("/webhook")
async def webhook_event(request: Request):
    """
    Основной вход для POST от Meta.
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

    # Достаём все (sender_id, text) и отвечаем
    pairs = extract_messages(body)
    if not pairs:
        # Ничего отвечать не нужно (например, какой-то служебный webhook)
        return {"status": "ignored"}

    for sender_id, text in pairs:
        try:
            reply = gemini_reply(text)  # твоя функция генерации ответа
            send_ig_text(sender_id, reply)  # твоя функция отправки
        except Exception as e:
            log.exception("Ошибка обработки сообщения от %s: %s", sender_id, e)

    return {"status": "ok"}


