import config

# --- Vector store backend: "chroma" (default) หรือ "pinecone" ---
VECTOR_BACKEND = (config.VECTOR_STORE or "chroma").lower()

if VECTOR_BACKEND == "pinecone":
    from langchain_pinecone import PineconeVectorStore
else:
    from langchain_chroma import Chroma

from langchain_huggingface import HuggingFaceEmbeddings
try:
    from langchain.retrievers import EnsembleRetriever
except ImportError:
    from langchain_classic.retrievers import EnsembleRetriever
from langchain_community.retrievers import BM25Retriever
import os

# --- Windows Path Fix ---
if os.name == 'nt':
    os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
# -------------------------

# langchain-pinecone อ่าน env PINECONE_API_KEY เท่านั้น (โปรเจกต์ตั้ง PINECONE_KEY)
# ไม่งั้นจะได้ ValueError: Pinecone API key must be provided in either
# `pinecone_api_key` or `PINECONE_API_KEY` environment variable (ดู langchain-pinecone#110)
if VECTOR_BACKEND == "pinecone" and config.PINECONE_API_KEY:
    os.environ.setdefault("PINECONE_API_KEY", config.PINECONE_API_KEY)

class Retriever:
    def __init__(self):
        print(f"Initializing Professional Hybrid Retriever (backend={VECTOR_BACKEND})...")
        self.embeddings = HuggingFaceEmbeddings(model_name=config.EMBEDDING_MODEL_NAME)

        # 1. Load Vector Stores
        if VECTOR_BACKEND == "pinecone":
            # Pinecone: ใช้ namespace แยก core/recent ใน index เดียว
            # text ถูกเก็บไว้ใน metadata field "text" (ดู scripts/migrate_chroma_to_pinecone.py)
            self.core_db = PineconeVectorStore(
                index_name=config.PINECONE_INDEX,
                embedding=self.embeddings,
                namespace=config.PINECONE_NAMESPACE_CORE,
                text_key="text",
            )
            self.recent_db = PineconeVectorStore(
                index_name=config.PINECONE_INDEX,
                embedding=self.embeddings,
                namespace=config.PINECONE_NAMESPACE_RECENT,
                text_key="text",
            )
        else:
            self.core_db = Chroma(
                collection_name=config.COLLECTION_CORE,
                persist_directory=config.DB_DIR,
                embedding_function=self.embeddings
            )
            self.recent_db = Chroma(
                collection_name=config.COLLECTION_RECENT,
                persist_directory=config.DB_DIR,
                embedding_function=self.embeddings
            )

    def _get_bm25_retriever(self, docs):
        """Builds a Thai-aware BM25 retriever from a list of documents."""
        if not docs:
            return None
        try:
            try:
                from pythainlp.tokenize import word_tokenize
                return BM25Retriever.from_documents(docs, preprocess_func=word_tokenize)
            except ImportError:
                return BM25Retriever.from_documents(docs)
        except (ImportError, Exception) as e:
            # ถ้าไม่มี rank_bm25 หรือล้ม ให้ fallback ไปใช้ vector rank 100%
            return None

    @staticmethod
    def detect_target_law(query):
        """
        ตรวจจับชื่อกฎหมายจากคำถามแบบ generic (ใช้ regex แทน list ตายตัว)
        คืนค่า fragment ของชื่อกฎหมาย เช่น 'ธุรกิจสถาบันการเงิน' หรือ None
        """
        import re
        is_asking_which_law = any(phrase in query for phrase in ["กฎหมายใด", "กฎหมายฉบับใด", "พ.ร.บ. ใด", "พระราชบัญญัติใด"])
        if is_asking_which_law:
            return None

        # จับข้อความต่อจากคำว่า พระราชบัญญัติ / พ.ร.บ. / พระราชกฤษฎีกา / กฎกระทรวง เช่น "พระราชบัญญัติธุรกิจสถาบันการเงิน"
        m = re.search(
            r'(?:พระราชบัญญัติ|พ\.?ร\.?บ\.?|พระราชกฤษฎีกา|พ\.?ร\.?ฎ\.?|กฎกระทรวง)\s*([\u0E00-\u0E7F]{4,40})',
            query
        )
        if m:
            fragment = m.group(1).strip()
            # ตัดคำหลังท้ายที่เป็นเงื่อนไข เช่น "พ.ศ. 2551" / "นั้น" / "ได้ไหม"
            fragment = re.split(r'\s+(?:พ\.?ศ\.?|นั้น|นี้|ได้|หรือ|ไหม|มั้ย)', fragment)[0].strip()
            # ตัดคำนำหน้าที่เป็นประเภทกฎหมายออกให้เหลือแต่ core name
            for prefix in ("พระราชบัญญัติ", "พระราชกฤษฎีกา", "กฎกระทรวง", "ว่าด้วย"):
                if fragment.startswith(prefix):
                    fragment = fragment[len(prefix):].strip()
            return fragment if len(fragment) >= 4 else None
        return None

    def retrieve(self, query, filter_metadata=None):
        """
        Retrieves documents using True Hybrid Search (RRF with Vector + Thai BM25).
        `query` ควรเป็น standalone query (ถ้ามีประวัติแชท ให้ rewrite ก่อนส่งเข้ามา)
        """
        search_query = query

        # 1. Semantic Search (Vector) - ดึงฐานข้อมูลมาเยอะขึ้นเพื่อให้แน่ใจว่าไม่พลาดมาตราสำคัญ
        core_vector_docs = self.core_db.similarity_search(search_query, k=300, filter=filter_metadata)
        recent_vector_docs = self.recent_db.similarity_search(search_query, k=50, filter=filter_metadata)
        all_vector_docs = core_vector_docs + recent_vector_docs

        if not all_vector_docs:
            return []
            
        # 1.5 Clean Up IAPP natural_text before BM25 processes it
        import json
        for doc in all_vector_docs:
            if doc.page_content.strip().startswith('{"natural_text"'):
                try:
                    data = json.loads(doc.page_content)
                    doc.page_content = data.get('natural_text', doc.page_content)
                except Exception:
                    pass

        # 2. Extract Target Law for Hard Filtering (generic regex-based detection)
        target_law = self.detect_target_law(query)

        if target_law:
            # เผื่อ regex จับ fragment ยาวเกิน: ใช้ fuzzy contains ทั้งสองทาง
            def law_matches(title):
                return target_law in title or title in target_law
            strict_matched_docs = [doc for doc in all_vector_docs if law_matches(doc.metadata.get('title', ''))]
            # ถ้ามีเอกสารที่ตรงกับชื่อกฎหมายที่ถามจริงๆ ให้ใช้เฉพาะกลุ่มนี้เท่านั้น ห้ามเอาขยะมาปน
            if len(strict_matched_docs) > 0:
                all_vector_docs = strict_matched_docs

        # 3. Thai-Aware BM25 Reranking & Reciprocal Rank Fusion (RRF)
        vector_ranks = {doc.page_content: i for i, doc in enumerate(all_vector_docs)}
        
        bm25_retriever = self._get_bm25_retriever(all_vector_docs)
        if bm25_retriever:
            bm25_retriever.k = len(all_vector_docs)
            bm25_docs = bm25_retriever.invoke(query)
            bm25_ranks = {doc.page_content: i for i, doc in enumerate(bm25_docs)}
        else:
            bm25_ranks = vector_ranks

        # Calculate RRF Score
        scores = {}
        for doc in all_vector_docs:
            content = doc.page_content
            vr = vector_ranks.get(content, 1000)
            br = bm25_ranks.get(content, 1000)
            
            # RRF formula: 1 / (60 + rank) 
            score = (1.0 / (60 + vr)) + (1.0 / (60 + br))
            
            # Soft boost กรณีไม่มี Hard Filtering
            if target_law and (target_law in doc.metadata.get('title', '') or doc.metadata.get('title', '') in target_law):
                score *= 1.5
                
            scores[content] = score

        # เรียงลำดับตามคะแนน RRF ที่คำนวณได้
        best_contents = sorted(scores.keys(), key=lambda k: scores[k], reverse=True)
        
        # 4. DYNAMIC TEXT REPAIR & CLEANUP
        seen_content = set()
        fixed_docs = []
        import re
        for content in best_contents:
            if len(fixed_docs) >= config.RETRIEVAL_K: # ลดจำนวนลงให้แม่นยำขึ้น เอาแค่ 5 อันดับแรกเพื่อลด Noise
                break
                
            # หาเอกสารต้นฉบับ
            doc = next(d for d in all_vector_docs if d.page_content == content)
            
            if 'section_id' in doc.metadata:
                new_label = doc.metadata['section_id']
                unit = doc.metadata.get("unit_type", "มาตรา")
                if str(new_label).startswith("ID:"):
                    doc.page_content = re.sub(r'(?:มาตรา|ข้อ):\s*ID:[a-zA-Z0-9_]+\n', '', doc.page_content)
                else:
                    doc.page_content = re.sub(r'(?:มาตรา|ข้อ):\s*ID:[a-zA-Z0-9_]+', f'{unit}: {new_label}', doc.page_content)
            
            # Ensure the title is absolutely glued to the text so the LLM doesn't hallucinate
            law_title = doc.metadata.get('title', 'ไม่ได้ระบุชื่อกฎหมาย')
            clean_text = doc.page_content.strip()
            
            # If the text somehow doesn't have the title physically in the string, prepend it
            if "กฎหมาย: " not in clean_text:
                clean_text = f"กฎหมาย: {law_title}\n{clean_text}"
                
            doc.page_content = clean_text

            if clean_text not in seen_content:
                seen_content.add(clean_text)
                fixed_docs.append(doc)
            
        return fixed_docs
