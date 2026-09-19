"""
Ratchakitcha Crawler — อัปเดตกฎหมายใหม่อัตโนมัติ
-------------------------------------------------
ดึงรายการประกาศใหม่จาก Web Service ทางการของราชกิจจานุเบกษา (api.soc.go.th)
โหลด PDF -> extract ข้อความ -> แบ่งตามมาตรา/ข้อ -> ingest เข้า collection recent_law
พร้อม deduplicate ด้วย hash (title + publish_date) กัน ingest ซ้ำ

ใช้งาน:
    python src/crawl_ratchakitcha.py            # crawl ครั้งเดียวแล้วจบ
    python src/crawl_ratchakitcha.py --weekly   # รันต่อเนื่อง ทำงานทุกสัปดาห์
"""
import os
import sys
import hashlib
import sqlite3
import argparse
import warnings

warnings.filterwarnings("ignore")
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import re
import requests
import config
from tqdm import tqdm

# --- Windows fix เดียวกับ retriever.py ---
if os.name == "nt":
    os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

DEDUPE_DB_PATH = os.path.join(config.DB_DIR, "crawl_dedupe.sqlite")

# ประเภทเอกสารที่สนใจ (กฎหมายหลัก + ประกาศ/ระเบียบ)
LAW_TYPE_KEYWORDS = [
    "พระราชบัญญัติ", "พ.ร.บ.", "พระราชกำหนด", "พ.ร.ก.",
    "พระราชกฤษฎีกา", "พ.ร.ฎ.", "กฎกระทรวง", "ประกาศ", "ระเบียบ", "คำสั่ง",
]

HEADERS = {"User-Agent": "ThaiLegalRAG/1.0 (contact: local-user)"}


