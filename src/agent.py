"""
LangGraph Conversation Agent for Thai Legal RAG
- หลายเทิร์น (multi-turn) ด้วย memory ต่อ session
- แยก intent: คำถามกฎหมาย / คุยทั่วไป / ถามต่อจากเทิร์นก่อน
- Rewrite query จากประวัติแชท แทนการฉีดชื่อกฎหมายตายตัว
- Grade เอกสารที่ retrieve มาก่อนตอบ ลด hallucination
"""
import warnings

warnings.filterwarnings("ignore")

import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import config
from retriever import Retriever
from llm_client import LLMClient
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from typing_extensions import Annotated, TypedDict
from typing import Literal


def add_messages(left, right):
    """Merge message lists for the LangGraph state."""
    return left + right


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    intent: str
    standalone_question: str
    retrieved_docs: list
    generation: str
    sources: list
    enough_context: bool


LEGAL_INTENT_PROMPT = """จำแนกประเภทข้อความสุดท้ายของผู้ใช้ในบทสนทนา

บทสนทนา:
{conversation}

ข้อความสุดท้ายของผู้ใช้: "{question}"

ตอบด้วยคำเดียวเท่านั้น:
- "legal" ถ้าเป็นคำถามเกี่ยวกับกฎหมาย เงื่อนไขทางกฎหมาย สิทธิ หน้าที่ มาตรา หรือข้อความในกฎหมาย
- "chat" ถ้าเป็นการทักทาย ขอบคุณ คุยเล่น หรือคำถามเกี่ยวกับตัวผู้ช่วยเอง

คำตอบ:"""

REWRITE_PROMPT = """จากบทสนทนาต่อไปนี้ เขียนคำถามสุดท้ายของผู้ใช้ใหม่ให้เป็นคำถามที่สมบูรณ์ในตัวเอง (standalone) โดยไม่ต้องอาศัยบริบทก่อนหน้า
- รวมคำที่ผู้ใช้อ้างถึงจากเทิร์นก่อน เช่น "มาตรานั้น", "อย่างที่คุยกัน" ให้เป็นชื่อหรือรายละเอียดจริง
- ห้ามเพิ่มข้อมูลที่ไม่มีในบทสนทนา
- ตอบด้วยข้อความคำถามเท่านั้น ไม่ต้องมีคำอธิบาย

บทสนทนา:
{conversation}

คำถามสุดท้าย: "{question}"

คำถามที่เขียนใหม่:"""

GRADE_PROMPT = """You are a grader checking whether retrieved legal documents are useful for answering the user's question.

Question: {question}

Retrieved documents:
---
{documents}
---

Is at least one document relevant or partially relevant to the question's topic (e.g. mentions the same offense, right, law, or related penalties)?
Be generous: if the topic overlaps, answer yes. Only answer no if NOTHING is related.
Answer with EXACTLY one word: "yes" or "no"."""


