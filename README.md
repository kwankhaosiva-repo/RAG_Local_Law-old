# Thai Legal RAG Chatbot

โปรเจกต์นี้เป็น **แชทบอทตอบคำถามกฎหมายไทย** ที่พัฒนาต่อยอดจากระบบ Retrieval-Augmented Generation (RAG) เดิม — คุยได้หลายเทิร์น (จำ context ต่อเนื่อง), ตอบพร้อมอ้างอิงมาตรา/ชื่อกฎหมาย, และ **อัปเดตกฎหมายใหม่จากราชกิจจานุเบกษาอัตโนมัติทุกสัปดาห์**

---

## สถาปัตยกรรมระบบ (System Architecture)

```
ผู้ใช้ ──> Web Chat UI (FastAPI) ──┐
LINE/Telegram (ผ่าน OpenClaw) ──┼──> LangGraph Agent ──> Retriever (Hybrid RRF) ──> Ollama LLM
CLI (python src/main.py) ──┘         │
                                     └──> ChromaDB (core_law + recent_law)
                                              ▲
                              Ratchakitcha Crawler (อัตโนมัติทุกสัปดาห์)
```

### 1. LangGraph Conversation Agent (`src/agent.py`) — สมองของแชทบอท
แทน pipeline เดิมที่ตอบเดี่ยวๆ ด้วย StateGraph ที่มี 4 ขั้นตอน:

*   **Route Intent:** แยกว่าเป็น "คำถามกฎหมาย" (→ ค้นฐานกฎหมาย) หรือ "คุยทั่วไป" (→ ตอบแบบแชทสุภาพ ไม่ต้องค้น)
*   **Query Rewrite:** คำถามในเทิร์นต่อๆ มา (เช่น "แล้วมาตราถัดไปล่ะ") จะถูก LLM รวมกับประวัติแชทให้เป็นคำถามสมบูรณ์ก่อนค้น — **แทนการฉีดชื่อกฎหมายตายตัวแบบเดิม**
*   **Hybrid Retrieval + Doc Grading:** ดึงเอกสารด้วย Vector + BM25 + RRF เหมือนเดิม แล้วให้ LLM ตรวจก่อนว่าเนื้อหาตอบคำถามได้จริงไหม — ถ้าไม่ จะตอบอย่างสุภาพว่าไม่พบข้อมูล (ลด hallucination)
*   **Multi-turn Memory:** เก็บประวัติต่อ session ด้วย `SqliteSaver` (LangGraph checkpointer) — คุยต่อเนื่องได้โดยส่งแค่ `session_id`

### 2. Data Ingestion (การนำเข้าข้อมูล)
*   **Hybrid Chunking Strategy:** ใช้ RegEx แยกกลุ่มตาม "หัวมาตรา" (มาตรา/ข้อ) ก่อน แล้วซอยด้วย `RecursiveCharacterTextSplitter`
*   **Hierarchical Metadata:** `title`, `section_id`, `hierarchy_level` (ลำดับศักดิ์ของกฎหมาย), `publish_date` ฝังในทุก Chunk
*   **Prefix Context Injection:** ชื่อกฎหมาย+มาตราแปะหัวทุก Chunk เสมอ
*   **Generic Law Detection (ใหม่):** Retriever ตรวจจับชื่อกฎหมายจากคำถามด้วย regex (`detect_target_law`) แทน list ตายตัว — รองรับกฎหมายทุกฉบับที่อยู่ในฐาน

### 3. Web Chat UI + API (`src/server.py`, `src/web/index.html`)
*   `POST /chat` — `{session_id, message}` → `{answer, sources[]}` (ชื่อกฎหมาย + มาตรา + วันประกาศ)
*   `POST /reset` — ล้างประวัติห้องแชท
*   `GET /` — หน้าเว็บแชทภาษาไทย แสดงฟองแชท + แหล่งอ้างอิงใต้คำตอบ

