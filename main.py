from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
import os

app = FastAPI()

VERIFY_TOKEN = (os.getenv("VERIFY_TOKEN", "apiapimeta") or "").strip()

@app.get("/")
def home():
    return {"ok": True, "service": "IG webhook", "verify_token": VERIFY_TOKEN}

# Проверка вебхука (Meta делает GET сюда)
@app.get("/webhook")
async def verify_webhook(request: Request):
    p = request.query_params
    mode = (p.get("hub.mode") or "").strip()
    token = (p.get("hub.verify_token") or "").strip()
    challenge = p.get("hub.challenge")

    # ЛОГИ в Render -> Logs: увидишь, что реально пришло
    print("WEBHOOK VERIFY ->", {"mode": mode, "token": token, "challenge": challenge, "expect": VERIFY_TOKEN})

    if mode == "subscribe" and token == VERIFY_TOKEN and challenge:
        # вернуть РОВНО challenge как text/plain
        return PlainTextResponse(str(challenge))
    return PlainTextResponse("forbidden", status_code=403)

# Приём событий после подтверждения (Meta шлёт POST)
@app.post("/webhook")
async def webhook_event(request: Request):
    data = await request.json()
    print("📩 incoming:", data)  # смотри логи в Render → Logs
    return {"status": "ok"}