class LegalAgent:
    def __init__(self):
        print("Initializing LangGraph Legal Conversation Agent...")
        self.retriever = Retriever()
        self.llm_client = LLMClient()
        self.llm = self.llm_client.llm
        self.chat_llm = self.llm.bind()  # same model, plain chat

        self._intent_chain = None
        self._rewrite_chain = None
        self._grade_chain = None

        os.makedirs(os.path.dirname(config.SESSION_DB_PATH), exist_ok=True)
        import sqlite3
        self._conn = sqlite3.connect(config.SESSION_DB_PATH, check_same_thread=False)
        self.checkpointer = SqliteSaver(self._conn)
        self.graph = self._build_graph()
        print("Agent ready.")

    # ---------- lazy chains ----------
    def _get_intent_chain(self):
        if self._intent_chain is None:
            from langchain_core.output_parsers import StrOutputParser
            self._intent_chain = (
                ChatPromptTemplate.from_template(LEGAL_INTENT_PROMPT) | self.llm | StrOutputParser()
            )
        return self._intent_chain

    def _get_rewrite_chain(self):
        if self._rewrite_chain is None:
            from langchain_core.output_parsers import StrOutputParser
            self._rewrite_chain = (
                ChatPromptTemplate.from_template(REWRITE_PROMPT) | self.llm | StrOutputParser()
            )
        return self._rewrite_chain

    def _get_grade_chain(self):
        if self._grade_chain is None:
            from langchain_core.output_parsers import StrOutputParser
            self._grade_chain = (
                ChatPromptTemplate.from_template(GRADE_PROMPT) | self.llm | StrOutputParser()
            )
        return self._grade_chain

    # ---------- graph ----------
    def _build_graph(self):
        g = StateGraph(AgentState)
        g.add_node("route_intent", self._route_intent)
        g.add_node("rewrite_query", self._rewrite_query)
        g.add_node("retrieve", self._retrieve)
        g.add_node("grade_docs", self._grade_docs)
        g.add_node("generate", self._generate)
        g.add_node("small_talk", self._small_talk)

        g.add_edge(START, "route_intent")
        g.add_conditional_edges(
            "route_intent",
            lambda s: s.get("intent", "legal"),
            {"legal": "rewrite_query", "chat": "small_talk"},
        )
        g.add_edge("rewrite_query", "retrieve")
        g.add_edge("retrieve", "grade_docs")
        g.add_conditional_edges(
            "grade_docs",
            lambda s: "generate" if s.get("enough_context") else END,
            {"generate": "generate", END: END},
        )
        g.add_edge("generate", END)
        g.add_edge("small_talk", END)
        return g.compile(checkpointer=self.checkpointer)

    # ---------- nodes ----------
    def _conversation_text(self, state):
        msgs = state["messages"][-(config.CHAT_HISTORY_LIMIT + 1):]
        lines = []
        for m in msgs:
            role = "ผู้ใช้" if isinstance(m, HumanMessage) else "ผู้ช่วย"
            lines.append(f"{role}: {m.content}")
        return "\n".join(lines)

    def _route_intent(self, state):
        question = state["messages"][-1].content
        try:
            intent_raw = self._get_intent_chain().invoke({
                "conversation": self._conversation_text(state),
                "question": question,
            }).strip().lower()
        except Exception:
            intent_raw = "legal"
        intent = "chat" if "chat" in intent_raw[:20] else "legal"
        return {"intent": intent}

    def _rewrite_query(self, state):
        question = state["messages"][-1].content
        standalone = question
        # Rewrite เฉพาะเมื่อมีประวัติก่อนหน้า (multi-turn) — เทิร์นแรกใช้คำถามตรงๆ
        if len(state["messages"]) > 1:
            try:
                rewritten = self._get_rewrite_chain().invoke({
                    "conversation": self._conversation_text(state),
                    "question": question,
                }).strip()
                if rewritten:
                    standalone = rewritten
            except Exception:
                pass
        return {"standalone_question": standalone}

    def _retrieve(self, state):
        standalone = state.get("standalone_question") or state["messages"][-1].content
        try:
            docs = self.retriever.retrieve(standalone)
        except Exception:
            docs = []
        return {"retrieved_docs": docs}

    def _grade_docs(self, state):
        docs = state.get("retrieved_docs") or []
        question = state.get("standalone_question") or state["messages"][-1].content
        if not docs:
            msg = "ขออภัยครับ ผมไม่พบเนื้อหากฎหมายที่เกี่ยวข้องกับคำถามนี้ในฐานข้อมูล ลองระบุชื่อกฎหมายหรือหัวข้อให้ชัดเจนขึ้นได้ครับ"
            return {
                "enough_context": False,
                "generation": msg,
                "sources": [],
                "messages": [AIMessage(content=msg)],
            }

        doc_text = "\n\n".join(
            f"[{i+1}] {d.page_content[:500]}" for i, d in enumerate(docs[:5])
        )
        try:
            verdict = self._get_grade_chain().invoke({
                "question": question,
                "documents": doc_text,
            }).strip().lower()
            enough = verdict.startswith("yes") or "yes" in verdict[:40]
        except Exception:
            # ถ้า grader ล้มเหลว ให้ผ่านไปก่อน (มี docs อยู่แล้ว)
            enough = True
        if not enough:
            msg = "ขออภัยครับ เนื้อหากฎหมายที่ค้นเจอยังไม่สามารถตอบคำถามนี้ได้อย่างมั่นใจ หากคำถามเกี่ยวกับกฎหมายเฉพาะฉบับ ลองระบุชื่อกฎหมายหรือเลขมาตรามาด้วยครับ"
            return {
                "enough_context": False,
                "generation": msg,
                "sources": [],
                "messages": [AIMessage(content=msg)],
            }
        return {"enough_context": True}

    def _small_talk(self, state):
        question = state["messages"][-1].content
        conversation = self._conversation_text(state)
        reply = self.chat_llm.invoke(
            f"""คุณคือผู้ช่วยตอบคำถามกฎหมายไทย กำลังคุยกับผู้ใช้แบบสุภาพ มิตรภาพ ตอบสั้นกระชับ (1-3 ประโยค)
หากผู้ใช้ยังไม่ได้ถามเรื่องกฎหมาย ให้ต้อนรับและชวนให้ถามคำถามกฎหมายที่สงสัย

บทสนทนา:
{conversation}

ข้อความผู้ใช้: "{question}"

ข้อความตอบ:"""
        )
        return {
            "generation": reply.content,
            "sources": [],
            "messages": [AIMessage(content=reply.content)],
        }

    def _generate(self, state):
        docs = state.get("retrieved_docs") or []
        question = state.get("standalone_question") or state["messages"][-1].content

        # ใช้ prompt ตอบกฎหมายเดิมจาก LLMClient (CoT + สรุปฟันธง) ผ่านประวัติแชทด้วย
        history = state["messages"][-(config.CHAT_HISTORY_LIMIT + 1):-1]
        answer = self.llm_client.generate_answer(question, docs, history=history)

        sources = []
        for doc in docs:
            meta = doc.metadata
            src = {
                "title": meta.get("title", ""),
                "section": meta.get("section_id") or meta.get("section_header", ""),
                "publish_date": meta.get("publish_date", ""),
                "source": meta.get("source", ""),
            }
            if src not in sources:
                sources.append(src)
        return {
            "generation": answer,
            "sources": sources,
            "messages": [AIMessage(content=answer)],
        }

    # ---------- public API ----------
    def chat(self, session_id: str, message: str) -> dict:
        """รับข้อความหนึ่งเทิร์น คืนคำตอบ + แหล่งอ้างอิง (จำ context ของ session อัตโนมัติ)"""
        result = self.graph.invoke(
            {"messages": [HumanMessage(content=message)]},
            config={"configurable": {"thread_id": session_id}},
        )
        return {
            "answer": result.get("generation", ""),
            "sources": result.get("sources", []),
        }

    def history(self, session_id: str) -> list:
        state = self.graph.get_state(
            config={"configurable": {"thread_id": session_id}}
        )
        return state.values.get("messages", []) if state and state.values else []

    def reset(self, session_id: str):
        self.checkpointer.delete_thread(session_id)


if __name__ == "__main__":
    agent = LegalAgent()
    print("CLI test mode. 'exit' to quit.")
    sid = "cli-test"
    while True:
        q = input("คุณ: ").strip()
        if q.lower() in ("exit", "quit", "q"):
            break
        if not q:
            continue
        out = agent.chat(sid, q)
        print("\nบอท:", out["answer"])
        if out["sources"]:
            print("อ้างอิง:", ", ".join(
                f"{s['title']} {s['section']}".strip() for s in out["sources"][:3]
            ))
        print()
