# Thai Legal RAG Chatbot

โปรเจกต์นี้เป็น **แชทบอทตอบคำถามกฎหมายไทยแบบหลายช่องทาง (Multi-channel)** — Web, LINE, Discord, OpenClaw และ CLI — คุยได้หลายเทิร์น (จำ context ต่อเนื่อง), ตอบพร้อมอ้างอิงมาตรา/ชื่อกฎหมาย, รองรับ **LLM ทั้ง local (Ollama) และ cloud (OpenAI/Anthropic/Google/OpenRouter/UNOROUTER/Groq/Mistral) พร้อม failover chain อัตโนมัติ**, **vector store เลือกได้ระหว่าง ChromaDB (local) กับ Pinecone (cloud)** และ **อัปเดตกฎหมายใหม่จากราชกิจจานุเบกษาอัตโนมัติทุกสัปดาห์**

Live: https://rag-law-226111202600.asia-southeast1.run.app (Cloud Run, project `rag-law-509304`)

---

## สถาปัตยกรรมระบบ (System Architecture)

```
ผู้ใช้ ──> Web Chat UI (FastAPI /) ─────────┐
       ──> LINE Messaging API (/line/webhook) ├─> runtime (shared agent) ──> LangGraph Agent
       ──> OpenClaw Gateway (/openclaw/webhook)┘            │                        │
       ──> Discord Bot (แยก process) ───────────────────────┤                        │
       ──> CLI (python src/main.py) ────────────────────────┘                        ▼
                                              Retriever (Hybrid RRF: Vector + Thai BM25)
                                                                              │
                                            Vector Store: ChromaDB (local disk) หรือ Pinecone (cloud)
                                                       │                        ▲
                                                       ▼                        │
                                                 LLMClient (failover chain)     │
                                                       │                  Ratchakitcha Crawler
                                                       ▼                  (อัตโนมัติทุกสัปดาห์)
                                     UNOROUTER / OpenRouter / Groq / Ollama ฯลฯ

ระบบเสริม: Firestore (rag-law-db: ประวัติแชท) · BigQuery (rag_law_query: analytics) · GCS (chroma_db snapshot)
```

### 1. LangGraph Conversation Agent (`src/agent.py`) — สมองของแชทบอท
StateGraph 4 ขั้นตอน:

*   **Route Intent:** แยกว่าเป็น "คำถามกฎหมาย" (→ ค้นฐานกฎหมาย) หรือ "คุยทั่วไป" (→ ตอบแบบแชทสุภาพ ไม่ต้องค้น)
*   **Query Rewrite:** คำถามเทิร์นต่อๆ มา (เช่น "แล้วมาตราถัดไปล่ะ") ถูก LLM รวมกับประวัติแชทให้เป็นคำถามสมบูรณ์ก่อนค้น
*   **Hybrid Retrieval + Doc Grading:** ดึงเอกสารด้วย Vector + BM25 + RRF แล้วให้ LLM ตรวจก่อนว่าเนื้อหาตอบได้จริงไหม — ถ้าไม่ ตอบอย่างสุภาพว่าไม่พบข้อมูล (ลด hallucination)
*   **Multi-turn Memory:** เก็บประวัติต่อ session ด้วย `SqliteSaver` (LangGraph checkpointer) + **บันทึกซ้ำลง Firestore** ไว้ตรวจสอบย้อนหลัง

### 2. Multi-channel Chat (ทุกช่องทางใช้ agent ตัวเดียวกันผ่าน `src/runtime.py`)