# ---------------- dedupe store ----------------
def init_dedupe_db():
    os.makedirs(os.path.dirname(DEDUPE_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DEDUPE_DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS ingested (
               doc_hash TEXT PRIMARY KEY,
               title TEXT,
               publish_date TEXT,
               ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
           )"""
    )
    conn.commit()
    return conn


def doc_hash(title: str, publish_date: str) -> str:
    return hashlib.sha256(f"{title}|{publish_date}".encode("utf-8")).hexdigest()


def already_ingested(conn, h: str) -> bool:
    cur = conn.execute("SELECT 1 FROM ingested WHERE doc_hash = ?", (h,))
    return cur.fetchone() is not None


def mark_ingested(conn, h: str, title: str, publish_date: str):
    conn.execute(
        "INSERT OR IGNORE INTO ingested (doc_hash, title, publish_date) VALUES (?, ?, ?)",
        (h, title, publish_date),
    )
    conn.commit()


# ---------------- API ----------------
def fetch_announcement_list(conn, page: int = 1, limit: int = 50):
    """เรียก API ราชกิจจานุเบกษา คืน list ของ {title, publish_date, pdf_path}"""
    url = config.RATCHAKITCHA_API_URL.format(page=page, limit=limit)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        print(f"[warn] เรียก API ราชกิจจาฯ ไม่สำเร็จ: {e}")
        return []

    items = []
    # โครงสร้าง response อาจเป็น list ตรงๆ หรือ key ใน object ใดๆ — รองรับทั้งสองแบบ
    entries = payload if isinstance(payload, list) else None
    if entries is None:
        for v in payload.values() if isinstance(payload, dict) else []:
            if isinstance(v, list):
                entries = v
                break
    if not entries:
        return []

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        title = (entry.get("title") or entry.get("bookName") or entry.get("docName") or "").strip()
        pdf_rel = (
            entry.get("pdfPath") or entry.get("filePdf") or entry.get("pdf_path")
            or entry.get("filePath") or ""
        )
        publish_date = str(entry.get("gisFromDate") or entry.get("publishDate")
                           or entry.get("announceDate") or "")[:10]
        if not title or not pdf_rel:
            continue
        if not any(k in title for k in LAW_TYPE_KEYWORDS):
            continue  # สนใจเฉพาะประเภทกฎหมาย/ประกาศ
        h = doc_hash(title, publish_date)
        if already_ingested(conn, h):
            continue
        items.append({"title": title, "publish_date": publish_date, "pdf_path": pdf_rel})
    return items


def download_pdf(pdf_rel_path: str, out_dir: str) -> str | None:
    url = config.RATCHAKITCHA_DOC_URL.format(pdf_path=pdf_rel_path.lstrip("/"))
    try:
        resp = requests.get(url, headers=HEADERS, timeout=60)
        resp.raise_for_status()
        os.makedirs(out_dir, exist_ok=True)
        fname = hashlib.md5(pdf_rel_path.encode()).hexdigest() + ".pdf"
        path = os.path.join(out_dir, fname)
        with open(path, "wb") as f:
            f.write(resp.content)
        return path
    except Exception as e:
        print(f"[warn] ดาวน์โหลด PDF ไม่สำเร็จ ({pdf_rel_path}): {e}")
        return None


# ---------------- PDF -> text ----------------
def extract_pdf_text(pdf_path: str) -> str:
    import fitz  # pymupdf

    try:
        doc = fitz.open(pdf_path)
        text = ""
        for page in doc:
            text += page.get_text("text") + "\n\n"
        doc.close()
        return text.strip()
    except Exception as e:
        print(f"[warn] extract text ไม่สำเร็จ ({pdf_path}): {e}")
        return ""


# ---------------- text -> Documents (ใช้ logic เดียวกับ ingest_recent.py) ----------------
def get_hierarchy_metadata(title):
    title_lower = title.lower()
    if "รัฐธรรมนูญ" in title_lower:
        return 1, "มาตรา"
    elif "พระราชบัญญัติ" in title_lower or "พ.ร.บ." in title_lower or "พระราชกำหนด" in title_lower or "พ.ร.ก." in title_lower:
        return 2, "มาตรา"
    elif "พระราชกฤษฎีกา" in title_lower or "พ.ร.ฎ." in title_lower:
        return 3, "มาตรา"
    elif "กฎกระทรวง" in title_lower:
        return 4, "ข้อ"
    elif "ประกาศ" in title_lower or "ระเบียบ" in title_lower or "คำสั่ง" in title_lower:
        return 5, "ข้อ"
    else:
        return 2, "มาตรา"


def build_documents(title, publish_date, full_content):
    """แบ่งเนื้อหาตามมาตรา/ข้อ แล้วสร้าง LangChain Documents พร้อม metadata"""
    from langchain_core.documents import Document
    from langchain.text_splitter import RecursiveCharacterTextSplitter

    hierarchy_level, unit_type = get_hierarchy_metadata(title)
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=config.CHUNK_SIZE,
        chunk_overlap=config.CHUNK_OVERLAP,
        separators=["\n\n", "\n", " ", ""],
    )

    pattern = r'((?:มาตรา|ข้อ)\s*[0-9๑-๙\d\./]+(?:\s*(?:ทวิ|ตรี|จัตวา|เบญจ|ฉ|สัตต|อัฐ|นพ))?)'
    sections = re.split(pattern, full_content)
    documents = []

    if len(sections) > 1:
        for i in range(1, len(sections), 2):
            sec_header = sections[i].strip()
            sec_body = sections[i + 1] if i + 1 < len(sections) else ""
            chunks = text_splitter.split_text(sec_body)
            for j, chunk in enumerate(chunks):
                header = f"กฎหมาย: {title}\nที่มา: ราชกิจจานุเบกษา {publish_date}\nส่วนของ: {sec_header}"
                if len(chunks) > 1:
                    header += f" (ส่วนที่ {j+1}/{len(chunks)})"
                chunk_text = f"{header}\nเนื้อหา: {chunk.strip()}"
                metadata = {
                    "source": "ratchakitcha",
                    "title": title,
                    "section_header": sec_header,
                    "unit_type": unit_type,
                    "hierarchy_level": hierarchy_level,
                    "publish_date": publish_date,
                    "category": "Crawler Update",
                }
                documents.append(Document(page_content=chunk_text, metadata=metadata))
    else:
        chunks = text_splitter.split_text(full_content)
        for j, chunk in enumerate(chunks):
            header = f"กฎหมาย: {title}\nที่มา: ราชกิจจานุเบกษา {publish_date}"
            if len(chunks) > 1:
                header += f" (ส่วนที่ {j+1}/{len(chunks)})"
            chunk_text = f"{header}\nเนื้อหา: {chunk.strip()}"
            metadata = {
                "source": "ratchakitcha",
                "title": title,
                "unit_type": unit_type,
                "hierarchy_level": hierarchy_level,
                "publish_date": publish_date,
                "category": "Crawler Update",
            }
            documents.append(Document(page_content=chunk_text, metadata=metadata))
    return documents


def ingest_documents(documents):
    from langchain_huggingface import HuggingFaceEmbeddings
    from langchain_chroma import Chroma

    embeddings = HuggingFaceEmbeddings(model_name=config.EMBEDDING_MODEL_NAME)
    vectorstore = Chroma(
        collection_name=config.COLLECTION_RECENT,
        embedding_function=embeddings,
        persist_directory=config.DB_DIR,
    )
    batch_size = 500
    for i in tqdm(range(0, len(documents), batch_size), desc="Indexing"):
        vectorstore.add_documents(documents=documents[i:i + batch_size])


# ---------------- main ----------------
def run_crawl():
    print("=" * 50)
    print("  Ratchakitcha Crawler — อัปเดตกฎหมายใหม่")
    print("=" * 50)

    conn = init_dedupe_db()
    new_items = []
    for page in range(1, config.CRAWL_PAGES + 1):
        items = fetch_announcement_list(conn, page=page, limit=config.CRAWL_PAGE_SIZE)
        new_items.extend(items)
        print(f"หน้า {page}: พบกฎหมาย/ประกาศใหม่ {len(items)} รายการ")

    if not new_items:
        print("ไม่มีรายการใหม่ที่ต้อง ingest")
        conn.close()
        return

    os.makedirs(config.CRAWL_DIR, exist_ok=True)
    all_docs = []
    for item in tqdm(new_items, desc="Processing PDFs"):
        pdf_path = download_pdf(item["pdf_path"], config.CRAWL_DIR)
        if not pdf_path:
            continue
        text = extract_pdf_text(pdf_path)
        if not text or len(text) < 100:
            # PDF เป็นรูปภาพ (ต้อง OCR) — ข้ามไปก่อน เพราะ iapp_2025 รองรับ OCR แยกอยู่แล้ว
            print(f"[skip] {item['title'][:50]} (PDF ไม่มี text layer ต้อง OCR)")
            continue
        docs = build_documents(item["title"], item["publish_date"], text)
        all_docs.extend(docs)
        mark_ingested(conn, doc_hash(item["title"], item["publish_date"]),
                      item["title"], item["publish_date"])

    if not all_docs:
        print("ไม่มี documents ที่ extract ได้")
        conn.close()
        return

    print(f"\nรวม {len(all_docs)} chunks เตรียม ingest เข้า '{config.COLLECTION_RECENT}'")
    ingest_documents(all_docs)
    conn.close()
    print("--- Crawl + Ingest สำเร็จ! ---")


def main():
    parser = argparse.ArgumentParser(description="Ratchakitcha law crawler")
    parser.add_argument("--weekly", action="store_true",
                        help="รันต่อเนื่อง ทำงานทุกสัปดาห์ (ใช้ schedule lib)")
    args = parser.parse_args()

    if args.weekly:
        import schedule
        import time

        print("โหมด weekly: จะ crawl ตอนนี้ครั้งแรก และทุก 7 วัน (Ctrl+C เพื่อหยุด)")
        run_crawl()
        schedule.every(7).days.do(run_crawl)
        while True:
            schedule.run_pending()
            time.sleep(3600)
    else:
        run_crawl()


if __name__ == "__main__":
    main()
