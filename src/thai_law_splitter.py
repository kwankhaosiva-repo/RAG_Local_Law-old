"""
Thai Law Section Splitter
-------------------------
แก้ปัญหา truncation ของหัวมาตรา ("มาตรา", "มาตราที่", "ข้อที่") ในการซอย chunk ข้อความกฎหมายไทย

ปัญหาของวิธีเดิม (ingest_core.py / ingest_recent.py):
  1. regex จับแค่ "มาตรา <เลข>" (ช่องว่างบังคับ) — พลาด "มาตราที่ 7/1", "ข้อที่ 5"
  2. "มาตรา 70/1 บรรว" — จับเลขติดคำไทยต่อท้ายเข้ามาในหัวมาตราด้วย (ก-ฮ อยู่ใน character class)
  3. ใช้ RecursiveCharacterTextSplitter ซอย "ต่อจาก" การ split ด้วยหัวมาตราแล้ว
     chunk ที่ 2..n ของมาตราเดียวกันจะไม่มีหัวมาตรากำกับเลย — เสีย context ตอน retrieve

วิธีแก้ของโมดูลนี้:
  1. SECTION_PATTERN ครอบคลุม: "มาตรา|มาตราที่|ข้อ|ข้อที่" + เลขอารบิก/ไทย + เศษส่วน (7/1)
     + ตัวเชื่อม (ทวิ, ตรี, จัตวา, เบญจ, ฉ, สัตต, อัฐ, นพ) + ตัวอักษรกำกับ (ก, ข, 1(ก)) — โดยไม่จับคำอื่นติดมา
  2. split_law_sections: แยกเนื้อหาเป็น (header, body) ครบทุก section
  3. split_law_chunks: ซอย body ยาวเป็นหลาย chunk แต่ **ทุก chunk มีหัวมาตราติดอยู่เสมอ**
     (ส่ง header เป็นบรรทัดแรกของทุก chunk + metadata.section_header ครบทุก chunk)
"""
import re

# หัวมาตรา/ข้อ: "มาตรา 7", "มาตราที่ 7/1", "มาตรา ๓๙", "ข้อ 5", "ข้อที่ ๑๒/๑ ทวิ", "มาตรา 12 ทวิ"
# - เลข: [0-9๐-๙] + / และ . (เศษส่วน 7/1)
# - ตามด้วยตัวเชื่อม/ตัวอักษรกำกับ (มีช่องว่างหรือติดกันก็ได้): ทวิ ตรี จัตวา เบญจ ฉ สัตต อัฐ นพ ก ข ค
# - ไม่จับคำอื่นติดมา (ต้องตามด้วยช่องว่าง/ท้ายบรรทัด/วรรค)
_NUM = r"[0-9๐-๙][0-9๐-๙/\.\-]*"
_SUFFIX = r"(?:\s*(?:ทวิ|ตรี|จัตวา|เบญจา|เบญจ|ฉ|สัตต|อัฐ|นพ|[ก-ฮ]))?"

SECTION_PATTERN = re.compile(
    r"((?:มาตรา|ข้อ)(?:ที่)?\s*" + _NUM + _SUFFIX + r")(?=\s|$|[ เนื้อความมีให้ผู้ต้องห้ามจะได้ว่าอันวรรคตอน])"
)

# ตัวบ่งบรรทัดหัวมาตราใน OCR: "มาตรา 5 " นำหน้าบรรทัดใหม่ตรงๆ ก็จับด้วย split ปกติ
SECTION_SPLIT_PATTERN = re.compile(
    r"(?<!ย)\b((?:มาตรา|ข้อ)(?:ที่)?\s*" + _NUM + _SUFFIX + r")(?=\s+[^\s]|\s*$)",
    re.MULTILINE,
)


def normalize_section_header(header: str) -> str:
    """ทำความสะอาดหัวมาตรา: ตัดช่องว่างเกิน, คงรูป มาตรา 7/1 ทวิ"""
    h = re.sub(r"\s+", " ", header.strip())
    # แปลง "มาตราที่" -> "มาตรา" รักษาความหมายเดิมไว้ใน body อยู่แล้ว
    return h


def split_law_sections(content: str):
    """
    แยกเนื้อหากฎหมายเป็น list ของ (section_header, body)
    ข้อความก่อนมาตราแรก (คำนำ/ชื่อกฎหมาย) จะคืนเป็น ("", preamble)
    """
    parts = SECTION_SPLIT_PATTERN.split(content)
    sections = []
    # parts = [preamble, header1, body1, header2, body2, ...]
    if parts and parts[0].strip():
        sections.append(("", parts[0].strip()))
    for i in range(1, len(parts) - 1, 2):
        header = normalize_section_header(parts[i])
        body = parts[i + 1].strip() if i + 1 < len(parts) else ""
        if body or header:
            sections.append((header, body))
    if len(parts) == 1 and parts[0].strip():
        sections.append(("", parts[0].strip()))
    return sections


def _pack_body_into_chunks(body: str, chunk_size: int, chunk_overlap: int):
    """ซอย body ยาวเป็น chunk ด้วย RecursiveCharacterTextSplitter (sentence/newline-aware)"""
    try:
        from langchain_text_splitters import RecursiveCharacterTextSplitter
    except ImportError:
        from langchain.text_splitter import RecursiveCharacterTextSplitter

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", " ", ""],
    )
    return splitter.split_text(body)


def split_law_chunks(content: str, chunk_size: int = 1000, chunk_overlap: int = 200,
                     title: str = "", source_note: str = "", extra_metadata: dict = None):
    """
    แยก + ซอยเนื้อหากฎหมายเป็น LangChain Documents
    **ทุก chunk มีหัวมาตราติดอยู่เสมอ** แม้มาตรานั้นยาวจนถูกซอยเป็นหลาย chunk
    `source_note` = บรรทัด "ที่มา: ..." (เช่น 'IAPP 2568-01' หรือ 'ราชกิจจานุเบกษา 2568-05-01')
    คืน list ของ Document(page_content, metadata)
    """
    from langchain_core.documents import Document

    docs = []
    for header, body in split_law_sections(content):
        if not body:
            continue
        pieces = _pack_body_into_chunks(body, chunk_size, chunk_overlap)
        n = len(pieces)
        for j, piece in enumerate(pieces):
            # สร้าง header line ที่ติดไปกับทุก chunk
            head_line = f"กฎหมาย: {title}\n" if title else ""
            if source_note:
                head_line += f"ที่มา: {source_note}\n"
            if header:
                if n > 1:
                    head_line += f"ส่วนของ: {header} (ส่วนที่ {j+1}/{n})\n"
                else:
                    head_line += f"ส่วนของ: {header}\n"
            page_content = f"{head_line}เนื้อหา: {piece}"

            metadata = dict(extra_metadata or {})
            if header:
                metadata["section_header"] = header
                metadata["section_id"] = header
                metadata["part"] = f"{j+1}/{n}" if n > 1 else "1/1"
            elif "section_header" not in metadata:
                metadata["section_header"] = "ส่วนเนื้อหา"
                metadata["section_id"] = "ส่วนเนื้อหา"
            docs.append(Document(page_content=page_content, metadata=metadata))
    return docs
