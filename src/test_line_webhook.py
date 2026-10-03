"""
Test: LINE webhook handler (src/line_bot.py)

ครอบคลุมบั๊ก "webhook ตอบ 200 แต่บอทไม่ตอบ":
  1. ตรวจ signature ถูก/ผิด (403 เมื่อ secret ไม่ตรง)
  2. webhook ตอบ 200 และตอบกลับผู้ใช้
  3. งานตอบกลับต้องทำ "ภายใน request" ของ webhook — ห้ามย้ายไปหลัง response
     (BackgroundTasks/thread ที่ปล่อยให้ request จบก่อน) เพราะ Cloud Run โหมด
     ประหยัด (--cpu-throttling, default) ตัด CPU หลังตอบ request → บอทเงียบ
  4. event เดิมที่ LINE ส่งซ้ำ (retry) ถูกข้าม ไม่ตอบซ้ำ
  5. send_reply คืน False เมื่อไม่มี access token (แทนที่จะเงียบ)

รัน:  cd src && python test_line_webhook.py
"""
import base64
import hashlib
import hmac
import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ต้องตั้งค่าก่อน import line_bot (ค่าถูกอ่านตอน import)
SECRET = "test-channel-secret"
os.environ["LINE_CHANNEL_SECRET"] = SECRET
os.environ["LINE_CHANNEL_ACCESS_TOKEN"] = "test-access-token-1234"

import line_bot  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def _sign(body: bytes) -> str:
    return base64.b64encode(
        hmac.new(SECRET.encode(), body, hashlib.sha256).digest()
    ).decode()


def _payload(event_id: str) -> dict:
    return {
        "destination": "Uxxxx",
        "events": [
            {
                "type": "message",
                "webhookEventId": event_id,
                "replyToken": "reply-token-abc",
                "source": {"type": "user", "userId": "Uuser1"},
                "message": {"type": "text", "id": "1", "text": "มาตรา 15 คืออะไร"},
            }
        ],
    }


def test_signature():
    print("=" * 60)
    print("A) ตรวจ X-Line-Signature")
    print("=" * 60)
    body = b'{"events":[]}'
    assert line_bot.signature_ok(body, _sign(body)) is True, "signature ที่ถูกต้องต้องผ่าน"
    assert line_bot.signature_ok(body, "wrong") is False, "signature ผิดต้องไม่ผ่าน"
    assert line_bot.signature_ok(body, "") is False, "ไม่มี signature ต้องไม่ผ่าน"
    print("  [PASS] signature ถูก/ผิด/ว่าง ทำงานถูกต้อง")


def test_dedup():
    print()
    print("=" * 60)
    print("B) กัน event ซ้ำ (LINE retry)")
    print("=" * 60)
    eid = "evt-dedup-1"
    assert line_bot._already_seen(eid) is False, "ครั้งแรกต้องไม่ถือว่าซ้ำ"
    assert line_bot._already_seen(eid) is True, "ครั้งที่สองต้องถือว่าซ้ำ"
    assert line_bot._already_seen("") is False, "eventId ว่างไม่ควรถูก dedup"
    print("  [PASS] eventId เดิมถูกมองว่าซ้ำ, ว่างไม่ถูก dedup")


