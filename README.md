# Thai Legal RAG Chatbot

โปรเจกต์นี้เป็น **แชทบอทตอบคำถามกฎหมายไทยแบบหลายช่องทาง (Multi-channel)** — Web, LINE, Discord, OpenClaw และ CLI — คุยได้หลายเทิร์น (จำ context ต่อเนื่อง), ตอบพร้อมอ้างอิงมาตรา/ชื่อกฎหมาย, รองรับ **LLM ทั้ง local (Ollama) และ cloud (OpenAI/Anthropic/Google/OpenRouter)** และ **อัปเดตกฎหมายใหม่จากราชกิจจานุเบกษาอัตโนมัติทุกสัปดาห์**

---

## สถาปัตยกรรมระบบ (System Architecture)

```
ผู้ใช้ ──> Web Chat UI (FastAPI /) ─────────┐
       ──> LINE Messaging API (/line/webhook) ├─> runtime (shared agent) ──> LangGraph Agent
       ──> OpenClaw Gateway (/openclaw/webhook)┘                                    │
       ──> Discord Bot (แยก process) ───────────────────────────────────────────────┤
       ──> CLI (python src/main.py) ────────────────────────────────────────────────┤
                                                                                    ▼
                                                              Retriever (Hybrid RRF: Vector + Thai BM25)
                                                                                    │
                                                                    ChromaDB (core_law + recent_law)
                                                                                    ▲
                                                       Ratchakitcha Crawler (อัตโนมัติทุกสัปดาห์)
```

### 1. LangGraph Conversation Agent (`src/agent.py`) — สมองของแชทบอท
แทน pipeline เดิมที่ตอบเดี่ยวๆ ด้วย StateGraph ที่มี 4 ขั้นตอน:

*   **Route Intent:** แยกว่าเป็น "คำถามกฎหมาย" (→ ค้นฐานกฎหมาย) หรือ "คุยทั่วไป" (→ ตอบแบบแชทสุภาพ ไม่ต้องค้น)
*   **Query Rewrite:** คำถามในเทิร์นต่อๆ มา (เช่น "แล้วมาตราถัดไปล่ะ") จะถูก LLM รวมกับประวัติแชทให้เป็นคำถามสมบูรณ์ก่อนค้น — **แทนการฉีดชื่อกฎหมายตายตัวแบบเดิม**
*   **Hybrid Retrieval + Doc Grading:** ดึงเอกสารด้วย Vector + BM25 + RRF แล้วให้ LLM ตรวจก่อนว่าเนื้อหาตอบคำถามได้จริงไหม — ถ้าไม่ จะตอบอย่างสุภาพว่าไม่พบข้อมูล (ลด hallucination)
*   **Multi-turn Memory:** เก็บประวัติต่อ session ด้วย `SqliteSaver` (LangGraph checkpointer) — คุยต่อเนื่องได้โดยส่งแค่ `session_id`

### 2. Multi-channel Chat (ทุกช่องทางใช้ agent ตัวเดียวกันผ่าน `src/runtime.py`)

| ช่องทาง | ไฟล์ | วิธีรัน | หมายเหตุ |
|---|---|---|---|
| **Web Chat** | `src/server.py` + `src/web/index.html` | `python src/server.py` → http://localhost:8000 | มี `POST /chat`, `POST /reset`, หน้าเว็บแชทไทย |
| **LINE** | `src/line_bot.py` | mount ใน server อัตโนมัติ | webhook `POST /line/webhook`, ตรวจ `X-Line-Signature`, แบ่งข้อความยาวเป็นหลาย bubble, คำสั่ง `ล้างแชท` |
| **Discord** | `src/discord_bot.py` | `python src/discord_bot.py` (แยก process) | `!ask <คำถาม>`, mention บอท, slash `/law`, ต่อ session ต่อ channel |
| **OpenClaw** | `src/openclaw_bridge.py` | mount ใน server อัตโนมัติ | webhook `POST /openclaw/webhook` + secret auth |
| **CLI** | `src/main.py` | `python src/main.py` | คำสั่ง `reset`, `exit` |

ทุก adapter เรียก `runtime.ask(text, session_id)` / `runtime.reset(session_id)` — agent โหลดครั้งเดียวต่อ process ใช้ร่วมกันทุกช่องทาง

