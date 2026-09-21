"""
LINE Messaging API Adapter
--------------------------
รับ webhook จาก LINE Platform → ตอบกลับผู้ใช้ด้วย Reply/Push API

ตั้งค่าใน LINE Developers Console (https://developers.line.biz/console/):
1. สร้าง Provider → Messaging API channel
2. Messaging settings → เปิดใช้ webhook
3. Webhook URL: https://<your-domain>/line/webhook
4. กด Verify (ต้องรัน server ก่อน) แล้ว disable "Use grouped responses"

ใช้ HTTP ตรงๆ (ไม่พึ่ง SDK เพิ่ม) — ลด dependency ให้เบาที่สุด
"""
import hashlib
import hmac
import json
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import httpx
import config
from runtime import ask, reset
from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse

line_router = APIRouter(prefix="/line", tags=["line"])

LINE_CHANNEL_ACCESS_TOKEN = (
    os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
    or os.environ.get("LINE_CHANNEL_ACCESS_TOKEN_LAW", "")
)
LINE_CHANNEL_SECRET = (
    os.environ.get("LINE_CHANNEL_SECRET")
    or os.environ.get("LINE_CHANNEL_SECRET_LAW", "")
)
MAX_TEXT = 4900  # LINE จำกัด ~5000 ตัวอักษรต่อข้อความ (เผื่อ margin)

REPLY_URL = "https://api.line.me/v2/bot/message/reply"
PUSH_URL = "https://api.line.me/v2/bot/message/push"


def signature_ok(body_bytes: bytes, signature: str) -> bool:
    """ตรวจ X-Line-Signature: HMAC-SHA256 ของ raw body ด้วย channel secret"""
    if not LINE_CHANNEL_SECRET:
        return False
    digest = hmac.new(
        LINE_CHANNEL_SECRET.encode(), body_bytes, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(digest, signature or "")


def split_message(text: str) -> list[str]:
    """แบ่งข้อความยาวเป็นหลายข้อความ (ตัดที่ขึ้นบรรทัดก่อนถ้าทำได้)"""
    if len(text) <= MAX_TEXT:
        return [text]
    parts = []
    while text:
        if len(text) <= MAX_TEXT:
            parts.append(text)
            break
        cut = text.rfind("\n", 0, MAX_TEXT)
        if cut < MAX_TEXT // 2:
            cut = MAX_TEXT
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return parts


def line_api(url: str, payload: dict):
    """เรียก LINE Messaging API หนึ่งครั้ง"""
    return httpx.post(
        url,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        },
        json=payload,
        timeout=15,
    )


def answer_text(session_id: str, text: str) -> str:
    """เรียก agent พร้อมจัดการคำสั่ง /reset และ fallback error"""
    if text.strip() in ("/reset", "/reset@lawbot", "ล้างแชท"):
        reset(session_id)
        return "🔄 เริ่มบทสนทนาใหม่แล้วครับ สอบถามกฎหมายได้เลยครับ"
    try:
        return ask(text, session_id)["answer"]
    except Exception as e:
        print(f"[line_bot] error: {e}")
        return "ขออภัยครับ เกิดข้อผิดพลาดในการประมวลผล กรุณาลองใหม่อีกครั้งครับ"


def make_reply_payload(reply_token: str, text: str) -> dict:
    parts = split_message(text)
    return {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": p} for p in parts[:5]],
    }


def make_push_payload(user_id: str, text: str) -> dict:
    parts = split_message(text)
    return {
        "to": user_id,
        "messages": [{"type": "text", "text": p} for p in parts[:5]],
    }


def send_reply(reply_token: str, user_id: str, text: str) -> None:
    """ตอบผู้ใช้: ลอง Reply API ก่อน (ฟรี ไม่นับ quota)
    ถ้า token หมดอายุ/ใช้แล้ว → fallback ไป Push API (นับ quota)
    """
    try:
        resp = line_api(REPLY_URL, make_reply_payload(reply_token, text))
        if resp.status_code == 200:
            return
        # invalid reply token = 400 "The reply token is invalid" / 429 throttled
        print(f"[line_bot] reply failed ({resp.status_code}): {resp.text[:300]}")
        if resp.status_code == 400 and user_id:
            print("[line_bot] falling back to Push API (counts toward quota)")
            line_api(PUSH_URL, make_push_payload(user_id, text))
    except Exception as e:
        print(f"[line_bot] send failed: {e}")


def process_event(reply_token: str, user_id: str, text: str) -> None:
    """ทำงานใน background — ประมวลผล LLM แล้วส่งคำตอบกลับ"""
    session_id = f"line:{user_id}"
    answer = answer_text(session_id, text)
    send_reply(reply_token, user_id, answer)


@line_router.post("/webhook")
async def line_webhook(request: Request, background_tasks: BackgroundTasks):
    body_bytes = await request.body()

    # ตรวจ signature (บังคับเมื่อตั้งค่า secret แล้ว)
    sig = request.headers.get("x-line-signature", "")
    if LINE_CHANNEL_SECRET and not signature_ok(body_bytes, sig):
        return JSONResponse({"error": "invalid signature"}, status_code=403)

    try:
        payload = json.loads(body_bytes or b"{}")
    except Exception:
        return JSONResponse({"error": "bad json"}, status_code=400)

    # สำคัญ: LINE webhook timeout = 2 วินาที — ต้อง return 200 ทันที
    # แล้วค่อยประมวลผล LLM (5-30 วิ) ใน background
    for event in payload.get("events", []):
        if event.get("type") not in ("message", "postback"):
            continue
        reply_token = event.get("replyToken")
        if not reply_token:
            continue

        # ข้อความจากผู้ใช้
        msg = event.get("message", {})
        text = msg.get("text", "") if event.get("type") == "message" else event.get("postback", {}).get("data", "")
        if not text:
            text = "ขออภัยครับ ผมรองรับข้อความตัวอักษรเท่านั้นครับ"

        user_id = event.get("source", {}).get("userId", "unknown")
        background_tasks.add_task(process_event, reply_token, user_id, text)

    # LINE ต้องได้ 200 เสมอ ไม่งั้นจะ retry
    return JSONResponse({"ok": True})


@line_router.get("/health")
def line_health():
    import db_sync
    return {
        "ok": True,
        "configured": bool(LINE_CHANNEL_ACCESS_TOKEN and LINE_CHANNEL_SECRET),
        "db_ready": db_sync.is_ready(),
    }


if __name__ == "__main__":
    # สำหรับรันแยก: uvicorn src.line_bot:app (แต่ปกติ mount ใน server.py)
    import uvicorn
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(line_router)
    uvicorn.run(app, host=config.API_HOST, port=config.API_PORT)