def test_webhook_replies_inside_request_and_dedups():
    print()
    print("=" * 60)
    print("C) webhook 200 + ตอบภายใน request + ข้าม retry")
    print("=" * 60)

    calls: list[tuple] = []

    def fake_process_event(reply_token, user_id, text, chat_id=""):
        calls.append((reply_token, user_id, text, chat_id))

    line_bot.process_event = fake_process_event  # monkeypatch

    app = FastAPI()
    app.include_router(line_bot.line_router)
    client = TestClient(app)

    body = json.dumps(_payload("evt-dispatch-1")).encode()
    resp = client.post(
        "/line/webhook", content=body, headers={"X-Line-Signature": _sign(body)}
    )
    assert resp.status_code == 200, f"ต้องตอบ 200 แต่ได้ {resp.status_code}"
    # สำคัญ: process_event ต้องทำงาน *เสร็จก่อน* response กลับ
    assert len(calls) == 1, f"ต้องเรียก process_event 1 ครั้ง แต่ได้ {len(calls)}"
    assert calls[0][1] == "Uuser1", f"args ผิด: {calls}"
    assert calls[0][2] == "มาตรา 15 คืออะไร", f"ข้อความผิด: {calls[0][2]}"
    assert calls[0][3] == "Uuser1", f"chat_id ผิด: {calls[0][3]}"
    print(f"  [PASS] ตอบ 200 และประมวลผลเสร็จใน request: {calls[0][:2]}")

    # ส่ง event เดิมซ้ำ (จำลอง LINE retry) → ต้องไม่เรียก process_event อีก
    resp2 = client.post(
        "/line/webhook", content=body, headers={"X-Line-Signature": _sign(body)}
    )
    assert resp2.status_code == 200
    time.sleep(0.2)
    assert len(calls) == 1, f"event ซ้ำต้องถูกข้าม แต่ถูกเรียก {len(calls)} ครั้ง"
    print("  [PASS] event ซ้ำถูกข้าม (ไม่ตอบซ้ำ)")

    # signature ผิด → 403
    bad = client.post(
        "/line/webhook", content=body, headers={"X-Line-Signature": "bad"}
    )
    assert bad.status_code == 403, f"signature ผิดต้องได้ 403 แต่ได้ {bad.status_code}"
    print("  [PASS] signature ผิด → 403")

    # handler ต้องไม่ dispatch งานออกไปนอก request (จะถูก Cloud Run ตัด CPU)
    import inspect

    src = inspect.getsource(line_bot.line_webhook)
    params = inspect.signature(line_bot.line_webhook).parameters
    assert "background_tasks" not in params, (
        "ห้ามรับ BackgroundTasks (งานหลัง response ถูก Cloud Run ตัด CPU)"
    )
    assert "threading.Thread" not in src, (
        "ห้าม spawn thread แล้วปล่อยให้ request จบก่อน — CPU จะถูกตัดหลัง response"
    )
    assert "run_in_threadpool" in src, "ต้องรอผลภายใน request (run_in_threadpool)"
    print("  [PASS] ประมวลผลภายใน request ไม่ใช่หลัง response")


def test_send_reply_without_token():
    print()
    print("=" * 60)
    print("D) send_reply เมื่อไม่มี access token")
    print("=" * 60)
    original = line_bot.LINE_CHANNEL_ACCESS_TOKEN
    line_bot.LINE_CHANNEL_ACCESS_TOKEN = ""
    try:
        ok = line_bot.send_reply("tok", "Uuser1", "hello")
        assert ok is False, "ไม่มี token ต้องคืน False"
        print("  [PASS] คืน False แทนที่จะเงียบ")
    finally:
        line_bot.LINE_CHANNEL_ACCESS_TOKEN = original


def test_loading_indicator():
    print()
    print("=" * 60)
    print("E) loading indicator (แอนิเมชันกำลังพิมพ์)")
    print("=" * 60)
    calls: list[tuple] = []

    def fake_line_api(url, payload):
        calls.append((url, payload))

        class _R:
            status_code = 200
            text = ""

        return _R()

    original = line_bot.line_api
    line_bot.line_api = fake_line_api
    try:
        line_bot.show_loading("Uuser1", 20)
        assert len(calls) == 1, f"ต้องเรียก loading 1 ครั้ง แต่ได้ {len(calls)}"
        assert calls[0][0] == line_bot.LOADING_URL, f"URL ผิด: {calls[0][0]}"
        assert calls[0][1] == {"chatId": "Uuser1", "loadingSeconds": 20}, calls[0][1]
        # chat_id ว่าง → ต้องไม่เรียก (best-effort)
        calls.clear()
        line_bot.show_loading("")
        assert calls == [], "chat_id ว่างต้องไม่เรียก loading"
        print("  [PASS] ส่ง loading indicator ถูกต้อง + ข้ามเมื่อไม่มี chat_id")
    finally:
        line_bot.line_api = original


if __name__ == "__main__":
    test_signature()
    test_dedup()
    test_webhook_replies_inside_request_and_dedups()
    test_send_reply_without_token()
    test_loading_indicator()
    print("\n" + "=" * 60)
    print("ALL LINE WEBHOOK TESTS PASSED ✅")
    print("=" * 60)