| ช่องทาง | ไฟล์ | วิธีรัน | หมายเหตุ |
|---|---|---|---|
| **Web Chat** | `src/server.py` + `src/web/index.html` | `python src/server.py` | UI ปรับแต่งได้: **ภาษาไทย/อังกฤษ (สลับป้ายทั้งหน้า)**, **รูปโปรไฟล์บอท/ชื่อบอท**, มาสคอต, ฟอนต์ 6 แบบ, ธีมมืด/สว่าง, layout แชท/เอกสาร, การ์ดอ้างอิงคลิกได้ |
| **LINE** | `src/line_bot.py` | mount ใน server อัตโนมัติ | ตอบ 200 ทันที + ประมวลผล background (LINE timeout 2 วิ), Reply API ฟรี + Push fallback, ตรวจ `X-Line-Signature` |
| **Discord** | `src/discord_bot.py` | `python src/discord_bot.py` (แยก process) | `!ask <คำถาม>`, mention, slash `/law` — ไม่ใช้ webhook (gateway websocket); บน Cloud Run รันเป็น service ที่ 2 พร้อม `DISCORD_HEALTH_PORT` |
| **OpenClaw** | `src/openclaw_bridge.py` | mount ใน server อัตโนมัติ | webhook + secret auth |
| **CLI** | `src/main.py` | `python src/main.py` | คำสั่ง `reset`, `exit` |

### 3. LLM หลาย Provider + Failover Chain (`src/llm_client.py`)

ตั้ง `LLM_PROVIDER` เป็น provider หลัก แล้วกำหนด `LLM_FAILOVER_CHAIN` — **ถ้าตัวหลัก quota หมด/ล่ม จะส่งต่อ prompt+context เดิมให้ตัวถัดไปทันที** (provider ที่ไม่มี key ถูกข้ามอัตโนมัติ):

```
LLM_FAILOVER_CHAIN=groq,unorouter,mistral,ollama
```

Provider ที่ตอบ deterministic error (invalid key / model ไม่มี / 404) ถูกข้ามทันทีไม่ retry, ส่วน error ชั่วคราว (rate limit / timeout) จะ retry 1 ครั้งด้วย backoff ก่อน failover

| Provider | env | หมายเหตุ |
|---|---|---|
| **Groq** (แนะนำ primary) | `LLM_PROVIDER=groq` + `GROQ_API` | เร็วมาก ~500 tok/s, `GROQ_MODEL=openai/gpt-oss-120b` (llama-3.3-70b ปิด free tier แล้ว) |
| **OpenRouter** | `LLM_PROVIDER=openrouter` + `OPENROUTER_API_KEY` | มีโมเดล `:free`, ตัวใหญ่น่าเชื่อถือ |
| **UNOROUTER** | `LLM_PROVIDER=unorouter` + `UNOROUTER_API_KEY` | OpenAI-compatible ฟรี, ต้องใช้ `https://api.unorouter.com/v1` + model `k2-horizon:free` |
| **Gateway ทั่วไป** | `LLM_PROVIDER=gateway` + `GATEWAY_BASE_URL/_API_KEY/_MODEL` | ใช้ได้ทุกเจ้ามาตรฐาน OpenAI (9router ฯลฯ) |
| **Groq / Mistral / Cloudflare** | `GROQ_API` / `MISTRAL_API` / `CLOUDFLARE_API` | free tier |
| **OpenAI / Anthropic / Google** | `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY` | Gemini ฟรีต้องผูก billing (ปิดไว้ก่อนใน config) |
| **Ollama (local)** | `LLM_PROVIDER=ollama` + `LLM_MODEL_NAME` | หรือผ่าน ngrok — ดู `docs/ngrok_local_llm.txt` |

### 4. Vector Store เลือกได้ (`VECTOR_STORE`) — ChromaDB หรือ Pinecone

| โหมด | การทำงาน | เหมาะกับ |
|---|---|---|
| **`chroma`** (default) | อ่านจาก local disk — บน Cloud Run จะ **rsync จาก GCS เป็น background** หลัง bind port (`src/db_sync.py`), คำถามแรกรอ sync เสร็จ (สูงสุด 5 นาที) | local dev / คุ้ม cost |
| **`pinecone`** | cloud vector DB (index `thai-law`, namespace `core_law`/`recent_law`) — **ไม่ต้องโหลดอะไรเลยตอน startup → ไม่มี cold start download** | Cloud Run / production |