### 3. Data Ingestion (การนำเข้าข้อมูล)
*   **Thai Law Section Splitter (ใหม่):** `src/thai_law_splitter.py` แก้ปัญหา truncation ของหัวมาตรา:
    *   จับรูปแบบครบ: `มาตรา 5`, `มาตราที่ 7/1`, `มาตรา ๓๙` (เลขไทย), `ข้อที่ 5`, คำเกิน (ทวิ/ตรี/จัตวา)
    *   **ไม่มีการตัดกลางคำ** — chunk ที่ซอยต่อจากมาตรายาวๆ จะมี header "ส่วนของ: มาตรา X (ส่วนที่ n/m)" ติดทุก chunk
    *   วิธีเดิม regex จับ "มาตราที่ 7/1" ได้เพี้ยนเป็น "มาตราท" (ตัดกลางคำ) และ chunk ที่ 2..n ของมาตราเดียวกันหายหัวมาตรา — ตรวจสอบด้วย `python src/test_section_splitter.py` (ALL TESTS PASSED ✅)
*   **Hierarchical Metadata:** `title`, `section_id`, `hierarchy_level`, `publish_date` ฝังในทุก Chunk
*   **Prefix Context Injection:** ชื่อกฎหมาย+มาตราแปะหัวทุก Chunk เสมอ
*   **Generic Law Detection:** Retriever ตรวจจับชื่อกฎหมายจากคำถามด้วย regex (`detect_target_law`) รองรับกฎหมายทุกฉบับ

### 4. LLM รองรับหลาย Provider (`src/llm_client.py`)
ตั้งผ่าน `.env` (ดู `.env_example`):

| Provider | env | โมเดลแนะนำ |
|---|---|---|
| **Ollama (local, default)** | `LLM_PROVIDER=ollama` + `OLLAMA_HOST` + `LLM_MODEL_NAME` | llama3.2 |
| **OpenAI** | `LLM_PROVIDER=openai` + `OPENAI_API_KEY` | gpt-4o-mini |
| **Anthropic** | `LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY` | claude-3-5-haiku |
| **Google** | `LLM_PROVIDER=google` + `GOOGLE_API_KEY` | gemini-2.0-flash |
| **OpenRouter** | `LLM_PROVIDER=openrouter` + `OPENROUTER_API_KEY` | openai/gpt-4o-mini |

สลับ provider ได้โดยไม่แก้โค้ด — เหมาะกับการ deploy บน cloud ที่ไม่มี GPU

### 5. Ratchakitcha Crawler (`src/crawl_ratchakitcha.py`) — อัปเดตกฎหมายใหม่อัตโนมัติ
*   ดึงรายการประกาศใหม่จาก **Web Service ทางการของราชกิจจานุเบกษา** (`api.soc.go.th`) กรองเฉพาะ พ.ร.บ. / พ.ร.ฎ. / กฎกระทรวง / ประกาศ / ระเบียบ
*   โหลด PDF → extract ข้อความ (PyMuPDF) → แบ่งตามมาตรา/ข้อ (ด้วย splitter ใหม่) → ingest เข้า collection `recent_law`
*   **Deduplicate:** hash (title + วันประกาศ) ใน SQLite กัน ingest ซ้ำ
*   รันครั้งเดียว: `python src/crawl_ratchakitcha.py` | รันต่อเนื่องทุกสัปดาห์: `python src/crawl_ratchakitcha.py --weekly`

### 6. Generation & Evaluation (คงเดิม)
*   **Chain-of-Thought + Strict Polarity:** ยก quote ก่อนแล้ว "สรุป:" ฟันธงชัดเจน
*   **LLM-as-a-Judge:** `src/evaluate.py` ใช้ประเมิน accuracy ก่อน/หลังปรับระบบ

---

## การติดตั้งและรัน (Quick Start)

```bash
pip install -r requirements.txt
cp .env_example .env          # แล้วแก้ค่าตามที่ใช้ (LLM provider, LINE token ฯลฯ)
ollama pull llama3.2          # ถ้าใช้ LLM local

# 1) Ingest ฐานกฎหมาย (ครั้งแรก)
python src/ingest_core.py
python src/ingest_recent.py

# 2) รันแชทบอท — เลือกช่องทางที่ต้องการ
python src/server.py                    # Web + LINE + OpenClaw ใน process เดียว
python src/discord_bot.py               # Discord (แยก process)
python src/main.py                      # CLI แบบหลายเทิร์น

# 3) (ทางเลือก) อัปเดตกฎหมายใหม่จากราชกิจจานุเบกษา
python src/crawl_ratchakitcha.py            # ครั้งเดียว
python src/crawl_ratchakitcha.py --weekly   # ทุกสัปดาห์

# 4) (ทางเลือก) ทดสอบ splitter กัน truncation หัวมาตรา
python src/test_section_splitter.py
```

