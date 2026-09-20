"""
OpenClaw Gateway Bridge
-----------------------
OpenClaw (https://github.com/openclaw/openclaw) เป็น open-source agent gateway
ที่เชื่อมช่องทางแชท (LINE, Telegram, WhatsApp, Discord, Slack ฯลฯ) เข้ากับ agent ในเครื่อง

วิธีเชื่อม:
1. รัน server นี้:  python src/server.py  (หรือ uvicorn src.server:app --port 8000)
2. ใน config ของ OpenClaw gateway ให้ชี้ webhook ไปที่:
      POST http://<host>:8000/openclaw/webhook
   โดยส่ง JSON: { "chat_id": "...", "text": "..." }
3. (ถ้าตั้ง env OPENCLAW_SECRET) ใส่ header: X-OpenClaw-Secret: <secret>
4. Gateway จะได้รับ: { "reply": "...", "sources": [...] }

ปรับรูปแบบ payload ให้ตรงกับ plugin/adapter ฝั่ง OpenClaw ของคุณได้ที่ฟังก์ชัน handle_openclaw_message
"""
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import config
import runtime
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

openclaw_router = APIRouter()


class OpenClawMessage(BaseModel):
    chat_id: str
    text: str


class OpenClawReply(BaseModel):
    reply: str
    sources: list = []


def handle_openclaw_message(chat_id: str, text: str) -> dict:
    """จุดเดียวที่ปรับ format ได้ตาม adapter ฝั่ง OpenClaw"""
    session_id = f"openclaw-{chat_id}"
    out = runtime.ask(text.strip(), session_id)
    reply = out["answer"]
    if out["sources"]:
        refs = "\n".join(
            f"📄 {s.get('title', '')} {s.get('section', '')}".strip()
            for s in out["sources"][:3]
        )
        reply = f"{reply}\n\n— อ้างอิง —\n{refs}"
    return {"reply": reply, "sources": out["sources"]}


@openclaw_router.post(config.OPENCLAW_WEBHOOK_PATH, response_model=OpenClawReply)
def openclaw_webhook(
    msg: OpenClawMessage,
    x_openclaw_secret: str = Header(default=""),
):
    # ตรวจ shared secret (ถ้าตั้งไว้)
    if config.OPENCLAW_SECRET and x_openclaw_secret != config.OPENCLAW_SECRET:
        raise HTTPException(status_code=401, detail="Invalid secret")

    if not msg.text.strip():
        return OpenClawReply(reply="กรุณาพิมพ์คำถามกฎหมายที่ต้องการสอบถามครับ")

    try:
        result = handle_openclaw_message(msg.chat_id, msg.text)
        return OpenClawReply(**result)
    except Exception as e:
        return OpenClawReply(reply=f"เกิดข้อผิดพลาดในการประมวลผล: {e}")