Migration chroma → Pinecone: `python scripts/migrate_chroma_to_pinecone.py` (มี `--dry-run`, dimension 384, metric cosine)

⚠️ **Pinecone Starter (free) มีโควตารายเดือน:** Write Units 2M/เดือน · Read Units 1M/เดือน (reset ทุกวันที่ 1) — migration ครั้งเดียวใช้ ~1.5M WU ควรรันต้นเดือน และ upsert ซ้ำด้วย ID เดิม = idempotent ทับข้อมูลเก่าได้เลย (ไม่ต้องลบ index ก่อน — ลบข้อมูลไม่ได้คืนโควตา WU ที่ใช้ไปแล้ว) เช็คเหลือเท่าไรที่ Pinecone Console → Usage

### 5. Data Ingestion (การนำเข้าข้อมูล)
*   **Thai Law Section Splitter (`src/thai_law_splitter.py`):** จับหัวมาตราครบทุกรูปแบบ (`มาตรา 5`, `มาตราที่ 7/1`, `มาตรา ๓๙` เลขไทย, `ข้อที่ 5`, ทวิ/ตรี/จัตวา) ไม่ตัดกลางคำ — chunk ต่อเนื่องมี header "ส่วนของ: มาตรา X (ส่วนที่ n/m)" ตรวจด้วย `python src/test_section_splitter.py`
*   **Hierarchical Metadata:** `title`, `section_id`, `hierarchy_level`, `publish_date` ในทุก chunk + **Prefix Context Injection** ชื่อกฎหมาย+มาตราแปะหัวทุก chunk
*   **Generic Law Detection:** `detect_target_law` ตรวจชื่อกฎหมายจากคำถามด้วย regex รองรับทุกฉบับ

### 6. Ratchakitcha Crawler (`src/crawl_ratchakitcha.py`) — อัปเดตกฎหมายใหม่อัตโนมัติ
*   **`hf` (default):** Open Law Data Thailand บน Hugging Face — ไม่ต้อง token
*   **`api`:** `api.soc.go.th` ทางการ — ต้องสมัคร Token (`RATCHAKITCHA_TOKEN`)

Pipeline: PDF → PyMuPDF → splitter → ingest `recent_law` + dedupe ด้วย hash (title + วันประกาศ)

```bash
python src/crawl_ratchakitcha.py --weekly   # รันต่อเนื่องทุกสัปดาห์
```

### 7. User Data & Analytics
*   **Firestore (`rag-law-db`):** ประวัติแชททุกเทิร์น → `chat_sessions/<session_id>/turns` (`src/firestore_db.py`), `/reset` ลบประวัติด้วย — fail-safe: ถ้าใช้ไม่ได้ระบบยังตอบได้ปกติ
*   **BigQuery (`rag_law_query.queries`):** log คำถาม/คำตอบไว้วิเคราะห์คำถามยอดนิยม (`src/analytics.py`)

### 8. Generation & Evaluation
*   **Chain-of-Thought + Strict Polarity:** ยก quote ก่อนแล้ว "สรุป:" ฟันธงชัดเจน
*   **Expert Legal Prompt:** ตอบเชิงนักกฎหมาย — ระบุ "มาตรา + ว่าด้วยเรื่องอะไร + เพราะอะไร" (เชื่อมข้อเท็จจริงกับทุกประการขององค์ประกอบความผิด/สิทธิ) และ **ห้ามมั่วเลขมาตรา** — อ้างเฉพาะมาตราที่อยู่ใน Context เท่านั้น ถ้าข้อมูลไม่พอให้บอกตรงๆ ว่า "ข้อมูลไม่เพียงพอ" พร้อมแนะนำกฎหมายที่น่าจะเกี่ยว
*   **LLM-as-a-Judge:** `src/evaluate.py` วัด accuracy เทียบ dataset (WangchanX-Legal-ThaiCCL-RAG)

