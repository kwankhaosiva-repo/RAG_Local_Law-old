import os
import json
import glob
import re
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain.text_splitter import RecursiveCharacterTextSplitter
from tqdm import tqdm
import config

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

def load_jsonl_files(directory):
    print(f"Scanning for JSONL files in {directory}...")
    files = glob.glob(os.path.join(directory, "**/*.jsonl"), recursive=True)
    documents = []
    
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=config.CHUNK_SIZE,
        chunk_overlap=config.CHUNK_OVERLAP,
        separators=["\n\n", "\n", " ", ""]
    )
    
    for f in files:
        base_name = os.path.basename(f)
        year_month = os.path.splitext(base_name)[0]
        
        print(f"Processing {base_name}...")
        
        try:
            with open(f, 'r', encoding='utf-8') as file:
                for line in file:
                    if not line.strip(): continue
                    try:
                        item = json.loads(line)
                        if item.get('success') is not True or 'data' not in item:
                            continue
                            
                        data = item['data']
                        raw_filename = data.get('file_name', 'Unknown')
                        title = data.get('doctitle', os.path.splitext(raw_filename)[0])
                        hierarchy_level, unit_type = get_hierarchy_metadata(title)
                        
                        ocr_results = data.get('ocr_results', [])
                        if not ocr_results: continue
                        
                        full_content = ""
                        for page in ocr_results:
                            page_text = page.get('markdown_output', '')
                            if page_text:
                                full_content += page_text + "\n\n"
                        
                        full_content = full_content.strip()
                        if not full_content: continue
                        
                        # Support "มาตรา", "ข้อ", and their extensions (ทวิ, ตรี, ก, ฮ, etc.)
                        pattern = r'((?:มาตรา|ข้อ)\s*[0-9๑-๙ก-ฮ\d\./]+(?:\s*(?:ทวิ|ตรี|จัตวา|เบญจ|ฉ|สัตต|อัฐ|นพ))?)'
                        sections = re.split(pattern, full_content)
                        
                        if len(sections) > 1:
                            for i in range(1, len(sections), 2):
                                sec_header = sections[i].strip()
                                sec_body = sections[i+1] if i+1 < len(sections) else ""
                                
                                chunks = text_splitter.split_text(sec_body)
                                for j, chunk in enumerate(chunks):
                                    chunk_header = f"กฎหมาย: {title}\nที่มา: IAPP {year_month}\nส่วนของ: {sec_header}"
                                    if len(chunks) > 1:
                                        chunk_header += f" (ส่วนที่ {j+1}/{len(chunks)})"
                                        
                                    chunk_text = f"{chunk_header}\nเนื้อหา: {chunk.strip()}"
                                    metadata = {
                                        "source": "iapp_2025",
                                        "filename": data.get('pdf_file', raw_filename),
                                        "title": title,
                                        "section_header": sec_header,
                                        "unit_type": unit_type,
                                        "hierarchy_level": hierarchy_level,
                                        "publish_date": data.get('publishDate', year_month),
                                        "category": data.get('category', 'New Law')
                                    }
                                    documents.append(Document(page_content=chunk_text, metadata=metadata))
                        else:
                            chunks = text_splitter.split_text(full_content)
                            for j, chunk in enumerate(chunks):
                                chunk_header = f"กฎหมาย: {title}\nที่มา: IAPP {year_month}"
                                if len(chunks) > 1:
                                    chunk_header += f" (ส่วนที่ {j+1}/{len(chunks)})"
                                    
                                chunk_text = f"{chunk_header}\nเนื้อหา: {chunk.strip()}"
                                metadata = {
                                    "source": "iapp_2025",
                                    "filename": data.get('pdf_file', raw_filename),
                                    "title": title,
                                    "unit_type": unit_type,
                                    "hierarchy_level": hierarchy_level,
                                    "publish_date": data.get('publishDate', year_month),
                                    "category": data.get('category', 'New Law')
                                }
                                documents.append(Document(page_content=chunk_text, metadata=metadata))
                                
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            print(f"Error reading {f}: {e}")
            
    return documents

def ingest_recent_law():
    if not os.path.exists(config.RECENT_LAW_DIR):
        print(f"Error: Recent law directory not found at {config.RECENT_LAW_DIR}")
        return

    docs = load_jsonl_files(config.RECENT_LAW_DIR)
    if not docs:
        print("No documents to ingest matching criteria.")
        return

    print(f"Prepared {len(docs)} documents from Recent Law dataset.")

    print(f"Initializing Embedding Model: {config.EMBEDDING_MODEL_NAME}")
    embeddings = HuggingFaceEmbeddings(model_name=config.EMBEDDING_MODEL_NAME)

    print(f"Persisting to ChromaDB collection: {config.COLLECTION_RECENT}")
    vectorstore = Chroma(
        collection_name=config.COLLECTION_RECENT,
        embedding_function=embeddings,
        persist_directory=config.DB_DIR
    )
    
    import time
    batch_size = 500
    print(f"Adding {len(docs)} documents to ChromaDB in batches of {batch_size}...")
    for i in tqdm(range(0, len(docs), batch_size), desc="Indexing Batches"):
        batch = docs[i:i+batch_size]
        vectorstore.add_documents(documents=batch)
        time.sleep(0.5) # ชะลอให้ SQLite เขียนไฟล์ลงฮาร์ดดิสก์ให้ทัน
        
    print("--- Recent Ingestion Success! ---")

if __name__ == "__main__":
    ingest_recent_law()

