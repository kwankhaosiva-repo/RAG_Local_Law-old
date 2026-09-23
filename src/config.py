import os
from dotenv import load_dotenv

# --- ENV_BLOB from Secret Manager -------------------------------------------
# If the whole .env file was uploaded to Secret Manager (e.g. secret name
# 'env_law') and mapped to a container env var, parse it here so every
# key inside becomes a normal environment variable.
for _env_file_var in ('ENV_FILE', 'ENV_BLOB', 'ENV_LAW', 'env_law'):
    _env_file_content = os.environ.get(_env_file_var, '')
    if _env_file_content and '=' in _env_file_content:
        for _line in _env_file_content.splitlines():
            _line = _line.strip()
            if not _line or _line.startswith('#') or '=' not in _line:
                continue
            if _line.startswith('export '):
                _line = _line[len('export '):].strip()
            _key, _, _val = _line.partition('=')
            _key = _key.strip()
            _val = _val.strip().strip('"').strip("'")
            if _key:
                os.environ.setdefault(_key, _val)
        break

load_dotenv()  # โหลด .env (ถ้ามี)

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
DATASETS_DIR = os.path.join(PROJECT_ROOT, "datasets")
DB_DIR = os.path.join(PROJECT_ROOT, "chroma_db")

# Files
# CORE_LAW is now fetched from Hugging Face "open-law-data-thailand/ocs-krisdika"
RECENT_LAW_DIR = os.path.join(DATASETS_DIR, "iapp_2025")

# Models
# LLM_PROVIDER: "ollama" (local, default) | "openai" | "anthropic" | "google" | "openrouter"
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "ollama")
LLM_MODEL_NAME = os.environ.get("LLM_MODEL_NAME", "llama3.2")  # Ollama model name

# --- Cloud LLM (ใช้เมื่อ LLM_PROVIDER != "ollama") ---
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-3-5-haiku-latest")
# --- Gemini ปิดไว้ก่อน (API key ฟรีต้องผูก billing account จึงจะใช้ได้) ---
# จะกลับมาใช้เมื่อไรผูกบัตร: uncomment + ตั้ง LLM_PROVIDER=google
# GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API", "")
# GOOGLE_MODEL = os.environ.get("GOOGLE_MODEL", "gemini-2.0-flash")
GOOGLE_API_KEY = ""  # disabled — ดู comment ด้านบน
GOOGLE_MODEL = "gemini-2.0-flash"
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENROUTER_API", "")
OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini")
# OpenAI-compatible providers (ใช้ langchain-openai ร่วมกัน)
GROQ_API_KEY = os.environ.get("GROQ_API_KEY") or os.environ.get("GROQ_API", "")
GROQ_BASE_URL = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
MISTRAL_API_KEY = os.environ.get("MISTRAL_API_KEY") or os.environ.get("MISTRAL_API", "")
MISTRAL_BASE_URL = os.environ.get("MISTRAL_BASE_URL", "https://api.mistral.ai/v1")
MISTRAL_MODEL = os.environ.get("MISTRAL_MODEL", "mistral-small-latest")
CLOUDFLARE_API_KEY = os.environ.get("CLOUDFLARE_API_KEY") or os.environ.get("CLOUDFLARE_API", "")
CLOUDFLARE_ACCOUNT_ID = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
CLOUDFLARE_BASE_URL = os.environ.get(
    "CLOUDFLARE_BASE_URL",
    f"https://api.cloudflare.com/client/v4/accounts/{os.environ.get('CLOUDFLARE_ACCOUNT_ID', '')}/ai/v1",
)
CLOUDFLARE_MODEL = os.environ.get("CLOUDFLARE_MODEL", "@cf/meta/llama-3.3-70b-instruct-fp8-fast")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST") or os.environ.get("OLLAMA_API", "http://localhost:11434")

# --- Generic OpenAI-compatible gateway (9router.com ฯลฯ) ---
# ใช้เมื่อ LLM_PROVIDER=gateway — รองรับทุกเจ้าที่ API เป็นมาตรฐาน OpenAI
GATEWAY_BASE_URL = os.environ.get("GATEWAY_BASE_URL", "")
GATEWAY_API_KEY = os.environ.get("GATEWAY_API_KEY", "")
GATEWAY_MODEL = os.environ.get("GATEWAY_MODEL", "gpt-4o-mini")