---

## การติดตั้งและรัน (Quick Start)

```bash
pip install -r requirements.txt
cp .env_example .env          # แล้วแก้ค่าตามที่ใช้ (LLM provider, LINE token ฯลฯ)
ollama pull llama3.2          # ถ้าใช้ LLM local

# 1) Ingest ฐานกฎหมาย (ครั้งแรก)
python src/ingest_core.py
python src/ingest_recent.py

# 2) (ทางเลือก) migrate ขึ้น Pinecone แล้วตั้ง VECTOR_STORE=pinecone ใน .env
pip install pinecone langchain-pinecone
python scripts/migrate_chroma_to_pinecone.py --dry-run
python scripts/migrate_chroma_to_pinecone.py

# 3) รันแชทบอท
python src/server.py                    # Web + LINE + OpenClaw ใน process เดียว
python src/discord_bot.py               # Discord (แยก process)
python src/main.py                      # CLI แบบหลายเทิร์น

# 4) (ทางเลือก) อัปเดตกฎหมายใหม่ / ทดสอบ / ประเมิน
python src/crawl_ratchakitcha.py --weekly
python src/test_section_splitter.py
python src/test_local.py --quick        # dev test ด้วย Ollama ไม่ต้องมี cloud key
python src/evaluate.py                  # วัด accuracy
```

### ตั้งค่า LINE Messaging API
1. สร้าง channel ที่ https://developers.line.biz/console/ (Messaging API)
2. ใส่ `LINE_CHANNEL_ACCESS_TOKEN` + `LINE_CHANNEL_SECRET` ใน `.env` (หรือ secret `env_law`)
3. Webhook URL: `https://<your-domain>/line/webhook` → Verify → Enable
4. **ปิด** auto-reply/greeting ของ Official Account และอย่าเปิด "Use grouped responses"
5. ทดสอบ: `curl https://<your-domain>/line/health` → `{"ok": true, "configured": true}`

อัปเดต env บน Cloud Run ให้มีผลทันทีโดยไม่ต้อง build image ใหม่:

```bash
gcloud run services update rag-law --region asia-southeast1 --project rag-law-509304 \
  --update-env-vars "LINE_CHANNEL_ACCESS_TOKEN=xxx,LINE_CHANNEL_SECRET=yyy"
```

หมายเหตุ: ถ้า service ใช้ `min-instances=0` การกด Verify ใน LINE Console ครั้งแรกอาจ timeout (cold start) — กดซ้ำอีกครั้งได้

### ตั้งค่า Discord Bot
1. สร้าง Application ที่ https://discord.com/developers/applications → Bot → copy token
2. ใส่ `DISCORD_BOT_TOKEN` ใน `.env` + เปิด **Message Content Intent**
3. เชิญบอทด้วย OAuth2 URL (scopes: `bot` + `applications.commands`)
4. รัน `python src/discord_bot.py` — ใช้ได้ทั้ง `!ask`, mention, และ slash `/law`
5. (ทางเลือก) รันบน Cloud Run เป็น service ที่ 2 — บอทต้องออนไลน์ตลอด จึงต้อง `--min-instances 1 --no-cpu-throttling`:

```bash
gcloud run deploy rag-law-discord --source . --region asia-southeast1 --project rag-law-509304 \
  --command python --args src/discord_bot.py \
  --min-instances 1 --max-instances 1 --no-cpu-throttling \
  --memory 2Gi --cpu 1 --port 8080 \
  --set-env-vars "DISCORD_HEALTH_PORT=8080,VECTOR_STORE=pinecone,LLM_PROVIDER=groq" \
  --set-secrets "ENV_LAW=env_law:latest" --allow-unauthenticated
```

`DISCORD_HEALTH_PORT` ทำให้บอทเปิด HTTP port ตอบ 200 ให้ Cloud Run (ไม่ตั้ง = ไม่เปิด port เลย เหมาะกับการรัน local)

