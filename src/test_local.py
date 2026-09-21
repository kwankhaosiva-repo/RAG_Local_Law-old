"""
Dev Test Mode: ทดสอบทั้งระบบด้วย Ollama local LLM ก่อน deploy ขึ้น GCP
- ไม่ต้องมี LINE token / cloud API key เลย
- ใช้โมเดลที่ pull ไว้แล้วในเครื่อง (default: mistral-small3.2:24b)

วิธีรัน:
    python src/test_local.py                          # ใช้ mistral-small3.2:24b
    python src/test_local.py --model llama3.2         # สลับโมเดล
    python src/test_local.py --quick                  # ข้าม multi-turn (เร็วขึ้น)
    python src/test_local.py --host http://localhost:11434

ทดสอบครบ 6 เคส:
  1. Ollama server เปิดอยู่ + มีโมเดลนี้ในเครื่อง
  2. LLM ตอบง่ายๆ ได้ (raw invoke)
  3. Retriever ค้น chroma_db เจอเอกสาร
  4. Agent ตอบคำถามกฎหมายพร้อมอ้างอิง (intent=legal → retrieve → grade → generate)
  5. Multi-turn: ถามต่อ "มาตราถัดไป" แล้ว rewrite query ทำงาน
  6. Small talk: intent routing ไม่ไปค้นกฎหมาย
"""
import argparse
import os
import sys
import time

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

parser = argparse.ArgumentParser(description="Dev test mode ด้วย Ollama local")
parser.add_argument("--model", default="mistral-small3.2:24b", help="Ollama model name")
parser.add_argument("--host", default=None, help="Ollama host (default จาก env/localhost:11434)")
parser.add_argument("--quick", action="store_true", help="ข้าม multi-turn test (โมเดลใหญ่ตอบช้า)")
args = parser.parse_args()

# ตั้ง env ก่อน import config เพื่อ force local provider + model ที่เลือก
os.environ["LLM_PROVIDER"] = "ollama"
os.environ["LLM_MODEL_NAME"] = args.model
if args.host:
    os.environ["OLLAMA_HOST"] = args.host

import config
import requests
from langchain_core.messages import HumanMessage

RESULTS = []


def record(name: str, ok: bool, detail: str = "", elapsed: float = 0.0):
    RESULTS.append((name, ok))
    status = "PASS ✅" if ok else "FAIL ❌"
    t = f" ({elapsed:.1f}s)" if elapsed else ""
    print(f"\n[{status}] {name}{t}")
    if detail:
        for line in detail.splitlines():
            print(f"    {line[:160]}")


print("=" * 60)
print(f"Dev Test Mode — Ollama local")
print(f"  host : {config.OLLAMA_HOST}")
print(f"  model: {config.LLM_MODEL_NAME}")
print("=" * 60)

# ---------- Test 1: Ollama server + model available ----------
try:
    t0 = time.time()
    r = requests.get(f"{config.OLLAMA_HOST}/api/tags", timeout=5)
    r.raise_for_status()
    models = [m["name"] for m in r.json().get("models", [])]
    has_model = any(m == config.LLM_MODEL_NAME or m.split(":")[0] == config.LLM_MODEL_NAME.split(":")[0]
                    for m in models)
    record("1) Ollama server + model", has_model,
           f"โมเดลในเครื่อง: {', '.join(models) or '(ไม่มี)'}", time.time() - t0)
except Exception as e:
    record("1) Ollama server + model", False, f"เชื่อมต่อ {config.OLLAMA_HOST} ไม่ได้: {e}")
    print("\n💡 รัน `ollama serve` ก่อน แล้วลองใหม่")
    sys.exit(1)

if not RESULTS[-1][1]:
    print(f"\n💡 รัน `ollama pull {config.LLM_MODEL_NAME}` ก่อน (โมเดลที่มี: {models})")
    sys.exit(1)

