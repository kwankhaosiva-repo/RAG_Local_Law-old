# Thai Legal RAG Chatbot — Presentation Brief

> **แชทบอทที่ปรึกษากฎหมายไทย อัจฉริยะ หลายช่องทาง** — ตอบพร้อมอ้างอิงมาตราจริงจากฐานกฎหมาย โดยยึดเนื้อหากฎหมายเท่านั้น ไม่เดา ไม่ hallucinate

---

## 1. ปัญหา (Problem)

- ประชาชนทั่วไปเข้าถึงคำปรึกษากฎหมายเบื้องต้นได้ยากและแพง
- กฎหมายไทยมีปริมาณมหาศาลและแก้ไขบ่อย — LLM ทั่วไปตอบจากความรู้เดิม เสี่ยง hallucinate เลขมาตรา/เนื้อหาล้าสมัย
- Keyword search ทั่วไปไม่เข้าใจภาษาธรรมชาติ (โดยเฉพาะภาษาไทยที่ไม่มีการเว้นวรรคคำ)

## 2. ทางออก (Solution)

RAG (Retrieval-Augmented Generation) เฉพาะทางกฎหมายไทย:

1. **ค้นกฎหมายจริงก่อนตอบเสมอ** — Hybrid Search: Semantic (Vector) + Keyword (Thai BM25) รวมกันด้วย RRF
2. **ตรวจคุณภาพก่อนตอบ** — LLM ให้คะแนนเอกสารที่ค้นได้ ถ้าไม่เกี่ยวพอ จะตอบตรงๆ ว่าไม่พบข้อมูล (ไม่เดา)
3. **อ้างอิงโปร่งใส** — ทุกคำตอบระบุชื่อกฎหมาย + มาตรา + ลิงก์เอกสารต้นทาง
4. **อัปเดตอัตโนมัติ** — Crawler ดึงประกาศใหม่จากราชกิจจานุเบกษาทุกสัปดาห์

## 3. จุดเด่นเชิงเทคนิค (Key Technical Highlights)

| จุดเด่น | รายละเอียด |
|---|---|
| **LangGraph Agent** | StateGraph 4 ขั้น: intent routing → query rewrite (multi-turn) → hybrid retrieval + grading → generation |
| **Hybrid Retrieval RRF** | Vector (semantic) + BM25 (ตัดคำไทยด้วย PyThaiNLP) รวมคะแนนด้วย Reciprocal Rank Fusion |
| **Multi-LLM Failover** | ต่อ LLM หลายเจ้าเรียงเป็น chain — ตัวหลัก quota หมด/ล่ม ส่งต่อ prompt+context ให้ตัวถัดไปทันที ไม่มี downtime ไม่แก้โค้ด |
| **Dual Vector Store** | ChromaDB (local, คุ้ม cost) ↔ Pinecone (cloud, ไม่มี cold start) — สลับด้วย env var เดียว |
| **Thai Law Splitter** | ตัด chunk ตามมาตรา/ข้อ ครบทุกรูปแบบ (เลขไทย, มาตราที่ 7/1, ทวิ/ตรี/จัตวา) ไม่ตัดกลางคำ — แก้ truncation หัวมาตรา |
| **Multi-channel** | Web, LINE (ตอบ 200 ทันที + background processing ตัด LINE timeout), Discord, OpenClaw, CLI — agent ตัวเดียวทุกช่องทาง |
| **User Data on GCP** | Firestore เก็บประวัติแชท · BigQuery เก็บ analytics คำถาม — fail-safe ทั้งคู่ |
| **Customizable UI** | เลือกมาสคอต/ฟอนต์ไทย 6 แบบ/ธีมมืด-สว่าง/layout — จำค่าใน localStorage |

## 4. Tech Stack

### AI / RAG Core
- **LangGraph** — conversation agent (StateGraph, multi-turn memory, doc grading)
- **LangChain** — retrieval pipeline, prompts, output parsers
- **LLM Providers (failover chain)** — OpenRouter · UNOROUTER · Groq · Mistral · OpenAI · Anthropic · Ollama (local)
- **HuggingFace Embeddings** — paraphrase-multilingual-MiniLM-L12-v2 (384 dim, รองรับไทย)

### Data & Storage
- **Pinecone / ChromaDB** — vector store (เลือกได้ผ่าน `VECTOR_STORE`)
- **Firestore (rag-law-db)** — ประวัติแชทต่อ session
- **BigQuery (rag_law_query)** — query analytics
- **Google Cloud Storage** — chroma_db snapshot + ข้อมูลกฎหมาย
- **PyThaiNLP** — Thai word segmentation สำหรับ BM25

### Application & Channels
- **FastAPI + Vanilla JS** — Web API + customizable chat UI
- **LINE Messaging API** — webhook, Reply API (ฟรี) + Push fallback, signature verification
- **discord.py** — Discord bot (`!ask`, slash commands)
- **PyMuPDF + requests** — Ratchakitcha crawler (Open Law Data Thailand HF / api.soc.go.th)

### Infrastructure
- **Google Cloud Run** — serverless deploy (auto-scaling, min-instances)
- **Docker + gcloud CLI** — containerization
- **Secret Manager** — env เป็นก้อนเดียว (`env_law`) พร้อม parser ใน app
- **Cloud Build / gcloud** — CI/deploy pipeline

## 5. สถาปัตยกรรม (1 ภาพ)

```
        Web / LINE / Discord / OpenClaw / CLI
                        │
                   FastAPI (Cloud Run)
                        │
                  LangGraph Agent
         ┌──────────────┼──────────────┐
   Route Intent   Rewrite Query   Grade Docs
         └──────────────┼──────────────┘
                        ▼
        Hybrid Retriever (Vector + Thai BM25 + RRF)
                        │
          Pinecone (cloud) หรือ ChromaDB (local)
                        ▼
        LLM Failover Chain → OpenRouter → UNOROUTER → Groq → ...
                        ▼
          คำตอบ + อ้างอิงมาตรา + ลิงก์เอกสารต้นทาง

   เสริม: Firestore (ประวัติ) · BigQuery (analytics)
          Ratchakitcha Crawler (อัปเดตกฎหมายทุกสัปดาห์)
```

## 6. ผลลัพธ์ที่ได้ (Outcomes)

- ตอบคำถามกฎหมายไทยแบบ multi-turn พร้อมอ้างอิงมาตราที่ตรวจสอบย้อนกลับได้
- ความพร้อมใช้งานสูงจาก failover chain — ผู้ใช้ไม่เจอ error จาก quota หมด
- ต้นทุนควบคุมได้: free-tier LLM เป็นหลัก + เลือก vector store ตามงบ
- ฐานกฎหมายสดใหม่เสมอด้วย crawler ราชกิจจานุเบกษาอัตโนมัติ

## 7. Next Steps (แผนต่อยอด)

- ประเมิน accuracy เชิงระบบด้วย dataset WangchanX-Legal-ThaiCCL-RAG เป็นระยะ
- ขยายช่องทาง: Telegram / WhatsApp ผ่าน OpenClaw gateway
- ยกระดับ reranking ด้วย cross-encoder เฉพาะกฎหมายไทย
- Dashboard วิเคราะห์คำถามยอดนิยมจาก BigQuery เพื่อหาช่องว่างฐานกฎหมาย
