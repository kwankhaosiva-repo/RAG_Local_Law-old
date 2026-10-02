"""
Reset ก่อน re-ingest + re-migrate (Pinecone)
--------------------------------------------
ใช้เมื่อต้องสร้าง chunk ใหม่ทั้งชุด (เช่น หลังแก้ splitter) แล้ว migrate ขึ้น Pinecone ใหม่

ทำไมต้องล้าง:
  - ingest_core.py / ingest_recent.py ใช้ Chroma.add_documents() แบบไม่ส่ง ID
    → chromadb สร้าง UUID ใหม่ทุกครั้ง ⇒ ของเก่า(เพี้ยน) + ของใหม่ ปนกันถ้าไม่ล้าง
  - Pinecone upsert ใช้ id เดิมจาก Chroma; หลังล้าง Chroma แล้ว re-ingest ID จะเป็นชุดใหม่
    → vector เก่าที่เพี้ยนจะค้างอยู่ถ้าไม่ลบ namespace เก่าก่อน

ข้อควรรู้เรื่องโควตา Pinecone Starter:
  - การลบ vector/namespace **ไม่คืน** Write Units (WU) ที่ใช้ไปแล้ว
  - re-migrate เต็มชุดจะกิน WU อีกครั้ง (~1.5M) ⇒ ควรทำต้นเดือนหลังวันที่ 1 (โควต้า reset)
  - ลบ namespace/index จึงไม่ช่วยประหยัดโควตา แต่ทำให้ข้อมูลสะอาด (ไม่มี chunk เก่าค้าง)

ใช้:
    python scripts/reset_for_remigrate.py --chroma --dry-run     # ดูว่าจะลบอะไร
    python scripts/reset_for_remigrate.py --chroma --yes         # ล้าง Chroma collections
    python scripts/reset_for_remigrate.py --pinecone --yes       # ลบ namespace เก่าใน Pinecone
    python scripts/reset_for_remigrate.py --chroma --pinecone --yes
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import config  # noqa: F401 - โหลด env blob (PINECONE_KEY ฯลฯ)


def reset_chroma(dry_run: bool) -> int:
    import chromadb

    client = chromadb.PersistentClient(path=config.DB_DIR)
    names = [c.name for c in client.list_collections()]
    targets = [n for n in (config.COLLECTION_CORE, config.COLLECTION_RECENT) if n in names]
    total = 0
    for name in targets:
        col = client.get_collection(name)
        count = col.count()
        total += count
        if dry_run:
            print(f"[dry-run] จะลบ Chroma collection {name!r} ({count} documents)")
        else:
            client.delete_collection(name)
            print(f"[chroma] ลบ collection {name!r} แล้ว ({count} documents)")
    if dry_run:
        print(f"[dry-run] รวม {total} documents ที่จะถูกลบใน {len(targets)} collections")
    return total


def reset_pinecone(dry_run: bool) -> None:
    api_key = os.environ.get("PINECONE_KEY") or config.PINECONE_API_KEY
    if not api_key:
        sys.exit("ERROR: ต้องตั้ง PINECONE_KEY ก่อนลบข้อมูลใน Pinecone")

    from pinecone import Pinecone

    pc = Pinecone(api_key=api_key)
    if config.PINECONE_INDEX not in [i.name for i in pc.list_indexes()]:
        print(f"[pinecone] ไม่พบ index {config.PINECONE_INDEX!r} — ข้าม")
        return

    index = pc.Index(config.PINECONE_INDEX)
    for ns in (config.PINECONE_NAMESPACE_CORE, config.PINECONE_NAMESPACE_RECENT):
        if dry_run:
            print(f"[dry-run] จะลบ namespace {ns!r} ทั้งหมดใน index {config.PINECONE_INDEX!r}")
            continue
        try:
            index.delete(delete_all=True, namespace=ns)
            print(f"[pinecone] ลบ namespace {ns!r} แล้ว")
        except Exception as exc:  # noqa: BLE001
            # namespace ว่าง/ไม่มีอยู่ Pinecone อาจตอบ error — ไม่ใช่ปัญหา
            print(f"[pinecone] ลบ namespace {ns!r} ข้าม: {exc}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ล้าง Chroma/Pinecone ก่อน re-ingest + re-migrate")
    parser.add_argument("--chroma", action="store_true", help="ล้าง Chroma collections (core_law, recent_law)")
    parser.add_argument("--pinecone", action="store_true", help="ลบ namespace เก่าใน Pinecone")
    parser.add_argument("--dry-run", action="store_true", help="แสดงสิ่งที่จะลบโดยไม่ทำจริง")
    parser.add_argument("--yes", action="store_true", help="ยืนยันการลบ (จำเป็นถ้าไม่ใช่ dry-run)")
    args = parser.parse_args()

    if not (args.chroma or args.pinecone):
        sys.exit("ERROR: ระบุ --chroma และ/หรือ --pinecone")

    if not args.dry_run and not args.yes:
        print("ยังไม่ลบอะไร — ใส่ --yes เพื่อยืนยัน (หรือ --dry-run เพื่อดูก่อน)")
        sys.exit(1)

    print(f"DB_DIR = {config.DB_DIR}")
    if args.chroma:
        reset_chroma(args.dry_run)
    if args.pinecone:
        reset_pinecone(args.dry_run)

    if args.dry_run:
        print("\nนี่คือ dry-run — ไม่มีข้อมูลถูกลบ")
