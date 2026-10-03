"""
Firestore user-data store (ประวัติแชท + ข้อมูลต่อผู้ใช้)
-------------------------------------------------------
Database: rag-law-db (Firestore Native mode, project rag-law-509304)

- เก็บประวัติแชทแต่ละเทิร์นไว้ตรวจสอบ/โหลดกลับมาดูได้
- ทำงานแบบ optional: ถ้า GOOGLE_APPLICATION_CREDENTIALS ไม่ได้ตั้ง
  (รัน local ไม่มี auth) ทุกฟังก์ชันจะ no-op กลับเฉยๆ ไม่ raise

Cloud Run ต้องมีสิทธิ์ roles/datastore.user บน project (default SA มีอยู่แล้ว
ถ้าเปิด Firestore API แล้ว) + ระบุด้วย deploy flag:
    --set-env-vars "FIRESTORE_DB=rag-law-db,..."
"""
import os
import time

# --- Config ---
PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "rag-law-509304")
DATABASE_ID = os.environ.get("FIRESTORE_DB", "rag-law-db")
COLLECTION = os.environ.get("FIRESTORE_CHAT_COLLECTION", "chat_sessions")

_client = None  # lazy singleton
_available: bool | None = None


def _get_client():
    """คืน Firestore client หรือ None ถ้าใช้ไม่ได้ (ไม่มี auth / lib ไม่มี)"""
    global _client, _available
    if _available is False:
        return None
    if _client is None:
        try:
            from google.cloud import firestore  # noqa: F401

            _client = firestore.Client(
                project=PROJECT_ID, database=DATABASE_ID
            )
            _available = True
        except Exception as e:
            print(f"[firestore_db] unavailable (chat history will not persist): {e}")
            _available = False
            return None
    return _client


MAX_RECENT_TURNS = 10  # rolling window 10 เทิร์นล่าสุดเพื่อประหยัดพื้นที่และ context limit


def _clean_sources(sources: list | None) -> list[dict]:
    """บีบอัด sources เก็บเฉพาะ metadata จำเป็น ประหยัดพื้นที่จัดเก็บ"""
    if not sources:
        return []
    cleaned = []
    for s in sources[:3]:
        cleaned.append({
            "title": str(s.get("title", ""))[:80],
            "section": str(s.get("section", ""))[:40],
            "url": str(s.get("source_url", ""))[:120],
        })
    return cleaned


def save_turn(
    session_id: str,
    user_message: str,
    assistant_message: str,
    sources: list | None = None,
) -> None:
    """บันทึก 1 เทิร์นลงใน session document เดียว (1 Write Operation = ประหยัดที่สุด)"""
    client = _get_client()
    if client is None:
        return
    try:
        doc_ref = client.collection(COLLECTION).document(session_id)
        snap = doc_ref.get()
        data = snap.to_dict() if snap.exists else {}

        turns = data.get("turns", [])
        new_turn = {
            "user": user_message.strip(),
            "assistant": assistant_message.strip(),
            "sources": _clean_sources(sources),
            "created_at": time.time(),
        }
        turns.append(new_turn)

        # บีบอัดเก็บเฉพาะ Rolling Window ล่าสุด ป้องกันพื้นที่บวม
        if len(turns) > MAX_RECENT_TURNS:
            turns = turns[-MAX_RECENT_TURNS:]

        doc_ref.set(
            {
                "session_id": session_id,
                "turns": turns,
                "turn_count": (data.get("turn_count", 0) + 1),
                "updated_at": time.time(),
                "updated_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            },
            merge=True,
        )
    except Exception as e:
        print(f"[firestore_db] save_turn error: {e}")


def get_history(session_id: str, limit: int = 10) -> list[dict]:
    """โหลดประวัติแชทของ session (อ่านเพียง 1 document เท่านั้น = 1 Read Cost)"""
    client = _get_client()
    if client is None:
        return []
    try:
        doc_ref = client.collection(COLLECTION).document(session_id)
        snap = doc_ref.get()
        if not snap.exists:
            return []
        data = snap.to_dict() or {}
        turns = data.get("turns", [])
        return turns[-limit:]
    except Exception as e:
        print(f"[firestore_db] get_history error: {e}")
        return []


def reset_session(session_id: str) -> None:
    """ลบประวัติ session ใน 1 operation (1 Delete Cost)"""
    client = _get_client()
    if client is None:
        return
    try:
        client.collection(COLLECTION).document(session_id).delete()
    except Exception as e:
        print(f"[firestore_db] reset_session error: {e}")


def is_available() -> bool:
    """สำหรับ /health — แค่เช็คว่า client ใช้ได้ไหม"""
    return _get_client() is not None

