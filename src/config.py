import os

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
DATASETS_DIR = os.path.join(PROJECT_ROOT, "datasets")
DB_DIR = os.path.join(PROJECT_ROOT, "chroma_db")

# Files
# CORE_LAW is now fetched from Hugging Face "open-law-data-thailand/ocs-krisdika"
RECENT_LAW_DIR = os.path.join(DATASETS_DIR, "iapp_2025")

# Models
LLM_MODEL_NAME = "llama3.2"  # Ollama model name
EMBEDDING_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

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
API_HOST = "0.0.0.0"
API_PORT = 8000

# --- OpenClaw Gateway Bridge ---
OPENCLAW_WEBHOOK_PATH = "/openclaw/webhook"
# Optional shared secret; set the OPENCLAW_SECRET env var to require it in the X-OpenClaw-Secret header
OPENCLAW_SECRET = os.environ.get("OPENCLAW_SECRET", "")

# --- Ratchakitcha Crawler ---
RATCHAKITCHA_API_URL = "https://api.soc.go.th/webservice/api/rkjs/{page}/{limit}"
RATCHAKITCHA_DOC_URL = "https://ratchakitcha.soc.go.th/documents/{pdf_path}"
CRAWL_PAGES = 3          # how many pages of announcements to fetch per run
CRAWL_PAGE_SIZE = 50     # items per API page
CRAWL_WEEKLY = False     # True = keep running and repeat weekly (schedule lib)
CRAWL_DIR = os.path.join(DATASETS_DIR, "ratchakitcha")
