"""
Shared runtime: lazy singleton for the LangGraph LegalAgent
ใช้ร่วมกันโดย server.py (web), line_bot.py, discord_bot.py, openclaw_bridge.py
เพื่อไม่โหลด embeddings + LLM ซ้ำหลายครั้งต่อ 1 process
"""
import threading

import db_sync

_lock = threading.Lock()
_agent = None


def get_agent():
    """สร้าง (ครั้งเดียวต่อ process) และคืน LegalAgent
    import แบบ lazy — ทำให้ server bind PORT ได้ไว ไม่รอโหลด ML libraries ก่อน
    """
    global _agent
    if _agent is None:
        with _lock:
            if _agent is None:
                from agent import LegalAgent  # lazy import
                _agent = LegalAgent()
    return _agent


def ask(question: str, session_id: str = "default") -> dict:
    """เรียก agent แบบหลายเทิร์น — คืน {'answer': str, 'sources': [...]}
    ถ้า chroma_db ยัง sync จาก GCS ไม่เสร็จ จะรอจนกว่าจะพร้อม (สูงสุด 5 นาที)
    """
    db_sync.wait_ready(timeout=300)  # บล็อกจน vector DB พร้อมก่อน
    result = get_agent().chat(session_id.strip() or "default", question)

    # persist + analytics (optional — fail ได้ ไม่กระทบคำตอบ)
    try:
        import firestore_db
        firestore_db.save_turn(
            session_id.strip() or "default",
            user_message=question,
            assistant_message=result["answer"],
            sources=result.get("sources", []),
        )
    except Exception as e:
        print(f"[runtime] firestore save skipped: {e}")

    try:
        import analytics
        analytics.log_query(
            session_id=session_id.strip() or "default",
            question=question,
            answer=result["answer"],
            sources=result.get("sources", []),
        )
    except Exception as e:
        print(f"[runtime] analytics log skipped: {e}")

    return result


def reset(session_id: str = "default"):
    """ล้างประวัติบทสนทนาของ session"""
    get_agent().reset(session_id.strip() or "default")
    try:
        import firestore_db
        firestore_db.reset_session(session_id.strip() or "default")
    except Exception as e:
        print(f"[runtime] firestore reset skipped: {e}")
