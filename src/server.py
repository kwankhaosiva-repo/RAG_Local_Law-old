"""
FastAPI Web Chat Server for Thai Legal RAG
- POST /chat    : {session_id, message} -> {answer, sources}
- POST /reset   : ล้างประวัติห้องแชท
- GET  /        : หน้าเว็บแชท
- GET  /presentation    : หน้า presentation/brief (docs/presentation.html)
- POST /line/webhook     : LINE Messaging API (ดู line_bot.py)
- POST /openclaw/webhook : จุดเชื่อม OpenClaw gateway (ดู openclaw_bridge.py)
- GET  /bot-avatar.jpg   : รูปโปรไฟล์บอทตั้งต้น (ใส่ไฟล์ src/web/bot-avatar.jpg เพื่อให้ทุกคนเห็น)

Discord รันแยก process: python src/discord_bot.py
"""
import warnings

warnings.filterwarnings("ignore")

import os
import sys
import threading
import time

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


@app.get("/ping", include_in_schema=False)
def ping():
    """ตอบเร็ว ไม่แตะ DB/โมเดล — ใช้ปลุก instance ก่อนที่ผู้ใช้จะส่งข้อความ LINE จริง"""
    return {"ok": True, "ts": time.time()}


@app.get("/")
def index():
    return FileResponse(os.path.join(os.path.dirname(__file__), "web", "index.html"))


# หน้า presentation/brief HTML — ไฟล์ต้นฉบับอยู่ที่ docs/presentation.html (นอก src/)
_PRESENTATION_HTML = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "docs",
    "presentation.html",
)


def _presentation():
    if os.path.exists(_PRESENTATION_HTML):
        return FileResponse(
            _PRESENTATION_HTML,
            media_type="text/html; charset=utf-8",
            headers={"Cache-Control": "no-cache"},
        )
    return JSONResponse(
        {"ok": False, "hint": "docs/presentation.html ไม่ถูก COPY เข้า image"},
        status_code=404,
        headers={"Cache-Control": "no-store"},
    )


@app.get("/presentation", include_in_schema=False)
def presentation():
    """หน้า presentation/brief (HTML เดียวกับ docs/presentation.html)"""
    return _presentation()


@app.get("/brief", include_in_schema=False)
def brief():
    """alias สั้น ๆ ของ /presentation"""
    return _presentation()


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


def _preload_model() -> None:
    """โหลด embedding model ล่วงหน้าตั้งแต่ตอน startup (background thread)

    ทำไม: cold start ที่ต้องโหลดโมเดล (~470MB) ใช้เวลา ~1-2 นาที — นานกว่า
    reply token ของ LINE (อายุ ~1 นาที) ทำให้ข้อความแรกของผู้ใช้ตอบไม่ทัน
    โหลดล่วงหน้าทำให้ instance พร้อมตอบเร็วเมื่อมีข้อความเข้ามา
    """
    try:
        print("[preload] กำลังโหลด embedding model ...")
        t0 = time.time()
        runtime.get_agent()
        print(f"[preload] โหลดเสร็จใน {time.time() - t0:.1f}s")
    except Exception as e:
        print(f"[preload] ล้มเหลว (จะ lazy-load ตอนมีคำถามแทน): {e}")


@app.on_event("startup")
def startup():
    # ไม่ block การ bind PORT — sync DB + โหลดโมเดล เป็น background thread ทั้งคู่
    db_sync.start_background_sync()
    threading.Thread(target=_preload_model, name="model-preload", daemon=True).start()


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

    # หมายเหตุ: รัน local ผ่าน uvicorn จะได้ startup event ตามปกติ
    # บน Cloud Run ใช้ CMD ใน Dockerfile: uvicorn src.server:app ...
    uvicorn.run(app, host=config.API_HOST, port=config.API_PORT)
