import os
from dotenv import load_dotenv

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
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
GOOGLE_MODEL = os.environ.get("GOOGLE_MODEL", "gemini-2.0-flash")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

EMBEDDING_MODEL_NAME = os.environ.get(
    "EMBEDDING_MODEL_NAME",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
)

# Vector DB
COLLECTION_CORE = "core_law"
COLLECTION_RECENT = "recent_law"

# Retrieval
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200
RETRIEVAL_K = 5

# --- Conversation Agent / Sessions ---
SESSION_DB_PATH = os.path.join(PROJECT_ROOT, "chroma_db", "sessions.sqlite")
CHAT_HISTORY_LIMIT = 12  # max messages sent to the LLM as context

# --- FastAPI Web Chat ---
API_HOST = os.environ.get("API_HOST", "0.0.0.0")
API_PORT = int(os.environ.get("PORT", "8000"))

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
RATCHAKITCHA_TOKEN = os.environ.get("RATCHAKITCHA_TOKEN", "")
CRAWL_PAGES = 3          # how many pages of announcements to fetch per run
CRAWL_PAGE_SIZE = 50     # items per API page
CRAWL_WEEKLY = False     # True = keep running and repeat weekly (schedule lib)
CRAWL_DIR = os.path.join(DATASETS_DIR, "ratchakitcha")
