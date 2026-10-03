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
import threading
import time

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import httpx
import config
from runtime import ask, reset
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

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
BOT_INFO_URL = "https://api.line.me/v2/bot/info"
LOADING_URL = "https://api.line.me/v2/bot/chat/loading/start"

# เตือนตั้งแต่ตอน import — อาการ "webhook 200 แต่บอทเงียบ" เกิดจากค่าเหล่านี้ว่างบ่อยที่สุด
if not (LINE_CHANNEL_ACCESS_TOKEN or "").strip():
    print("[line_bot] ⚠️ LINE_CHANNEL_ACCESS_TOKEN ว่าง — รับ webhook ได้แต่ตอบกลับไม่ได้")
if not (LINE_CHANNEL_SECRET or "").strip():
    print("[line_bot] ⚠️ LINE_CHANNEL_SECRET ว่าง — ข้ามการตรวจ signature (ไม่ปลอดภัย)")


def _token_tail() -> str:
    """ท้าย token 4 ตัว — ยืนยันว่าโหลด token ตัวไหนโดยไม่เปิดเผยค่าจริง"""
    t = (LINE_CHANNEL_ACCESS_TOKEN or "").strip()
    return f"...{t[-4:]}" if len(t) >= 4 else "(ว่าง)"


def signature_ok(body_bytes: bytes, signature: str) -> bool:
    """ตรวจ X-Line-Signature: HMAC-SHA256 แบบ Base64 ของ raw body ด้วย channel secret"""
    secret = (LINE_CHANNEL_SECRET or "").strip()
    if not secret:
        return False
    import base64
    sig = (signature or "").strip()
    digest = hmac.new(
        secret.encode("utf-8"), body_bytes, hashlib.sha256
    ).digest()
    computed = base64.b64encode(digest).decode("utf-8")
    is_valid = hmac.compare_digest(computed, sig)
    if not is_valid:
        print(f"[line_bot] Signature mismatch! header_len={len(sig)}, computed_len={len(computed)}, secret_len={len(secret)}")
    return is_valid


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
    print("[line_bot] เรียก runtime.ask ...")
    try:
        out = ask(text, session_id)
        print("[line_bot] runtime.ask สำเร็จ")
        return out["answer"]
    except Exception as e:
        print(f"[line_bot] error: {e}")
        return "ขออภัยครับ เกิดข้อผิดพลาดในการประมวลผล กรุณาลองใหม่อีกครั้งครับ"


def clean_markdown_tables_for_line(text: str) -> str:
    """แปลงตาราง Markdown (| col | col |) ให้เป็นข้อความแบบ Bullet สำหรับแสดงผลบน LINE สวยงาม"""
    import re
    lines = text.split("\n")
    cleaned_lines = []
    in_table = False
    headers = []
    
    for line in lines:
        stripped = line.strip()
        # ตรวจว่าเป็นเส้นแบ่งตาราง เช่น |---|---|
        if re.match(r"^\|?\s*[-:]+\s*\|[-:\|\s]*$", stripped):
            in_table = True
            continue
        # แถวตาราง | col1 | col2 |
        if stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 3:
            cols = [c.strip() for c in stripped.strip("|").split("|")]
            if not in_table and not headers:
                headers = cols
                continue
            in_table = True
            if headers and len(headers) == len(cols):
                card_items = [f"{h}: {c}" for h, c in zip(headers, cols) if c]
                cleaned_lines.append("• " + " | ".join(card_items))
            else:
                cleaned_lines.append("• " + " - ".join(c for c in cols if c))
            continue
        else:
            in_table = False
            headers = []
            cleaned_lines.append(line)
            
    return "\n".join(cleaned_lines)


def make_quick_reply() -> dict:
    """สร้างปุ่ม Quick Reply ลอยเหนือคีย์บอร์ดบน LINE (สำหรับขอรายละเอียดเชิงลึก หรือเริ่มเรื่องใหม่)"""
    return {
        "items": [
            {
                "type": "action",
                "action": {
                    "type": "message",
                    "label": "📖 รายละเอียดเชิงลึก",
                    "text": "ขอรายละเอียดเชิงลึกและตัวบทกฎหมายเพิ่มเติม",
                },
            },
            {
                "type": "action",
                "action": {
                    "type": "message",
                    "label": "⚖️ ขั้นตอนทางคดี",
                    "text": "ขั้นตอนการแจ้งความหรือดำเนินคดีต้องทำอย่างไรบ้าง",
                },
            },
            {
                "type": "action",
                "action": {
                    "type": "message",
                    "label": "🔄 ถามเรื่องใหม่",
                    "text": "/reset",
                },
            },
        ]
    }


