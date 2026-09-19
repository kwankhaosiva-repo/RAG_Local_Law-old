"""
FastAPI Web Chat Server for Thai Legal RAG
- POST /chat    : {session_id, message} -> {answer, sources}
- POST /reset   : ล้างประวัติห้องแชท
- GET  /        : หน้าเว็บแชท
- POST /openclaw/webhook : จุดเชื่อม OpenClaw gateway (ดู openclaw_bridge.py)
"""
import warnings

warnings.filterwarnings("ignore")

import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import config
from agent import LegalAgent
from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

app = FastAPI(title="Thai Legal RAG Chatbot")

# สร้าง agent ครั้งเดียวตอน startup (โหลด embeddings + LLM)
_agent = None


def get_agent() -> LegalAgent:
    global _agent
    if _agent is None:
        _agent = LegalAgent()
    return _agent


class ChatRequest(BaseModel):
    session_id: str = "default"
    message: str


class ChatResponse(BaseModel):
    answer: str
    sources: list = []


@app.get("/")
def index():
    return FileResponse(os.path.join(os.path.dirname(__file__), "web", "index.html"))


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    agent = get_agent()
    try:
        out = agent.chat(req.session_id.strip(), req.message.strip())
        return ChatResponse(answer=out["answer"], sources=out["sources"])
    except Exception as e:
        return ChatResponse(
            answer=f"เกิดข้อผิดพลาดในการประมวลผล: {e}", sources=[]
        )


@app.post("/reset")
def reset(req: ChatRequest):
    get_agent().reset(req.session_id.strip())
    return {"ok": True}


# --- OpenClaw Gateway Bridge (ถ้ามีไฟล์ bridge ให้ mount) ---
try:
    from openclaw_bridge import openclaw_router

    app.include_router(openclaw_router)
    print("OpenClaw bridge mounted at", config.OPENCLAW_WEBHOOK_PATH)
except ImportError:
    pass


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=config.API_HOST, port=config.API_PORT)
