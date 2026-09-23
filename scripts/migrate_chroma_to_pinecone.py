"""
Migrate chroma_db → Pinecone
----------------------------
อ่านทุก document + embedding จาก chroma local (หรือจาก GCS snapshot) แล้ว
อัปขึ้น Pinecone ทีละ batch

ใช้:
    export PINECONE_KEY=pcsk_...
    python scripts/migrate_chroma_to_pinecone.py --dry-run   # เช็คจำนวน + ขนาด (ไม่ต้องมี key)
    python scripts/migrate_chroma_to_pinecone.py             # อัปจริง

Option:
    --db-dir ./chroma_db        # ที่อยู่ chroma local (default: ./chroma_db)
    --batch-size 200            # จำนวน vectors ต่อ 1 อัป (default 200)
    --dry-run                   # นับจำนวน + ประเมินขนาด/WU เท่านั้น ไม่แตะ Pinecone
    --limit N                   # อัปสูงสุด N records แล้วหยุด (ไว้คุมโควต้า WU)
    --only core_law|recent_law  # ทำเฉพาะ collection ที่ระบุ
    --state-file PATH           # ไฟล์เก็บความคืบหน้าเพื่อ resume (default: scripts/.migrate_state.json)
    --reset-state               # ลืมความคืบหน้าเดิม เริ่มจาก 0

หมายเหตุสำคัญ:
    - Pinecone SDK v7 validate แบบ strict: ค่า values ต้องเป็น Python float เท่านั้น
      ถ้าส่ง numpy.float64 (chroma คืน dtype float64) จะได้
      PineconeApiTypeError: Required value type is float and passed type was float64
      → สคริปต์นี้แปลงเป็น float ให้แล้ว (ดู build_vectors)
    - Pinecone จำกัด 1 upsert = 1,000 records หรือ 2 MB → ตัด batch ตามขนาดให้อัตโนมัติ
    - upsert ใช้ ID เดิมจาก Chroma → รันซ้ำได้ (idempotent) แต่ "นับ WU ใหม่ทุกครั้ง"
      Starter = 2M WU/เดือน → migration นี้ ~1.5M WU ⇒ ควรทำครั้งเดียวในหนึ่งเดือน
    - Pinecone index ที่ใช้: ชื่อจาก env PINECONE_INDEX (default: thai-law)
      dimension ต้องตรงกับ embedding model (paraphrase-multilingual-MiniLM-L12-v2 = 384)
      metric: cosine
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import config  # noqa: F401 - โหลด env blob / paths ก่อน

# --- Pinecone limits (docs.pinecone.io/reference/api/database-limits/operation-limits) ---
MAX_UPSERT_RECORDS = 1000
MAX_UPSERT_BYTES = 2 * 1024 * 1024  # 2 MB ต่อ 1 request
FETCH_BATCH = 500                   # จำนวน records ที่ดึงจาก chroma ต่อครั้ง
BYTES_PER_FLOAT = 4                 # float32
RECORD_OVERHEAD_BYTES = 64          # เผื่อ key/วงเล็บ/id แบบหยาบ ๆ
STATE_FILE_DEFAULT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), ".migrate_state.json"
)


def _log(msg: str) -> None:
    print(f"[migrate] {msg}", flush=True)


def get_pinecone_index():
    """เชื่อม Pinecone + สร้าง index ถ้ายังไม่มี + เช็ค dimension ให้ตรงกับ embedding"""
    api_key = os.environ.get("PINECONE_KEY") or config.PINECONE_API_KEY
    if not api_key:
        sys.exit("ERROR: PINECONE_KEY not set")

    from pinecone import Pinecone, ServerlessSpec
    from langchain_huggingface import HuggingFaceEmbeddings

    pc = Pinecone(api_key=api_key)
    index_name = config.PINECONE_INDEX
    dim = len(
        HuggingFaceEmbeddings(model_name=config.EMBEDDING_MODEL_NAME).embed_query("ทดสอบ")
    )

    if index_name not in [i.name for i in pc.list_indexes()]:
        _log(f"Creating index {index_name!r} (dim={dim}, metric=cosine)...")
        pc.create_index(
            name=index_name,
            dimension=dim,
            metric="cosine",
            spec=ServerlessSpec(cloud="aws", region="us-east-1"),
        )
        _log("Index created (Serverless, aws us-east-1 — เปลี่ยนได้ที่ Pinecone console)")
    else:
        existing_dim = int(pc.describe_index(index_name).dimension)
        if existing_dim != dim:
            sys.exit(
                f"ERROR: index {index_name!r} มี dimension={existing_dim} "
                f"แต่ embedding model ให้ dim={dim} → ลบ index เดิมก่อน"
            )
        _log(f"ใช้ index เดิม {index_name!r} (dim={dim})")

    return pc.Index(index_name)


def _to_float_values(embedding):
    """numpy.ndarray/list → list[float] ของ Python จริง ๆ (Pinecone SDK v7 บังคับ)"""
    if embedding is None:
        return []
    if hasattr(embedding, "tolist"):  # chroma 1.x คืน numpy array dtype float64
        embedding = embedding.tolist()
    return [float(x) for x in embedding]


def build_vectors(ids, embeddings, metadatas, documents):
    """สร้าง record dict + ขนาดโดยประมาณ (ไบต์) ของแต่ละ record"""
    vectors, sizes = [], []
    for i, _id in enumerate(ids):
        # Pinecone ไม่รับ metadata ที่เป็น None → ตัดทิ้ง
        meta = {k: v for k, v in (metadatas[i] or {}).items() if v is not None}
        meta["text"] = documents[i] or ""  # Pinecone ต้องเก็บ text ใน metadata
        values = _to_float_values(embeddings[i])
        vectors.append({"id": _id, "values": values, "metadata": meta})
        sizes.append(
            len(values) * BYTES_PER_FLOAT
            + len(str(_id))
            + len(json.dumps(meta, ensure_ascii=False).encode("utf-8"))
            + RECORD_OVERHEAD_BYTES
        )
    return vectors, sizes


def _fit_batches(vectors, sizes, batch_size):
    """ตัด batch ตามจำนวนที่ขอ และตามลิมิต Pinecone (1,000 records / 2 MB ต่อ request)"""
    limit = min(batch_size, MAX_UPSERT_RECORDS)
    batch, batch_bytes = [], 0
    for vec, size in zip(vectors, sizes):
        if batch and (len(batch) >= limit or batch_bytes + size > MAX_UPSERT_BYTES):
            yield batch
            batch, batch_bytes = [], 0
        batch.append(vec)
        batch_bytes += size
    if batch:
        yield batch


def _load_state(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(path, state):
    state["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)


def _is_quota_error(exc):
    msg = str(exc).lower()
    return any(
        token in msg
        for token in ("429", "too_many_requests", "resource_exhausted", "usage limit", "quota")
    )


def iter_chroma_batches(db_dir: str, collection_name: str, start_offset: int = 0):
    """yield (offset, total, result) ทีละ FETCH_BATCH records จาก chroma raw client"""
    import chromadb

    client = chromadb.PersistentClient(path=db_dir)
    col = client.get_collection(collection_name)
    total = col.count()
    _log(f"Collection {collection_name!r}: {total} documents")

    for offset in range(start_offset, total, FETCH_BATCH):
        got = col.get(
            offset=offset,
            limit=FETCH_BATCH,
            include=["documents", "metadatas", "embeddings"],
        )
        yield offset, total, got


def dry_run_pass(db_dir: str, collections) -> int:
    """นับจำนวน + ประเมินขนาด/WU โดยไม่แตะ Pinecone เลย (ไม่ต้องมี API key)"""
    grand_total = 0
    grand_bytes = 0
    for col_name in collections:
        print(f"\n=== {col_name} (dry-run) ===")
        seen = 0
        col_bytes = 0
        for _, _, got in iter_chroma_batches(db_dir, col_name):
            vectors, sizes = build_vectors(
                got["ids"], got["embeddings"], got["metadatas"], got["documents"]
            )
            seen += len(vectors)
            col_bytes += sum(sizes)
            print(f"  batch: {len(vectors)} docs (total {seen})", flush=True)
        grand_total += seen
        grand_bytes += col_bytes
        _log(
            f"{col_name}: {seen} records, payload ~{col_bytes / 1e6:.1f} MB "
            f"(~{col_bytes / 1024 / 1e6:.2f}M WU ถ้า 1 WU ≈ 1 KB)"
        )
    _log(
        f"DRY RUN — รวม {grand_total} vectors, payload ~{grand_bytes / 1e6:.1f} MB "
        f"(~{grand_bytes / 1024 / 1e6:.2f}M WU) — ยังไม่แตะ Pinecone"
    )
    return grand_total


def migrate(
    db_dir: str,
    batch_size: int,
    dry_run: bool,
    *,
    limit=None,
    only=None,
    index=None,
    state_file: str = STATE_FILE_DEFAULT,
    reset_state: bool = False,
) -> int:
    collections = [config.COLLECTION_CORE, config.COLLECTION_RECENT]
    # Pinecone 1 namespace ต่อ 1 collection เดิม — ทำให้ค้นแยกได้เหมือนเดิม
    namespaces = {
        config.COLLECTION_CORE: config.PINECONE_NAMESPACE_CORE,
        config.COLLECTION_RECENT: config.PINECONE_NAMESPACE_RECENT,
    }
    if only:
        if only not in namespaces:
            sys.exit(
                f"ERROR: --only ต้องเป็น {config.COLLECTION_CORE} หรือ {config.COLLECTION_RECENT}"
            )
        collections = [only]

    if dry_run:
        return dry_run_pass(db_dir, collections)

    if index is None:
        index = get_pinecone_index()

    state = {} if reset_state else _load_state(state_file)
    remaining = limit
    grand_total = 0

    for col_name in collections:
        if remaining is not None and remaining <= 0:
            _log("ถึง --limit แล้วหยุด (รันซ้ำเพื่อทำต่อจากจุดเดิม)")
            break
        ns = namespaces[col_name]
        col_state = state.get(col_name) or {}
        if col_state.get("done"):
            _log(f"{col_name}: ข้าม (state file บอกว่าทำครบแล้ว)")
            continue
        start_offset = int(col_state.get("offset") or 0)
        _log(f"\n=== {col_name} → namespace {ns!r} (เริ่มที่ offset {start_offset}) ===")

        for offset, total, got in iter_chroma_batches(db_dir, col_name, start_offset):
            ids = got["ids"]
            embeddings = got["embeddings"]
            metadatas = got["metadatas"]
            documents = got["documents"]
            if remaining is not None and remaining < len(ids):
                ids = ids[:remaining]
                embeddings = embeddings[:remaining]
                metadatas = metadatas[:remaining]
                documents = documents[:remaining]

            consumed = len(ids)
            if consumed == 0:
                break

            vectors, sizes = build_vectors(ids, embeddings, metadatas, documents)
            sent = 0
            try:
                for chunk in _fit_batches(vectors, sizes, batch_size):
                    index.upsert(vectors=chunk, namespace=ns)
                    sent += len(chunk)
                    grand_total += len(chunk)
                    if remaining is not None:
                        remaining -= len(chunk)
                    _log(f"  upserted {sent}/{consumed} (total {grand_total})")
            except Exception as exc:  # noqa: BLE001
                if _is_quota_error(exc):
                    state[col_name] = {"offset": offset, "done": False}
                    _save_state(state_file, state)
                    _log("⛔ Pinecone ตอบ 429 / โควต้าหมด — หยุดไว้อย่างปลอดภัย")
                    _log(f"   ความคืบหน้าอยู่ใน {state_file} → รันใหม่จะต่อจาก offset {offset}")
                    _log("   ทางเลือก: อัปเป็น Builder ($20/เดือน, 5M WU) แล้วรันต่อในเดือนเดียวกัน")
                    sys.exit(2)
                raise

            # จบ batch นี้ → จำ offset ถัดไปไว้ resume (done เมื่อเก็บครบทั้ง collection)
            next_offset = offset + consumed
            state[col_name] = {"offset": next_offset, "done": next_offset >= total}
            _save_state(state_file, state)
            if remaining is not None and remaining <= 0:
                break

        if remaining is not None and remaining <= 0:
            _log("ถึง --limit แล้วหยุด (รันซ้ำเพื่อทำต่อจากจุดเดิม)")
            break

    _log(f"\nDONE — {grand_total} vectors migrated.")
    try:
        stats = index.describe_index_stats()
        total = getattr(stats, "total_vector_count", None)
        if total is None and isinstance(stats, dict):
            total = stats.get("total_vector_count")
        _log(f"Pinecone index stats: {total} vectors (เช็คโควต้าจริงใน console → Usage)")
    except Exception as exc:  # noqa: BLE001
        _log(f"describe_index_stats ล้มเหลว (ไม่กระทบข้อมูลที่อัปแล้ว): {exc}")
    return grand_total


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate chroma → Pinecone")
    parser.add_argument("--db-dir", default=config.DB_DIR)
    parser.add_argument("--batch-size", type=int, default=200,
                        help="vectors ต่อ 1 upsert (default 200; Pinecone จำกัด 1000 records/2 MB)")
    parser.add_argument("--dry-run", action="store_true",
                        help="นับจำนวน + ประเมินขนาด/WU เท่านั้น ไม่แตะ Pinecone")
    parser.add_argument("--limit", type=int, default=None,
                        help="อัปสูงสุด N records แล้วหยุด (ไว้คุมโควต้า WU)")
    parser.add_argument("--only", default=None,
                        help=f"ทำเฉพาะ collection: {config.COLLECTION_CORE} หรือ {config.COLLECTION_RECENT}")
    parser.add_argument("--state-file", default=STATE_FILE_DEFAULT,
                        help="ไฟล์เก็บความคืบหน้าเพื่อ resume")
    parser.add_argument("--reset-state", action="store_true",
                        help="ลืมความคืบหน้าเดิม เริ่มจาก offset 0")
    args = parser.parse_args()

    migrate(
        args.db_dir,
        args.batch_size,
        args.dry_run,
        limit=args.limit,
        only=args.only,
        state_file=args.state_file,
        reset_state=args.reset_state,
    )
