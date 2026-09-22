"""
BigQuery analytics logger (ตาราง rag_law_query)
----------------------------------------------
Project: rag-law-509304, Dataset: rag_law_query, Table: queries (สร้างอัตโนมัติ)

เก็บทุกคำถาม/คำตอบไว้วิเคราะห์ภายหลัง (คำถามยอดนิยม, ช่องว่างของฐานกฎหมาย,
คุณภาพคำตอบ ฯลฯ) — ทำงานแบบ optional: ถ้าไม่มี auth หรือ BigQuery API
ยังไม่เปิด จะ log ข้ามเฉยๆ ไม่ raise

Enable ใน project:
    gcloud services enable bigquery.googleapis.com

สร้าง dataset + table (ทำครั้งเดียว):
    bq mk --dataset --location=asia-southeast1 rag-law-509304:rag_law_query
    bq mk --table rag-law-509304:rag_law_query.queries \\
      session_id:STRING,question:STRING,answer:STRING,sources_json:STRING,created_at:TIMESTAMP
"""
import json
import os

PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "rag-law-509304")
DATASET = os.environ.get("BIGQUERY_DATASET", "rag_law_query")
TABLE = os.environ.get("BIGQUERY_TABLE", "queries")

_client = None
_available: bool | None = None


def _get_client():
    global _client, _available
    if _available is False:
        return None
    if _client is None:
        try:
            from google.cloud import bigquery

            _client = bigquery.Client(project=PROJECT_ID)
            _available = True
        except Exception as e:
            print(f"[analytics] BigQuery unavailable (logging skipped): {e}")
            _available = False
            return None
    return _client


def log_query(
    session_id: str,
    question: str,
    answer: str,
    sources: list | None = None,
) -> None:
    """insert 1 แถว — ไม่ raise แม้ BigQuery ใช้ไม่ได้"""
    client = _get_client()
    if client is None:
        return
    table_id = f"{PROJECT_ID}.{DATASET}.{TABLE}"
    row = {
        "session_id": session_id,
        "question": question,
        "answer": answer,
        "sources_json": json.dumps(sources or [], ensure_ascii=False),
    }
    try:
        errors = client.insert_rows_json(table_id, [row])
        if errors:
            print(f"[analytics] BigQuery insert errors: {errors}")
    except Exception as e:
        # อย่าให้ analytics พังทั้ง request
        print(f"[analytics] BigQuery insert failed: {e}")
