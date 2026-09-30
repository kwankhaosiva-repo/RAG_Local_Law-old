"""
FastAPI Web Chat Server for Thai Legal RAG
- POST /chat    : {session_id, message} -> {answer, sources}
- POST /reset   : ล้างประวัติห้องแชท
- GET  /        : หน้าเว็บแชท
- POST /line/webhook     : LINE Messaging API (ดู line_bot.py)
- POST /openclaw/webhook : จุดเชื่อม OpenClaw gateway (ดู openclaw_bridge.py)
- GET  /bot-avatar.jpg   : รูปโปรไฟล์บอทตั้งต้น (ใส่ไฟล์ src/web/bot-avatar.jpg เพื่อให้ทุกคนเห็น)

Discord รันแยก process: python src/discord_bot.py
"""
import warnings

warnings.filterwarnings("ignore")

import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import config
import runtime
import db_sync
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

app = FastAPI(title="Thai Legal RAG Chatbot")


class ChatRequest(BaseModel):
    session_id: str = "default"
    message: str


class ChatResponse(BaseModel):
    answer: str
    sources: list = []


@app.get("/health")
def health():
    return {"ok": True, "db_ready": db_sync.is_ready()}


@app.get("/")
def index():
    return FileResponse(os.path.join(os.path.dirname(__file__), "web", "index.html"))


# รูปโปรไฟล์บอทตั้งต้น — เจอไฟล์ไหนใช้ไฟล์นั้น (URL เดิมเสมอ /bot-avatar.jpg)
BOT_AVATAR_FILES = (
    ("bot-avatar.jpg", "image/jpeg"),
    ("bot-avatar.jpeg", "image/jpeg"),
    ("bot-avatar.png", "image/png"),
    ("bot-avatar.webp", "image/webp"),
)


@app.get("/bot-avatar.jpg", include_in_schema=False)
def bot_avatar():
    """รูปโปรไฟล์ตั้งต้นของบอท — วางไฟล์ src/web/bot-avatar.jpg (หรือ .png/.webp) แล้วทุกคนเห็นรูปนี้
    (ผู้ใช้ที่อัปโหลดรูปเองในหน้าตั้งค่าจะใช้รูปของตัวเองทับเฉพาะเบราว์เซอร์นั้น)
    """
    web_dir = os.path.join(os.path.dirname(__file__), "web")
    for fname, media_type in BOT_AVATAR_FILES:
        path = os.path.join(web_dir, fname)
        if os.path.exists(path):
            return FileResponse(
                path, media_type=media_type, headers={"Cache-Control": "no-cache"}
            )
    return JSONResponse(
        {"ok": False, "hint": "วางไฟล์รูปที่ src/web/bot-avatar.jpg"},
        status_code=404,
        headers={"Cache-Control": "no-store"},
    )


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    try:
        out = runtime.ask(req.message.strip(), req.session_id.strip())
        return ChatResponse(answer=out["answer"], sources=out["sources"])
    except Exception as e:
        return ChatResponse(
            answer=f"เกิดข้อผิดพลาดในการประมวลผล: {e}", sources=[]
        )


@app.post("/reset")
def reset(req: ChatRequest):
    runtime.reset(req.session_id.strip())
    return {"ok": True}


@app.on_event("startup")
def startup():
    # ไม่ block การ bind PORT — โหลด chroma_db จาก GCS เป็น background thread
    db_sync.start_background_sync()


# --- Optional channel routers (mount ถ้ามีไฟล์) ---
try:
    from line_bot import line_router

    app.include_router(line_router)
    print("LINE bot mounted at /line/webhook")
except ImportError:
    pass

try:
    from openclaw_bridge import openclaw_router

    app.include_router(openclaw_router)
    print("OpenClaw bridge mounted at", config.OPENCLAW_WEBHOOK_PATH)
except ImportError:
    pass


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=config.API_HOST, port=config.API_PORT)