def make_reply_payload(reply_token: str, text: str) -> dict:
    clean_text = clean_markdown_tables_for_line(text)
    parts = split_message(clean_text)
    messages = [{"type": "text", "text": p} for p in parts[:5]]
    if messages:
        messages[-1]["quickReply"] = make_quick_reply()
    return {
        "replyToken": reply_token,
        "messages": messages,
    }


def make_push_payload(user_id: str, text: str) -> dict:
    clean_text = clean_markdown_tables_for_line(text)
    parts = split_message(clean_text)
    messages = [{"type": "text", "text": p} for p in parts[:5]]
    if messages:
        messages[-1]["quickReply"] = make_quick_reply()
    return {
        "to": user_id,
        "messages": messages,
    }


def send_reply(reply_token: str, user_id: str, text: str) -> bool:
    """ตอบผู้ใช้: ลอง Reply API ก่อน (ฟรี ไม่นับ quota)
    ถ้าใช้ไม่ได้ (token หมดอายุ/ถูกใช้แล้ว/401/403) → fallback ไป Push API (นับ quota)
    คืน True ถ้าส่งสำเร็จอย่างน้อยหนึ่งช่องทาง
    """
    if not (LINE_CHANNEL_ACCESS_TOKEN or "").strip():
        print("[line_bot] ❌ LINE_CHANNEL_ACCESS_TOKEN ว่าง — ส่ง reply ไม่ได้")
        return False

    try:
        resp = line_api(REPLY_URL, make_reply_payload(reply_token, text))
        if resp.status_code == 200:
            print(f"[line_bot] ✅ reply ok (token {_token_tail()})")
            return True
        # invalid reply token = 400 "The reply token is invalid" / 401 token ผิด / 429 throttled
        print(f"[line_bot] reply failed ({resp.status_code}): {resp.text[:300]}")
    except Exception as e:
        print(f"[line_bot] reply exception: {e}")

    # fallback: Push API (ต้องมี userId)
    if not user_id or user_id == "unknown":
        print("[line_bot] ไม่มี userId → ข้าม Push fallback")
        return False
    try:
        print("[line_bot] falling back to Push API (counts toward quota)")
        presp = line_api(PUSH_URL, make_push_payload(user_id, text))
        if presp.status_code == 200:
            print("[line_bot] ✅ push ok")
            return True
        print(f"[line_bot] push failed ({presp.status_code}): {presp.text[:300]}")
    except Exception as e:
        print(f"[line_bot] push exception: {e}")
    return False


def show_loading(chat_id: str, seconds: int = 20) -> None:
    """แสดงแอนิเมชัน "กำลังพิมพ์..." ให้ผู้ใช้เห็นทันทีระหว่างประมวลผล
    ฟรี ไม่นับโควตาข้อความ (best-effort — ถ้าล้มเหลวไม่กระทบการตอบ)
    """
    if not chat_id or not (LINE_CHANNEL_ACCESS_TOKEN or "").strip():
        return
    try:
        resp = line_api(LOADING_URL, {"chatId": chat_id, "loadingSeconds": seconds})
        if resp.status_code != 200:
            print(f"[line_bot] loading indicator failed ({resp.status_code}): {resp.text[:200]}")
    except Exception as e:
        print(f"[line_bot] loading indicator exception: {e}")


def process_event(reply_token: str, user_id: str, text: str, chat_id: str = "") -> None:
    """ประมวลผล LLM แล้วส่งคำตอบกลับ — เรียก *ภายใน request* ของ webhook

    ⚠️ ห้ามย้ายไปทำหลัง response (BackgroundTasks/thread ที่ปล่อยให้ request จบก่อน)
    เพราะ Cloud Run โหมดประหยัด (--cpu-throttling, default) จะให้ CPU "เฉพาะตอน
    มี request ค้างอยู่" — งานที่ทำหลังตอบ webhook จะถูกอด CPU → บอทเงียบ
    """
    t0 = time.time()
    session_id = f"line:{user_id}"
    print(f"[line_bot] ▶ process_event user={user_id} len={len(text)} text={text[:60]!r}")
    show_loading(chat_id or user_id)
    try:
        answer = answer_text(session_id, text)
    except Exception as e:
        print(f"[line_bot] ❌ answer_text หลุด exception: {e}")
        answer = "ขออภัยครับ เกิดข้อผิดพลาดในการประมวลผล กรุณาลองใหม่อีกครั้งครับ"
    print(f"[line_bot] ⏱ agent ใช้เวลา {time.time() - t0:.1f}s → กำลังส่ง reply")
    ok = send_reply(reply_token, user_id, answer)
    print(f"[line_bot] ■ process_event จบ ok={ok} รวม {time.time() - t0:.1f}s")