### การเชื่อม OpenClaw (Telegram/WhatsApp ฯลฯ)
1. รัน server: `python src/server.py`
2. ชี้ webhook ของ OpenClaw gateway ไปที่ `POST http://<host>:8000/openclaw/webhook` ส่ง `{chat_id, text}`
3. (แนะนำ) ตั้ง `OPENCLAW_SECRET=...` ทั้งสองฝั่ง

### จะเพิ่มช่องทาง LINE / Discord — ต้องแตะอะไรบ้าง

**ไม่ต้องสร้าง endpoint ใหม่** — มีอยู่แล้วทั้งหมด: `/line/webhook` ถูก mount อัตโนมัติใน `src/server.py` (ตรวจ `X-Line-Signature` + ตอบ 200 ทันทีแล้วประมวลผล LLM ใน background เพราะ LINE timeout แค่ 2 วิ) ส่วน Discord **ไม่ใช้ webhook เลย** — ใช้ gateway websocket จึงรันเป็น process แยก

| ต้องเพิ่ม | LINE | Discord |
|---|---|---|
| env var | `LINE_CHANNEL_ACCESS_TOKEN` + `LINE_CHANNEL_SECRET` | `DISCORD_BOT_TOKEN` |
| ฝั่งผู้ให้บริการ | Webhook URL = `https://<domain>/line/webhook` → Verify → Enable | เปิด Message Content Intent + เชิญบอทด้วย OAuth2 URL |
| วิธีรัน | process เดียวกับเว็บ (ไม่ต้องทำอะไรเพิ่ม) | แยก process: `python src/discord_bot.py` |
| ตรวจสอบ | `GET /line/health` | log `[discord_bot] logged in as ...` |
| ข้อควรรู้ | reply token อายุ ~30 วิ — ถ้า LLM ช้ากว่านั้นจะ fallback ไป Push API (นับ quota) | ต้องออนไลน์ตลอด — Cloud Run ต้อง `--min-instances 1 --no-cpu-throttling` (มีค่าใช้จ่ายรายเดือน) |

คำสั่ง Cloud Run ของทั้งสองช่องทางอยู่ใน **`gcp_deploy_gcs.txt`** (ท้ายไฟล์)

### ปรับโปรไฟล์บอทบนเว็บ (รูป + ชื่อ)
*   **เฉพาะเบราว์เซอร์ตัวเอง:** ⚙️ ตั้งค่า → **โปรไฟล์ผู้ช่วย** → 📷 เลือกรูปของฉัน (ย่อเป็น 256px เก็บใน `localStorage`, ใช้เป็น favicon ด้วย) · ช่องชื่อผู้ช่วยแก้ได้ทันที · ↺ กลับไปใช้มาสคอต
*   **ให้ทุกคนเห็นรูปเดียวกัน:** วางไฟล์ `src/web/bot-avatar.jpg` (หรือ `.jpeg`/`.png`/`.webp`) แล้ว deploy ใหม่ → เสิร์ฟที่ `GET /bot-avatar.jpg` แบบเดียวกัน (ถ้าไม่มีไฟล์จะ 404 แล้วหน้าเว็บถอยไปใช้มาสคอต emoji อัตโนมัติ)
*   แนะนำรูปสี่เหลี่ยมจัตุรัส ≥ 512×512 (ระบบครอปกลาง + ย่อให้เองอยู่แล้ว — ไฟล์ default ปัจจุบัน 512×512 ~40KB)
*   **สลับภาษา UI (ไทย / English):** ⚙️ ตั้งค่า → ภาษา — แปลป้ายทั้งหมด (สถานะ, ปุ่ม, แผนภาพเส้นทางการวิเคราะห์, การ์ดอ้างอิง, คำถามแนะนำ, favicon title) · ชื่อบอทจะเปลี่ยนตามภาษาเมื่อยังไม่ได้ตั้งชื่อเอง · ค่าเริ่มต้นคือไทย (ผู้ใช้ที่เคยบันทึกค่าจะได้ค่าเดิม)
*   *หมายเหตุ:* การสลับ UI ไม่ได้เปลี่ยน **ภาษาของคำตอบ** — prompt ฝั่งเซิร์ฟเวอร์ยังสั่งให้ตอบอ้างมาตราเป็นไทยเสมอ