### ตั้งค่า LINE Messaging API
1. สร้าง channel ที่ https://developers.line.biz/console/ (Messaging API)
2. คัดลอก `LINE_CHANNEL_ACCESS_TOKEN` + `LINE_CHANNEL_SECRET` ใส่ `.env`
3. รัน server แล้วตั้ง Webhook URL ใน console: `https://<your-domain>/line/webhook`
4. ทดสอบ: `curl https://<your-domain>/line/health` ต้องได้ `{"ok": true, "configured": true}`

### ตั้งค่า Discord Bot
1. สร้าง Application ที่ https://discord.com/developers/applications → Bot → copy token
2. ใส่ `DISCORD_BOT_TOKEN` ใน `.env` + เปิด **Message Content Intent**
3. เชิญบอทด้วย OAuth2 URL (scopes: `bot` + `applications.commands`)
4. รัน `python src/discord_bot.py` — ใช้ได้ทั้ง `!ask`, mention, และ slash `/law`

### การเชื่อม OpenClaw (เปิดช่องทาง Telegram/WhatsApp ฯลฯ)
1. รัน server: `python src/server.py`
2. ใน config ของ OpenClaw gateway ชี้ webhook ไปที่ `POST http://<host>:8000/openclaw/webhook` ส่ง `{chat_id, text}`
3. (แนะนำ) ตั้ง `OPENCLAW_SECRET=...` ทั้งสองฝั่งเพื่อยืนยันตัวตน

### การประเมินผล
```bash
python src/evaluate.py   # วัด accuracy เทียบกับ dataset ข้อสอบ (WangchanX-Legal-ThaiCCL-RAG)
```

---

## Docker & Cloud Deploy

```bash
# Build + รัน local (mount chroma_db เข้า container)
docker build -t thai-law-chatbot .
docker run -p 8000:8000 --env-file .env -v $(pwd)/chroma_db:/app/chroma_db thai-law-chatbot
```

Deploy ขึ้น Google Cloud Run (build, secrets, Cloud Scheduler สำหรับ crawler, Discord บน VM) — ดูคำสั่งฉบับเต็มใน **`gcp_deploy.txt`**

---

## เทคโนโลยีที่ใช้งาน (Tech Stack)

*   **LangGraph:** สมองของ agent — StateGraph, multi-turn memory (SqliteSaver), intent routing, doc grading
*   **LangChain:** Data loaders, prompts, output parsers และ hybrid retriever
*   **ChromaDB:** Vector Database แบบ Offline (Local) สอง collection: `core_law` + `recent_law`
*   **LLM:** Ollama (local) หรือ OpenAI / Anthropic / Google / OpenRouter (cloud) — สลับได้ผ่าน `.env`
*   **HuggingFace Embeddings:** multilingual MiniLM สำหรับ semantic search
*   **PyThaiNLP:** ตัดคำไทยสำหรับ BM25
*   **FastAPI + Vanilla JS:** Web Chat UI + API
*   **LINE Messaging API / discord.py / OpenClaw:** ช่องทางแชทภายนอก
*   **PyMuPDF + requests:** Ratchakitcha crawler
*   **Docker + Cloud Run:** deploy บน cloud

---

## แหล่งข้อมูล (References & Datasets)

อ้างอิงชุดข้อมูลกฎหมายไทยจากการเผยแพร่โดย Open Law Data Thailand:

1.  **ocs-krisdika:** ชุดข้อมูลกฎหมายจากสำนักงานคณะกรรมการกฤษฎีกา
2.  **iapp_2025:** ชุดข้อมูลกฎหมายประกาศใหม่ประมวลผลผ่าน OCR
3.  **WangchanX-Legal-ThaiCCL-RAG:** ชุดข้อมูลสำหรับการประเมินระบบ RAG กฎหมายไทยโดยศูนย์วิจัย AIResearch
4.  **ราชกิจจานุเบกษา Web Service (api.soc.go.th):** แหล่งดึงกฎหมายใหม่อัตโนมัติผ่าน crawler
