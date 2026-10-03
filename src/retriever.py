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

    @staticmethod
    def normalize_section_digits(query: str) -> list[str]:
        """สร้าง variant ของเลขมาตราทั้งเลขอารบิกและเลขไทย"""
        import re
        arabic_to_thai = str.maketrans("0123456789", "๐๑๒๓๔๕๖๗๘๙")
        thai_to_arabic = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")
        extra = []
        for num in re.findall(r'(?:มาตรา|ม\.)\s*(\d+)', query):
            t_num = num.translate(arabic_to_thai)
            extra.append(f"มาตรา {t_num}")
        for num in re.findall(r'(?:มาตรา|ม\.)\s*([๐-๙]+)', query):
            a_num = num.translate(thai_to_arabic)
            extra.append(f"มาตรา {a_num}")
        return extra

    @staticmethod
    def expand_legal_phrases(query: str) -> list[str]:
        """สกัดถ้อยคำตัวบทกฎหมายทางการ (Statutory Phrases) จากภาษาเล่าเรื่องของผู้ใช้"""
        q = query.lower()
        triggers = [
            {
                "keywords": [("ข่มขู่", "สัญญา"), ("ข่มขู่", "เซ็น"), ("บังคับ", "สัญญา"), ("บังคับ", "เซ็น"),
                             ("ข่มขู่", "ครอบครัว"), ("ข่มขู่", "สมยอม"), ("ข่มขู่", "ยอม"), ("บังคับ", "ข่มขู่")],
                "phrases": [
                    "การแสดงเจตนาเพราะถูกข่มขู่เป็นโมฆียะ",
                    "การข่มขู่ย่อมทำให้การแสดงเจตนาเป็นโมฆียะแม้บุคคลภายนอกจะเป็นผู้ข่มขู่",
                    "ข่มขืนใจผู้อื่นให้กระทำการใด ไม่กระทำการใด หรือจำยอมต่อสิ่งใด",
                    "กรรโชกทรัพย์ ขู่เข็ญว่าจะทำอันตรายต่อชีวิต ร่างกาย เสรีภาพ",
                ]
            },
            {
                "keywords": [("หลอก", "สัญญา"), ("โกง", "สัญญา"), ("หลอก", "เซ็น"), ("กลฉ้อฉล",), ("ฉ้อฉล",), ("หลอกลวง",), ("ฉ้อโกง",)],
                "phrases": [
                    "กลฉ้อฉล สำคัญผิดในสิ่งซึ่งเป็นสาระสำคัญแห่งนิติกรรม เป็นโมฆะ โมฆียะ",
                    "หลอกลวงผู้อื่นด้วยการแสดงข้อความอันเป็นเท็จ ความผิดฐานฉ้อโกง",
                ]
            },
            {
                "keywords": [("ครอบครองปรปักษ์",), ("แย่ง", "ที่ดิน"), ("บุกรุก", "ที่ดิน")],
                "phrases": [
                    "ครอบครองปรปักษ์ อสังหาริมทรัพย์ สงบ เปิดเผย เจตนาเป็นเจ้าของ",
                    "เข้าไปในอสังหาริมทรัพย์ของผู้อื่นเพื่อครอบครอง บุกรุก",
                ]
            },
            {
                "keywords": [("เลิกจ้าง",), ("ไล่ออก",), ("ค่าชดเชย", "งาน"), ("ไม่จ่ายค่าจ้าง",)],
                "phrases": [
                    "บอกกล่าวล่วงหน้า ค่าชดเชย การเลิกจ้าง พ.ร.บ.คุ้มครองแรงงาน",
                    "นายจ้างเลิกจ้างโดยไม่มีความผิด จ่ายค่าชดเชย",
                ]
            },
            {
                "keywords": [("กู้ยืม",), ("ยืมเงิน",), ("ทวงหนี้",), ("ดอกเบี้ยเกิน",)],
                "phrases": [
                    "การกู้ยืมเงิน มีหลักฐานแห่งการกู้ยืมเป็นหนังสือ ป.พ.พ. มาตรา 653",
                    "ทวงถามหนี้ ข่มขู่ ใช้ความรุนแรง ดอกเบี้ยเกินอัตรา",
                ]
            },
            {
                "keywords": [("หมิ่นประมาท",), ("ด่า", "เฟซ"), ("โพสต์", "ด่า"), ("ประจาน",)],
                "phrases": [
                    "ใส่ความผู้อื่นต่อบุคคลที่สาม หมิ่นประมาท โดยการโฆษณา",
                    "นำเข้าสู่ระบบคอมพิวเตอร์ซึ่งข้อมูลคอมพิวเตอร์อันเป็นเท็จ",
                ]
            },
            {
                "keywords": [("ละเมิด",), ("รถชน",), ("ทำร้ายร่างกาย",), ("เรียกค่าเสียหาย",)],
                "phrases": [
                    "จงใจหรือประมาทเลินเล่อ ทำต่อบุคคลอื่นโดยผิดกฎหมาย ละเมิด ค่าสินไหมทดแทน",
                    "ทำร้ายผู้อื่นจนเป็นเหตุให้เกิดอันตรายแก่กายหรือจิตใจ",
                ]
            },
            {
                "keywords": [("มรดก",), ("พินัยกรรม",), ("ทายาท",)],
                "phrases": [
                    "ทายาทโดยธรรม ลำดับทายาท กองมรดก การแบ่งมรดก พินัยกรรม",
                ]
            },
        ]
        matched_phrases = []
        for item in triggers:
            for kw_group in item["keywords"]:
                if all(kw in q for kw in kw_group):
                    for p in item["phrases"]:
                        if p not in matched_phrases:
                            matched_phrases.append(p)
                    break
        return matched_phrases

    def retrieve(self, query, filter_metadata=None, additional_queries=None):
        """
        Retrieves documents using Multi-Aspect Hybrid Search (RRF with Vector + Statutory Concept Injection + BM25).
        `query` ควรเป็น standalone query (ถ้ามีประวัติแชท ให้ rewrite ก่อนส่งเข้ามา)
        """
        search_query = query

        # 1. รวบรวม Search Queries (Original + Statutory Phrase Expansion + Digits)
        extra_queries = list(additional_queries or [])
        extra_queries.extend(self.normalize_section_digits(query))
        extra_queries.extend(self.expand_legal_phrases(query))
        clean_extra = []
        for eq in extra_queries:
            eq_s = eq.strip()
            if eq_s and eq_s != query and eq_s not in clean_extra:
                clean_extra.append(eq_s)

        # 2. Semantic Search (Vector) - ดึง Primary Query ก่อน
        core_vector_docs = self.core_db.similarity_search(search_query, k=200, filter=filter_metadata)
        recent_vector_docs = self.recent_db.similarity_search(search_query, k=50, filter=filter_metadata)
        all_vector_docs = core_vector_docs + recent_vector_docs

        # ถ้ามี Statutory Phrase Expansion ให้ค้นหาและสอดแทรก (Interleave) เอกสารเฉพาะทาง
        if clean_extra:
            aspect_doc_lists = []
            for eq in clean_extra[:4]:  # จำกัดไม่เกิน 4 phrases เพื่อความเร็ว
                docs = self.core_db.similarity_search(eq, k=15, filter=filter_metadata)
                if docs:
                    aspect_doc_lists.append(docs)
            
            if aspect_doc_lists:
                seen_c = {d.page_content for d in all_vector_docs}
                injected = []
                # ดึง top 2 ของแต่ละ aspect มาสอดแทรกไว้ลำดับต้นๆ เพื่อการันตีว่าตัวบทกฎหมายหลักติดโผ
                for alist in aspect_doc_lists:
                    for d in alist[:2]:
                        if d.page_content not in seen_c:
                            seen_c.add(d.page_content)
                            injected.append(d)
                all_vector_docs = injected + all_vector_docs

        if not all_vector_docs:
            return []
            
        # 2.5 Clean Up IAPP natural_text before BM25 processes it
        import json
        for doc in all_vector_docs:
            if doc.page_content.strip().startswith('{"natural_text"'):
                try:
                    data = json.loads(doc.page_content)
                    doc.page_content = data.get('natural_text', doc.page_content)
                except Exception:
                    pass

        # 3. Extract Target Law for Hard Filtering (generic regex-based detection)
        target_law = self.detect_target_law(query)

        if target_law:
            def law_matches(title):
                return target_law in title or title in target_law
            strict_matched_docs = [doc for doc in all_vector_docs if law_matches(doc.metadata.get('title', ''))]
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

        # Calculate weighted RRF Score — vector น้ำหนักมากกว่า BM25 (config)
        # เพราะ BM25 จับ keyword ผิวเผิน เช่น "ประกัน" ในบริบท "ใช้ครอบครัวเป็นตัวประกัน"
        # ไม่ได้หมายถึง พ.ร.บ.ประกันสังคม — semantic search เข้าใจบริบทกว่า
        vw = config.RETRIEVER_VECTOR_WEIGHT
        bw = config.RETRIEVER_BM25_WEIGHT
        scores = {}
        for doc in all_vector_docs:
            content = doc.page_content
            vr = vector_ranks.get(content, 1000)
            br = bm25_ranks.get(content, 1000)
            
            # Weighted RRF: weight / (60 + rank)
            score = (vw / (60 + vr)) + (bw / (60 + br))
            
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
