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


def save_turn(
    session_id: str,
    user_message: str,
    assistant_message: str,
    sources: list | None = None,
) -> None:
    """บันทึก 1 เทิร์นของบทสนทนา (1 document ต่อเทิร์น ใต้ collection ของ session)"""
    client = _get_client()
    if client is None:
        return
    ref = (
        client.collection(COLLECTION)
        .document(session_id)
        .collection("turns")
        .document()
    )
    ref.set(
        {
            "user_message": user_message,
            "assistant_message": assistant_message,
            "sources": sources or [],
            "created_at": time.time(),
            "created_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
    )


def get_history(session_id: str, limit: int = 50) -> list[dict]:
    """โหลดประวัติแชทของ session (เก่าสุดก่อน)"""
    client = _get_client()
    if client is None:
        return []
    docs = (
        client.collection(COLLECTION)
        .document(session_id)
        .collection("turns")
        .order_by("created_at")
        .limit(limit)
        .stream()
    )
    return [d.to_dict() for d in docs]


def reset_session(session_id: str) -> None:
    """ลบประวัติทั้งหมดของ session (ตอน user กด reset)"""
    client = _get_client()
    if client is None:
        return
    turns = (
        client.collection(COLLECTION)
        .document(session_id)
        .collection("turns")
        .stream()
    )
    batch = client.batch()
    for i, doc in enumerate(turns):
        batch.delete(doc.reference)
        if (i + 1) % 400 == 0:
            batch.commit()
            batch = client.batch()
    batch.commit()


def is_available() -> bool:
    """สำหรับ /health — แค่เช็คว่า client ใช้ได้ไหม"""
    return _get_client() is not None
