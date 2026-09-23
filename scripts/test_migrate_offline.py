"""
ทดสอบ scripts/migrate_chroma_to_pinecone.py แบบ offline (ไม่ยิง Pinecone จริง)

ใช้:
    python scripts/test_migrate_offline.py

ทำไมต้องมี:
    - เคยเจอ bug: chroma คืน numpy.float64 → Pinecone SDK v7 โยน
      PineconeApiTypeError: Required value type is float and passed type was float64
    - สคริปต์นี้ใช้ IndexRequestFactory ของ Pinecone SDK เป็น validator ตัวจริง
      (ตรวจ type/metadata ครบเหมือนยิงขึ้นจริง แต่ไม่กิน WU ไม่ต้องมี API key)
    - ตรวจ batch cap (1,000 records / 2 MB), resume ด้วย state file และการหยุดเมื่อโดน 429
"""
import importlib.util
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "migrate_chroma_to_pinecone.py")

spec = importlib.util.spec_from_file_location("mig", SCRIPT)
mig = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mig)

from pinecone.db_data.request_factory import IndexRequestFactory  # noqa: E402

CHROMA = mig.config.DB_DIR
OK = []


def check(label, cond, extra=""):
    print(f"{'✅' if cond else '❌'} {label} {extra}")
    OK.append(bool(cond))


class FakeIndex:
    """แทน pinecone.Index — validate ด้วย SDK จริง แต่เก็บผลใน memory"""

    def __init__(self, fail_after=None, quota=False):
        self.records = 0
        self.batches = []
        self.namespaces = set()
        self.fail_after = fail_after
        self.quota = quota

    def upsert(self, vectors, namespace):
        IndexRequestFactory.upsert_request(vectors, namespace, True)
        for vec in vectors:
            if not all(type(x) is float for x in vec["values"]):
                raise AssertionError("values ต้องเป็น Python float ทุกตัว")
            if any(v is None for v in vec["metadata"].values()):
                raise AssertionError("metadata ต้องไม่มี None")
        self.batches.append(len(vectors))
        self.namespaces.add(namespace)
        self.records += len(vectors)
        if self.fail_after and self.records >= self.fail_after:
            if self.quota:
                raise Exception(
                    "Request failed. You've reached the write unit limit for the current "
                    "month. (429 TOO_MANY_REQUESTS RESOURCE_EXHAUSTED)"
                )
            raise Exception("network boom")

    def describe_index_stats(self):
        return {"total_vector_count": self.records}


def main():
    import numpy as np

    print("\n--- 1) _to_float_values: numpy float64 ต้องกลายเป็น Python float ---")
    vals = mig._to_float_values(np.array([0.5, -1.25], dtype=np.float64))
    check("numpy float64 → list[float]", vals == [0.5, -1.25] and all(type(x) is float for x in vals), vals)

    print("\n--- 2) _fit_batches: cap 1,000 records / 2 MB ---")
    mb = 1024 * 1024
    b1 = [len(c) for c in mig._fit_batches(list(range(2500)), [1] * 2500, 5000)]
    check("จำกัด 1,000 records/ชุด", b1 == [1000, 1000, 500], b1)
    b2 = [len(c) for c in mig._fit_batches(list(range(5)), [mb] * 5, 1000)]
    check("จำกัด 2 MB/ชุด", b2 == [2, 2, 1], b2)

    print("\n--- 3) migrate recent_law (fake index) limit=1200 ---")
    state = tempfile.mktemp(suffix=".json")
    idx = FakeIndex()
    n1 = mig.migrate(CHROMA, 200, False, limit=1200, only="recent_law", index=idx, state_file=state)
    st = json.load(open(state, encoding="utf-8"))
    check("อัปครบ 1200 records", n1 == 1200, f"migrated={n1}")
    check("ทุกชุดผ่าน validator ของ SDK", bool(idx.batches), f"batches={len(idx.batches)} max={max(idx.batches)}")
    check("namespace ถูกต้อง", idx.namespaces == {"recent_law"}, idx.namespaces)
    check("state = offset 1200, ยังไม่ done",
          st["recent_law"] == {"offset": 1200, "done": False}, st["recent_law"])

    print("\n--- 4) รันซ้ำ ต้องต่อจาก offset เดิม ไม่เริ่ม 0 ---")
    idx2 = FakeIndex()
    n2 = mig.migrate(CHROMA, 200, False, limit=600, only="recent_law", index=idx2, state_file=state)
    st2 = json.load(open(state, encoding="utf-8"))
    check("รอบสองอัป 600 records", n2 == 600, f"migrated={n2}")
    check("offset ต่อเนื่อง 1200 → 1800", st2["recent_law"]["offset"] == 1800, st2["recent_law"])

    print("\n--- 5) โดน 429 ต้องหยุดอย่างปลอดภัย + จำ offset ไว้ resume ---")
    state3 = tempfile.mktemp(suffix=".json")
    idx3 = FakeIndex(fail_after=700, quota=True)
    try:
        mig.migrate(CHROMA, 200, False, only="recent_law", index=idx3, state_file=state3)
        check("ต้อง SystemExit(2) เมื่อโดนโควต้า", False)
    except SystemExit as exc:
        st3 = json.load(open(state3, encoding="utf-8"))
        check("exit code = 2", exc.code == 2, exc.code)
        check("state ชี้ batch ที่ค้าง (offset 500)",
              st3["recent_law"] == {"offset": 500, "done": False}, st3["recent_law"])

    print(f"\n=== สรุป: ผ่าน {sum(OK)}/{len(OK)} ข้อ ===")
    return 0 if all(OK) else 1


if __name__ == "__main__":
    sys.exit(main())
