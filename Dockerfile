# ===== Thai Legal RAG Chatbot =====
# Multi-channel: Web chat + LINE webhook + OpenClaw bridge (Discord รันแยก: python src/discord_bot.py)
# Build:  docker build -t thai-law-chatbot .
# Run:    docker run -p 8000:8000 --env-file .env -v $(pwd)/chroma_db:/app/chroma_db thai-law-chatbot

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

# libgomp จำเป็นสำหรับ onnxruntime/sentence-transformers
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    curl \
    apt-transport-https \
    ca-certificates \
    gnupg \
    && curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" > /etc/apt/sources.list.d/google-cloud-sdk.list \
    && apt-get update && apt-get install -y --no-install-recommends google-cloud-cli \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps ก่อนเพื่อ leverage layer cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- ฝัง embedding model เข้า image (HF cache) ---
# ทำไม: ถ้าไม่ฝัง ตอน cold start container ต้องโหลด ~470MB จาก Hugging Face
#       ซึ่งใช้เวลา ~1-2 นาที — นานกว่า reply token ของ LINE (~1 นาที)
#       ทำให้ข้อความแรกของผู้ใช้ตอบไม่ทัน (บอทเงียบ)
#       ฝังไว้ใน image → cold start เหลือแค่ import + โหลดจาก disk (~10-30 วิ)
#       ทำให้ใช้ --min-instances 0 (ไม่เสียเงินช่วง idle) ได้โดยไม่ทรมานผู้ใช้มาก
ARG EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('${EMBEDDING_MODEL}')"

# Copy source
COPY src/ ./src/
COPY docs/ ./docs/
COPY .env_example ./

# chroma_db (vector index) ควร mount เป็น volume ตอนรัน:
#   -v $(pwd)/chroma_db:/app/chroma_db
# หรือจะ COPY เข้าไปใน image ก็ได้ (สะดวกกับ Cloud Run):
# COPY chroma_db/ ./chroma_db/

RUN useradd -m appuser
USER appuser

EXPOSE 8080

# Cloud Run injects $PORT (default 8080)
# server.py จะ bind PORT ทันที แล้ว sync chroma_db จาก GCS แบบ background
# (ถ้า sync ไม่ทัน คำถามแรกจะรอจนกว่า DB พร้อม — ดู src/db_sync.py)
CMD exec uvicorn src.server:app --host 0.0.0.0 --port ${PORT:-8080}
