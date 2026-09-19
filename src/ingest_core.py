import json
import os
import re
import config
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from langchain.schema import Document
from tqdm import tqdm
from huggingface_hub import snapshot_download
from langchain.text_splitter import RecursiveCharacterTextSplitter

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
        return 2, "มาตรา" # Default for unknown Structural laws

def ingest_core_law():
    print("Starting Ingestion (Filter: Year 2500+ AND is_latest only)...")
    
    dataset_path = os.path.join(config.DATASETS_DIR, "ocs-krisdika_manual")
    
    all_files = []
    if os.path.exists(dataset_path):
        for root, dirs, files in os.walk(dataset_path):
            for f in files:
                if f.endswith(".jsonl"):
                    # --- FILTER YEAR (2500+) ---
                    # ดึงปีจากชื่อโฟลเดอร์ เช่น data/2562/...
                    year_match = re.search(r'(\d{4})', root)
                    if year_match:
                        year = int(year_match.group(1))
                        # แปลง ค.ศ. เป็น พ.ศ. โดยประมาณถ้าเจอตัวเลขน้อยกว่า 2400
                        actual_be_year = year if year > 2400 else year + 543
                        if actual_be_year >= 2500:
                            all_files.append(os.path.join(root, f))
    
    documents = []
    print(f"Processing {len(all_files)} filtered files...")
    
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=config.CHUNK_SIZE,
        chunk_overlap=config.CHUNK_OVERLAP,
        separators=["\n\n", "\n", " ", ""]
    )
    
    for file_path in tqdm(all_files):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    data = json.loads(line)
                    
                    # --- FILTER LATEST ---
                    if not data.get('is_latest', False): continue 
                    
                    title = data.get('title', 'Unknown')
                    hierarchy_level, unit_type = get_hierarchy_metadata(title)
                    sections = data.get('sections', [])
                    publish_date = data.get('publish_date', '')
                    reference_url = data.get('reference_url', '')
                    category = data.get('category', '')
                    
                    for sec in sections:
                        content = sec.get('content', '').strip()
                        if not content: continue
                        
                        # ใช้ Regex ควานหาชื่อ มาตรา/ข้อ จากเนื้อหาโดยตรง เพื่อเลี่ยงการดึง sectionId (int) ผิดๆ มาใช้
                        pattern = r'((?:มาตรา|ข้อ)\s*[0-9๑-๙ก-ฮ\./]+(?:\s*(?:ทวิ|ตรี|จัตวา|เบญจ|ฉ|สัตต|อัฐ|นพ))?)'
                        match = re.search(pattern, content)
                        section_label = match.group(1).strip() if match else ""
                        
                        chunks = text_splitter.split_text(content)
                        for j, chunk in enumerate(chunks):
                            if section_label:
                                chunk_header = f"กฎหมาย: {title}\nส่วนของ: {section_label}"
                            else:
                                chunk_header = f"กฎหมาย: {title}"
                            
                            if len(chunks) > 1:
                                chunk_header += f" (ส่วนที่ {j+1}/{len(chunks)})"
                                
                            full_text = f"{chunk_header}\nเนื้อหา: {chunk.strip()}"
                            
                            metadata = {
                                "source": "ocs-krisdika",
                                "title": title,
                                "section_id": section_label if section_label else "ส่วนเนื้อหา",
                                "unit_type": unit_type,
                                "hierarchy_level": hierarchy_level,
                                "publish_date": publish_date,
                                "reference_url": reference_url,
                                "category": category
                            }
                            documents.append(Document(page_content=full_text, metadata=metadata))
        except Exception:
            continue

    print(f"Total documents to index: {len(documents)}")
    
    if not documents:
        print("No documents found!")
        return

    # 2. Ingest to ChromaDB (Manual Batching to prevent HNSW corruption)
    print(f"Initializing Embeddings and Vector DB...")
    embeddings = HuggingFaceEmbeddings(model_name=config.EMBEDDING_MODEL_NAME)
    
    vectorstore = Chroma(
        collection_name=config.COLLECTION_CORE,
        embedding_function=embeddings,
        persist_directory=config.DB_DIR
    )
    
    import time
    batch_size = 500
    print(f"Adding {len(documents)} documents to ChromaDB in batches of {batch_size}...")
    
    for i in tqdm(range(0, len(documents), batch_size), desc="Indexing Batches"):
        batch = documents[i:i + batch_size]
        vectorstore.add_documents(batch)
        time.sleep(0.5) # ชะลอให้ SQLite ค่อยๆ บันทึกไฟล์ .bin ลงฮาร์ดดิสก์ให้ทัน ไม่ให้พัง
        
    print("--- Core Ingestion Success! ---")

if __name__ == "__main__":
    ingest_core_law()