---

## Docker & Cloud Deploy

```bash
# Build + รัน local (mount chroma_db เข้า container)
docker build -t thai-law-chatbot .
docker run -p 8080:8080 --env-file .env -v $(pwd)/chroma_db:/app/chroma_db thai-law-chatbot
```

Deploy บน Google Cloud Run:
*   **โหมด chroma:** chroma_db (~5GB) ถูก rsync จาก GCS (`gs://chroma-db-law`) เป็น background หลัง bind port → health check ผ่านทันที, ใช้ `--memory 4Gi` (embedding model + index กิน RAM)
*   **โหมด pinecone:** ไม่ต้อง sync อะไร ใช้ `--memory 2Gi` พอ (RAM หลักคือ embedding model PyTorch)
*   **ห้ามใช้ default 512Mi** — อาการ: `POST /chat` → 503 + "Memory limit exceeded" ใน log
*   env ทั้งหมดอัปเป็นก้อนเดียวใน Secret Manager (`env_law`) → container รับเป็น `ENV_LAW` แล้ว parse ใน `src/config.py`

คำสั่งฉบับเต็ม: **`gcp_deploy_gcs.txt`** | Local LLM ผ่าน ngrok: **`docs/ngrok_local_llm.txt`**

---

## เทคโนโลยีที่ใช้งาน (Tech Stack)

*   **LangGraph:** สมองของ agent — StateGraph, multi-turn memory (SqliteSaver), intent routing, doc grading
*   **LangChain:** loaders, prompts, output parsers, hybrid retriever, `langchain-pinecone`
*   **Vector Store:** ChromaDB (local) หรือ Pinecone (cloud serverless) — สลับด้วย `VECTOR_STORE`
*   **LLM:** failover chain หลาย provider — OpenRouter / UNOROUTER / Groq / Mistral / OpenAI / Anthropic / Ollama — สลับได้ผ่าน env ไม่แก้โค้ด
*   **HuggingFace Embeddings:** multilingual MiniLM (384 dim) สำหรับ semantic search
*   **PyThaiNLP:** ตัดคำไทยสำหรับ BM25
*   **FastAPI + Vanilla JS:** Web Chat UI ปรับแต่งได้ (ธีม/ฟอนต์/มาสคอต/layout) + API
*   **LINE Messaging API / discord.py / OpenClaw:** ช่องทางแชทภายนอก
*   **Google Cloud:** Cloud Run (deploy) · Secret Manager (env_law) · Firestore `rag-law-db` (ประวัติแชท) · BigQuery `rag_law_query` (analytics) · GCS `chroma-db-law` (snapshot) · Cloud Build (CI)
*   **PyMuPDF + requests:** Ratchakitcha crawler
*   **Docker + gcloud CLI:** containerization และ deploy

---

## แหล่งข้อมูล (References & Datasets)

อ้างอิงชุดข้อมูลกฎหมายไทยจากการเผยแพร่โดย Open Law Data Thailand:

1.  **ocs-krisdika:** ชุดข้อมูลกฎหมายจากสำนักงานคณะกรรมการกฤษฎีกา
2.  **iapp_2025:** ชุดข้อมูลกฎหมายประกาศใหม่ประมวลผลผ่าน OCR
3.  **WangchanX-Legal-ThaiCCL-RAG:** ชุดข้อมูลสำหรับการประเมินระบบ RAG กฎหมายไทยโดยศูนย์วิจัย AIResearch
4.  **ราชกิจจานุเบกษา Web Service (api.soc.go.th):** แหล่งดึงกฎหมายใหม่อัตโนมัติผ่าน crawler
