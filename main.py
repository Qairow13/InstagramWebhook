from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
import os

app = FastAPI()

# Маркер, который ты введёшь в Meta → Webhooks
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "apiapimeta")

@app.get("/")
def home():
    return {"ok": True, "service": "IG webhook"}

# Проверка вебхука (Meta делает GET сюда)
@app.get("/webhook")
async def verify_webhook(request: Request):
    p = request.query_params
    mode = p.get("hub.mode")
    token = p.get("hub.verify_token")
    challenge = p.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN and challenge:
        # вернуть РОВНО challenge как text/plain
        return PlainTextResponse(challenge)
    return PlainTextResponse("forbidden", status_code=403)

# Приём событий после подтверждения (Meta шлёт POST)
@app.post("/webhook")
async def webhook_event(request: Request):
    data = await request.json()
    print("📩 incoming:", data)  # смотри логи в Render → Logs
    return {"status": "ok"}