# ---------- Test 2: Raw LLM invoke ----------
try:
    t0 = time.time()
    from langchain_ollama import ChatOllama
    llm = ChatOllama(model=config.LLM_MODEL_NAME, temperature=0.0, base_url=config.OLLAMA_HOST)
    reply = llm.invoke([HumanMessage(content="ตอบด้วยคำเดียวว่า: OK")])
    ok = "ok" in reply.content.strip().lower()
    record("2) Raw LLM invoke", ok, f"ตอบ: {reply.content.strip()[:100]}", time.time() - t0)
except Exception as e:
    record("2) Raw LLM invoke", False, str(e))

# ---------- Test 3: Retriever (chroma_db) ----------
try:
    t0 = time.time()
    from retriever import Retriever
    retriever = Retriever()
    docs = retriever.retrieve("การโจรกรรมมีโทษอย่างไร")
    record("3) Retriever ค้นเจอเอกสาร", len(docs) > 0,
           f"ได้ {len(docs)} chunks" + (f" | ตัวอย่าง: {docs[0].metadata.get('title', '')[:80]}" if docs else ""),
           time.time() - t0)
except Exception as e:
    record("3) Retriever ค้นเจอเอกสาร", False, str(e))
    print("💡 ถ้ายังไม่ได้ ingest รัน: python src/ingest_core.py && python src/ingest_recent.py")

# ---------- Test 4-6: Full agent (จะช้าหน่อยกับโมเดล 24b) ----------
if RESULTS[-1][1]:  # มี docs ค่อยทดสอบ agent
    try:
        t0 = time.time()
        print("\nกำลังโหลด agent (embeddings + LLM)...")
        from agent import LegalAgent
        agent = LegalAgent()
        sid = "dev-test"
        agent.reset(sid)

        out = agent.chat(sid, "การโจรกรรมมีโทษจำคุกกี่ปี")
        has_cite = len(out["sources"]) > 0
        record("4) Agent ตอบกฎหมาย + อ้างอิง", bool(out["answer"]) and has_cite,
               out["answer"][:200] + f"\n    อ้างอิง {len(out['sources'])} รายการ", time.time() - t0)

        if not args.quick and has_cite:
            t0 = time.time()
            out2 = agent.chat(sid, "แล้วถ้าใช้ความรุนแรงทำให้เจ็บล่ะ")
            record("5) Multi-turn (query rewrite)", bool(out2["answer"]),
                   out2["answer"][:200], time.time() - t0)
            agent.reset(sid)
        elif not has_cite:
            print("\n(ข้าม Test 5 เพราะ Test 4 ไม่ผ่าน)")
        else:
            print("\n(ข้าม Test 5 — ใช้ --quick)")

        agent.reset(sid)  # เริ่ม session ใหม่ — ไม่งั้น intent routing จะเห็นบทสนทนากฎหมายเก่า
        t0 = time.time()
        out3 = agent.chat(sid, "สวัสดี วันนี้อากาศเป็นไงบ้าง")
        # small talk ต้องไม่มี sources (ไม่ไปค้นกฎหมาย)
        record("6) Small talk (intent routing)", bool(out3["answer"]) and len(out3["sources"]) == 0,
               out3["answer"][:200], time.time() - t0)
        agent.reset(sid)
    except Exception as e:
        record("Agent tests", False, str(e))
else:
    print("\n(ข้าม Test 4-6 เพราะ Retriever ไม่ผ่าน)")

# ---------- Summary ----------
passed = sum(1 for _, ok in RESULTS if ok)
total = len(RESULTS)
print("\n" + "=" * 60)
print(f"สรุป: {passed}/{total} tests passed")
if passed == total:
    print("🎉 ระบบพร้อม — ถัดไป: deploy ขึ้น GCP แล้วสลับ provider เป็น cloud LLM (ดู gcp_deploy.txt)")
else:
    print("⚠️  มีเคสที่ยังไม่ผ่าน — แก้ตาม 💡 ด้านบนแล้วรันใหม่")
    sys.exit(1)
