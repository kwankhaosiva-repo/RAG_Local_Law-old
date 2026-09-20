"""
Shared runtime: lazy singleton for the LangGraph LegalAgent
ใช้ร่วมกันโดย server.py (web), line_bot.py, discord_bot.py, openclaw_bridge.py
เพื่อไม่โหลด embeddings + LLM ซ้ำหลายครั้งต่อ 1 process
"""
import threading

from agent import LegalAgent

_lock = threading.Lock()
_agent: LegalAgent | None = None


def get_agent() -> LegalAgent:
    """สร้าง (ครั้งเดียวต่อ process) และคืน LegalAgent"""
    global _agent
    if _agent is None:
        with _lock:
            if _agent is None:
                _agent = LegalAgent()
    return _agent


def ask(question: str, session_id: str = "default") -> dict:
    """เรียก agent แบบหลายเทิร์น — คืน {'answer': str, 'sources': [...]}"""
    return get_agent().chat(session_id.strip() or "default", question)


def reset(session_id: str = "default"):
    """ล้างประวัติบทสนทนาของ session"""
    get_agent().reset(session_id.strip() or "default")
