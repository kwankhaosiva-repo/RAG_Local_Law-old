"""
Background GCS → local chroma_db sync (สำหรับ Cloud Run)
---------------------------------------------------------
ปัญหาเดิม: startup script โหลด chroma_db 3.4GB ก่อน start uvicorn
→ Cloud Run รอ PORT ไม่เจอภายใน timeout จึง fail container start

วิธีแก้: start server ทันที (bind port) แล้วค่อย sync ข้อมูล
เป็น background thread หลัง health check ผ่านแล้ว

จนกว่า sync จะเสร็จ retriever ยังไม่สามารถ query ได้ —
db_sync.wait_ready() จะ block จุดที่ต้องใช้ DB จริง (agent chat)
"""
import os
import subprocess
import threading
import time

import config

_lock = threading.Lock()
_ready = threading.Event()
_error: str | None = None
_started = False

BUCKET = os.environ.get("GCS_CHROMA_BUCKET", "gs://chroma-db-law")
PREFIX = os.environ.get("GCS_CHROMA_PREFIX", "chroma_db")
DEST = config.DB_DIR  # ใช้ path เดียวกับ config ทั้งหมด

# ถ้าโฟลเดอร์นี้มีข้อมูลอยู่แล้ว (เช่น COPY ใน image หรือ mount volume) → ข้าม sync
_MARKER_FILES = ("chroma.sqlite3", "sessions.sqlite")


def _db_looks_present() -> bool:
    return os.path.isfile(os.path.join(DEST, "chroma.sqlite3"))


def _gcloud_available() -> bool:
    try:
        subprocess.run(
            ["gcloud", "--version"], capture_output=True, check=True, timeout=30
        )
        return True
    except Exception:
        return False


def _sync_once() -> None:
    global _error
    os.makedirs(DEST, exist_ok=True)
    print(f"[db_sync] Syncing {BUCKET}/{PREFIX} -> {DEST} ...")
    result = subprocess.run(
        [
            "gcloud", "storage", "rsync",
            f"{BUCKET}/{PREFIX}", DEST,
            "--recursive",
        ],
        capture_output=True,
        text=True,
        timeout=900,  # ไม่เกิน 15 นาที
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"gcloud storage rsync failed (exit {result.returncode}): "
            f"{result.stderr[-2000:] or result.stdout[-2000:]}"
        )
    print("[db_sync] Sync done.")


def _worker() -> None:
    global _error
    try:
        if _db_looks_present():
            print("[db_sync] chroma_db already present locally, skipping sync.")
        elif not _gcloud_available():
            raise RuntimeError("gcloud CLI not found in PATH")
        else:
            _sync_once()
        _ready.set()
        print("[db_sync] Vector DB ready.")
    except Exception as e:
        _error = str(e)
        print(f"[db_sync] FAILED: {e}")


def start_background_sync() -> None:
    """เรียกตอน FastAPI startup — non-blocking, ไม่ delay การ bind PORT"""
    global _started
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_worker, name="gcs-db-sync", daemon=True).start()


def wait_ready(timeout: float = 300.0) -> None:
    """Block จนกว่า DB พร้อม (หรือ raise ถ้า sync fail / timeout)"""
    if _ready.is_set():
        return
    start = time.time()
    while not _ready.wait(timeout=min(1.0, timeout)):
        if _error:
            raise RuntimeError(f"chroma_db sync failed: {_error}")
        if time.time() - start > timeout:
            raise TimeoutError("chroma_db sync still not ready, try again later")
    if _error:
        raise RuntimeError(f"chroma_db sync failed: {_error}")


def is_ready() -> bool:
    return _ready.is_set()