# --- UNOROUTER (unorouter.com — free OpenAI-compatible API) ---
# key เก็บใน Secret Manager แล้ว (secret env_law)
UNOROUTER_API_KEY = os.environ.get("UNOROUTER_API_KEY", "")
UNOROUTER_BASE_URL = os.environ.get("UNOROUTER_BASE_URL", "https://unorouter.com/v1")
UNOROUTER_MODEL = os.environ.get("UNOROUTER_MODEL", "gpt-4o-mini")

# --- Failover chain: ถ้า LLM หลักล้ม (quota หมด/ล่ม) ส่งต่อ prompt+context เดิม
# ให้ตัวถัดไปทันที — default: unorouter → groq → mistral → ollama (ตามที่มี key)
# (provider ที่ไม่มี key จะถูกข้ามอัตโนมัติ ไม่ต้องแก้ chain เอง)
LLM_FAILOVER_CHAIN = os.environ.get(
    "LLM_FAILOVER_CHAIN", "unorouter,groq,mistral,ollama"
)

EMBEDDING_MODEL_NAME = os.environ.get(
    "EMBEDDING_MODEL_NAME",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
)

# Vector DB
COLLECTION_CORE = "core_law"
COLLECTION_RECENT = "recent_law"

# --- Vector store backend: "chroma" (default, local disk) หรือ "pinecone" (cloud) ---
# pinecone = ไม่ต้อง rsync chroma_db ตอน startup → ไม่มี cold start delay
VECTOR_STORE = os.environ.get("VECTOR_STORE", "chroma")
PINECONE_API_KEY = os.environ.get("PINECONE_KEY") or os.environ.get("PINECONE_API_KEY", "")
PINECONE_INDEX = os.environ.get("PINECONE_INDEX", "thai-law")
PINECONE_NAMESPACE_CORE = os.environ.get("PINECONE_NAMESPACE_CORE", "core_law")
PINECONE_NAMESPACE_RECENT = os.environ.get("PINECONE_NAMESPACE_RECENT", "recent_law")

# Retrieval
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200
RETRIEVAL_K = 5

# --- Conversation Agent / Sessions ---
SESSION_DB_PATH = os.path.join(PROJECT_ROOT, "chroma_db", "sessions.sqlite")
CHAT_HISTORY_LIMIT = 12  # max messages sent to the LLM as context

# --- FastAPI Web Chat ---
API_HOST = os.environ.get("API_HOST", "0.0.0.0")
API_PORT = int(os.environ.get("PORT", "8080"))

# --- OpenClaw Gateway Bridge ---
OPENCLAW_WEBHOOK_PATH = "/openclaw/webhook"
# Optional shared secret; set the OPENCLAW_SECRET env var to require it in the X-OpenClaw-Secret header
OPENCLAW_SECRET = os.environ.get("OPENCLAW_SECRET", "")

# --- Ratchakitcha Crawler ---
# แหล่งดึงกฎหมายใหม่: "hf" (Open Law Data บน Hugging Face — ไม่ต้อง token, แนะนำ)
# หรือ "api" (Web Service ทางการ api.soc.go.th — ต้องสมัคร Token ที่ https://www2.soc.go.th)
RATCHAKITCHA_SOURCE = os.environ.get("RATCHAKITCHA_SOURCE", "hf")
# Dataset meta รายเดือน (jsonl: doctitle, publishDate, source_url ฯลฯ) จากโครงการ Open Law Data Thailand
RATCHAKITCHA_HF_DATASET = os.environ.get("RATCHAKITCHA_HF_DATASET", "open-law-data-thailand/soc-ratchakitcha")
RATCHAKITCHA_HF_MONTHS = int(os.environ.get("RATCHAKITCHA_HF_MONTHS", "1"))  # จำนวนเดือนย้อนหลังที่จะดึง
RATCHAKITCHA_API_URL = "https://api.soc.go.th/webservice/api/rkjs/{page}/{limit}"
RATCHAKITCHA_DOC_URL = "https://ratchakitcha.soc.go.th/documents/{pdf_path}"
# Token จำเป็นเฉพาะเมื่อใช้ source "api" (สมัครที่ https://www2.soc.go.th)
RATCHAKITCHA_TOKEN = os.environ.get("RATCHAKITCHA_TOKEN") or os.environ.get("OPEND", "")
CRAWL_PAGES = 3          # how many pages of announcements to fetch per run
CRAWL_PAGE_SIZE = 50     # items per API page
CRAWL_WEEKLY = False     # True = keep running and repeat weekly (schedule lib)
CRAWL_DIR = os.path.join(DATASETS_DIR, "ratchakitcha")