### 4. OpenClaw Gateway Bridge (`src/openclaw_bridge.py`)
เปิด endpoint `POST /openclaw/webhook` รับ payload `{chat_id, text}` จาก [OpenClaw](https://github.com/openclaw/openclaw) (open-source agent gateway) เพื่อเปิดช่องทาง **LINE / Telegram / WhatsApp / Discord** โดยไม่ต้องแก้ agent — แค่ชี้ webhook ของ OpenClaw มาที่ endpoint นี้ (รองรับ shared secret ผ่าน env `OPENCLAW_SECRET` + header `X-OpenClaw-Secret`)

### 5. Ratchakitcha Crawler (`src/crawl_ratchakitcha.py`) — อัปเดตกฎหมายใหม่อัตโนมัติ
*   ดึงรายการประกาศใหม่จาก **Web Service ทางการของราชกิจจานุเบกษา** (`api.soc.go.th`) กรองเฉพาะ พ.ร.บ. / พ.ร.ฎ. / กฎกระทรวง / ประกาศ / ระเบียบ
*   โหลด PDF → extract ข้อความ (PyMuPDF) → แบ่งตามมาตรา/ข้อ → ingest เข้า collection `recent_law`
*   **Deduplicate:** hash (title + วันประกาศ) ใน SQLite กัน ingest ซ้ำ
*   รันครั้งเดียว: `python src/crawl_ratchakitcha.py` | รันต่อเนื่องทุกสัปดาห์: `python src/crawl_ratchakitcha.py --weekly`
*   *หมายเหตุ:* PDF ที่ไม่มี text layer (เป็นรูปภาพ) จะถูกข้ามไว้ให้ pipeline OCR ของ iapp_2025 จัดการ

### 6. Generation & Evaluation (คงเดิม)
*   **Chain-of-Thought + Strict Polarity:** ยก quote ก่อนแล้ว "สรุป:" ฟันธงชัดเจน — คงระบบเดิมไว้สำหรับคำถามกฎหมาย
*   **LLM-as-a-Judge:** `src/evaluate.py` ใช้ประเมิน accuracy ก่อน/หลังปรับระบบ

---

## การติดตั้งและรัน (Quick Start)

```bash
pip install -r requirements.txt
ollama pull llama3.2

# 1) Ingest ฐานกฎหมาย (ครั้งแรก)
python src/ingest_core.py
python src/ingest_recent.py

# 2) รันแชทบอท — เลือก 1 ใน 3 วิธี
python src/main.py                      # CLI แบบหลายเทิร์น
python src/server.py                    # Web Chat: http://localhost:8000
uvicorn src.server:app --host 0.0.0.0 --port 8000

# 3) (ทางเลือก) อัปเดตกฎหมายใหม่จากราชกิจจาฯ
python src/crawl_ratchakitcha.py            # ครั้งเดียว
python src/crawl_ratchakitcha.py --weekly   # ทุกสัปดาห์
```

### การเชื่อม OpenClaw (เปิดช่องทาง LINE/Telegram ฯลฯ)
1. รัน server: `python src/server.py`
2. ใน config ของ OpenClaw gateway ชี้ webhook ไปที่ `POST http://<host>:8000/openclaw/webhook` ส่ง `{chat_id, text}`
3. (แนะนำ) ตั้ง `OPENCLAW_SECRET=...` ทั้งสองฝั่งเพื่อยืนยันตัวตน

### การประเมินผล
```bash
python src/evaluate.py   # วัด accuracy เทียบกับ dataset ข้อสอบ (WangchanX-Legal-ThaiCCL-RAG)
```

---

## เทคโนโลยีที่ใช้งาน (Tech Stack)

*   **LangGraph:** สมองของ agent — StateGraph, multi-turn memory (SqliteSaver), intent routing, doc grading
*   **LangChain:** Data loaders, prompts, output parsers และ hybrid retriever
*   **ChromaDB:** Vector Database แบบ Offline (Local) สอง collection: `core_law` + `recent_law`
*   **Ollama:** รัน Local LLM (Llama 3.2) — ไม่มีค่า API รัน offline ได้
*   **HuggingFace Embeddings:** BAAI/bge-m3 / multilingual MiniLM สำหรับ semantic search
*   **PyThaiNLP:** ตัดคำไทยสำหรับ BM25
*   **FastAPI + Vanilla JS:** Web Chat UI
*   **OpenClaw:** agent gateway เชื่อมช่องทางแชทภายนอก (LINE, Telegram, WhatsApp)
*   **PyMuPDF + requests:** Ratchakitcha crawler

---

## แหล่งข้อมูล (References & Datasets)

อ้างอิงชุดข้อมูลกฎหมายไทยจากการเผยแพร่โดย Open Law Data Thailand:

1.  **ocs-krisdika:** ชุดข้อมูลกฎหมายจากสำนักงานคณะกรรมการกฤษฎีกา
2.  **iapp_2025:** ชุดข้อมูลกฎหมายประกาศใหม่ประมวลผลผ่าน OCR
3.  **WangchanX-Legal-ThaiCCL-RAG:** ชุดข้อมูลสำหรับการประเมินระบบ RAG กฎหมายไทยโดยศูนย์วิจัย AIResearch
4.  **ราชกิจจานุเบกษา Web Service (api.soc.go.th):** แหล่งดึงกฎหมายใหม่อัตโนมัติผ่าน crawler