# --- กัน LINE retry ซ้ำ (LINE ส่ง event เดิมซ้ำเมื่อ webhook ตอบช้า) ---
_seen_ids: dict[str, float] = {}
_seen_lock = threading.Lock()
_SEEN_TTL = 600  # เก็บ eventId 10 นาที


def _already_seen(event_id: str) -> bool:
    if not event_id:
        return False
    now = time.time()
    with _seen_lock:
        for k, ts in list(_seen_ids.items()):
            if now - ts > _SEEN_TTL:
                del _seen_ids[k]
        if event_id in _seen_ids:
            return True
        _seen_ids[event_id] = now
        return False


@line_router.post("/webhook")
async def line_webhook(request: Request):
    body_bytes = await request.body()

    # ตรวจ signature (บังคับเมื่อตั้งค่า secret แล้ว)
    sig = request.headers.get("x-line-signature", "")
    if LINE_CHANNEL_SECRET and not signature_ok(body_bytes, sig):
        return JSONResponse({"error": "invalid signature"}, status_code=403)

    try:
        payload = json.loads(body_bytes or b"{}")
    except Exception:
        return JSONResponse({"error": "bad json"}, status_code=400)

    # ประมวลผล "ภายใน request นี้" (ไม่ใช่หลัง response)
    # เหตุผล: Cloud Run default = --cpu-throttling → ให้ CPU เฉพาะตอนมี request ค้างอยู่
    #         ถ้าตอบ 200 แล้วค่อยทำต่อ (BackgroundTasks/thread) งานจะถูกอด CPU → บอทเงียบ
    # ต้นทุน: webhook ตอบช้า (วินาที) — LINE อาจส่ง event เดิมซ้ำ จึงกันด้วย webhookEventId
    #        และใช้ run_in_threadpool เพื่อไม่ให้บล็อก event loop ของ server
    events = payload.get("events", [])
    print(f"[line_bot] webhook events={len(events)}")
    for event in events:
        etype = event.get("type")
        if etype not in ("message", "postback"):
            print(f"[line_bot] ข้าม event type={etype}")
            continue
        if _already_seen(event.get("webhookEventId", "")):
            print("[line_bot] ข้าม event ซ้ำ (retry)")
            continue
        reply_token = event.get("replyToken")
        if not reply_token:
            print("[line_bot] ไม่มี replyToken — ข้าม")
            continue

        # ข้อความจากผู้ใช้
        msg = event.get("message", {})
        text = msg.get("text", "") if etype == "message" else event.get("postback", {}).get("data", "")
        if not text:
            text = "ขออภัยครับ ผมรองรับข้อความตัวอักษรเท่านั้นครับ"

        source = event.get("source", {})
        user_id = source.get("userId", "unknown")
        chat_id = (
            source.get("userId")
            or source.get("groupId")
            or source.get("roomId")
            or ""
        )
        print(f"[line_bot] ประมวลผลใน request: user={user_id} text={text[:60]!r}")
        try:
            await run_in_threadpool(process_event, reply_token, user_id, text, chat_id)
        except Exception as e:
            # ต้องไม่ทำให้ webhook ล้ม — LINE ต้องได้ 200 เสมอ
            print(f"[line_bot] ❌ process_event ล้มเหลว: {e}")

    return JSONResponse({"ok": True})


@line_router.get("/health")
def line_health():
    import db_sync
    token = (LINE_CHANNEL_ACCESS_TOKEN or "").strip()
    secret = (LINE_CHANNEL_SECRET or "").strip()
    return {
        "ok": True,
        "configured": bool(token and secret),
        "token_set": bool(token),
        "secret_set": bool(secret),
        "token_len": len(token),
        "secret_len": len(secret),
        "token_tail": _token_tail(),
        "db_ready": db_sync.is_ready(),
    }


@line_router.get("/selftest")
def line_selftest():
    """ตรวจว่า access token ใช้ได้จริงไหม — เรียก GET /v2/bot/info ของ LINE
    เปิดจากเบราว์เซอร์ได้เลย: https://<cloud-run-url>/line/selftest
    """
    token = (LINE_CHANNEL_ACCESS_TOKEN or "").strip()
    if not token:
        return JSONResponse({"ok": False, "error": "LINE_CHANNEL_ACCESS_TOKEN ว่าง"})
    try:
        resp = httpx.get(
            BOT_INFO_URL,
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"เรียก LINE ไม่ได้: {e}"})
    return JSONResponse(
        {
            "ok": resp.status_code == 200,
            "status": resp.status_code,
            "token_tail": _token_tail(),
            "body": resp.text[:500],
        }
    )


if __name__ == "__main__":
    # สำหรับรันแยก: uvicorn src.line_bot:app (แต่ปกติ mount ใน server.py)
    import uvicorn
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(line_router)
    uvicorn.run(app, host=config.API_HOST, port=config.API_PORT)
