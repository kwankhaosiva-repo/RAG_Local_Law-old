"""
Test: ตรวจจับปัญหาการตัด (truncation) ของหัวมาตราใน chunking เดิม

ปัญหาที่พบใน pipeline เดิม:
  1. ใช้ RecursiveCharacterTextSplitter ซอย "ต่อจาก" การ split ด้วยหัวมาตราแล้ว
     chunk ที่ 2..n ของมาตราเดียวกันจะ "หาย" — ไม่มีหัวมาตรากำกับเลย
  2. regex เดิมจับ "มาตราที่" ไม่ได้ (จับแค่ "มาตรา <ตัวเลข>" แล้วถัดไปด้วยช่องว่างเท่านั้น)
     variants ที่พบจริงในกฎหมายไทย: "มาตราที่ 7/1", "มาตรา ๓๙" (เลขไทย),
     "มาตรา 70/1 บรรว" (ตัวเลขติดคำ), "ข้อที่ 5", "มาตรา ๑๒/๑ ทวิ"
"""
import re
import sys

sys.path.insert(0, "src")

SAMPLE_LAW = """พระราชบัญญัติตัวอย่าง พ.ศ. 2560
มาตรา 5 ในกฎหมายนี้ หมายความว่า สถาบันการเงินต้องจัดให้มีระบบการกำกับดูแลภายในที่เพียงพอและเหมาะสม เพื่อให้การประกอบธุรกิจเป็นไปอย่างมีธรรมาภิบาล มีความมั่นคงปลอดภัย และมีการบริหารความเสี่ยงอย่างมีประสิทธิผล ทั้งนี้ ให้เป็นไปตามหลักเกณฑ์ที่กำหนดในกฎกระทรวง
มาตราที่ 7/1 บุคคลซึ่งได้รับใบอนุญาตประกอบธุรกิจตามกฎหมายว่าด้วยธุรกิจสถาบันการเงิน ต้องแสดงใบอนุญาตไว้ ณ สถานที่ประกอบธุรกิจนั้นในที่เปิดเผย และห้ามนำไปจำนำ จำหน่าย หรือโอนให้แก่ผู้อื่นโดยไม่ได้รับอนุญาตจากผู้รับใบอนุญาต
มาตรา ๓๙ เมื่อเห็นสมควร รัฐมนตรีมีอำนาจออกกฎกระทรวงเพื่อประโยชน์ในการบริหารราชการ โดยให้มีผลใช้บังคับได้ในกรณีที่มีความจำเป็นฉุกเฉินหรือเพื่อป้องกันภัยหรือรักษาความสงบเรียบร้อยหรือความสะอาดของบ้านเมือง และเมื่อเหตุที่ทำให้ต้องออกกฎกระทรวงนั้นสิ้นสุดลงแล้ว ให้ยกเลิกกฎกระทรวงนั้นเสีย
ข้อที่ 5 ผู้ขอรับใบอนุญาตต้องยื่นคำขอต่อนายทะเบียนตามแบบและมีรายละเอียดดังต่อไปนี้ พร้อมด้วยเอกสารและหลักฐานที่จำเป็นประกอบคำขอ ค่าธรรมเนียมในการขอรับใบอนุญาตตามที่กำหนดในกฎกระทรวง และเมื่อได้รับใบอนุญาตแล้วต้องชำระค่าธรรมเนียมประจำปีด้วย"""


def old_split(content):
    """วิธีเดิม (จาก ingest_core.py) — regex จับเฉพาะ 'มาตรา <เลข>' แบบมีช่องว่าง"""
    text_splitter = _old_splitter()
    pattern = r'((?:มาตรา|ข้อ)\s*[0-9๑-๙ก-ฮ\./]+(?:\s*(?:ทวิ|ตรี|จัตวา|เบญจ|ฉ|สัตต|อัฐ|นพ))?)'
    sections = re.split(pattern, content)
    out = []
    if len(sections) > 1:
        for i in range(1, len(sections), 2):
            sec_header = sections[i].strip()
            sec_body = sections[i + 1] if i + 1 < len(sections) else ""
            chunks = text_splitter.split_text(sec_body)
            for j, chunk in enumerate(chunks):
                out.append((sec_header if len(chunks) == 1 else f"{sec_header} (ส่วนที่ {j+1}/{len(chunks)})", chunk))
    return out


def _old_splitter():
    try:
        from langchain.text_splitter import RecursiveCharacterTextSplitter
    except ImportError:
        from langchain_text_splitters import RecursiveCharacterTextSplitter
    return RecursiveCharacterTextSplitter(
        chunk_size=150,  # ตั้งเล็กเพื่อบังคับให้เกิดกรณี chunk ยาวเกิน (ทดสอบ truncation)
        chunk_overlap=20,
        separators=["\n\n", "\n", " ", ""],
    )


def test_old_approach_problems():
    print("=" * 60)
    print("A) ทดสอบ regex เดิมกับ variants หัวมาตรา")
    print("=" * 60)
    old_pattern = r'((?:มาตรา|ข้อ)\s*[0-9๑-๙ก-ฮ\./]+(?:\s*(?:ทวิ|ตรี|จัตวา|เบญจ|ฉ|สัตต|อัฐ|นพ))?)'

    cases = {
        "มาตราที่ 7/1 ห้ามจำนำ": "มาตราที่",       # ❌ regex เดิมต้อง fail (มี 'ที่' ติดกัน)
        "มาตรา ๓๙ อำนาจรัฐมนตรี": "มาตรา ๓๙",       # ✅ เลขไทย OK
        "ข้อที่ 5 ผู้ขอรับใบอนุญาต": "ข้อที่",        # ❌ fail
        "มาตรา 70/1 บรรว": "มาตรา 70/1",            # ⚠️ ติดคำ 'บรรว'
        "มาตรา 12 ทวิ ว่าด้วยทุน": "มาตรา 12 ทวิ",   # ✅ OK
    }
    old_fails = []
    for text, expected in cases.items():
        m = re.search(old_pattern, text)
        got = m.group(1) if m else None
        # ต้องจับได้ "อย่างถูกต้อง" (ตัด 'บรรว' ออกเองไม่ได้ = จับเกิน)
        ok = got is not None and (got == expected or (expected in got and "บรรว" not in got))
        status = "OK " if ok else "FAIL"
        print(f"  [{status}] {text!r:45} -> {got!r}")
        if not ok:
            old_fails.append(text)

    print(f"\n  regex เดิมพลาด {len(old_fails)}/{len(cases)} cases")
    assert len(old_fails) >= 2, "regex เดิมควรพลาดอย่างน้อย 2 cases (มาตราที่/ข้อที่) — ถ้าไม่พลาดแปลว่า test ผิด"

    print()
    print("=" * 60)
    print("B) ทดสอบ truncation: หัวมาตราถูกตัดกลางคำ / เลขมาตราหลุดเข้าเนื้อหา")
    print("=" * 60)
    chunks = old_split(SAMPLE_LAW)
    # bug จริงของวิธีเดิม: regex จับ "มาตราที่" ได้แค่ "มาตราท" → เลขมาตรา "ี่ 7/1" หลุดไปอยู่หน้า body
    truncated = [
        c for c in chunks
        if c[0] in ("มาตราท", "ข้อท") or c[1].startswith("ี่")
    ]
    for header, body in chunks:
        print(f"  header={header!r:35} | body[:50]={body[:50]!r}...")
    print(f"\n  chunk ที่หัวมาตราถูกตัดกลางคำ (เลขมาตราหายจาก metadata): {len(truncated)}/{len(chunks)}")
    # "มาตราที่ 7/1" และ "ข้อที่ 5" ต้องถูกตัดผิดทั้งคู่
    assert len(truncated) >= 2, "ควรมี chunk ที่หัวมาตราถูกตัดกลางคำอย่างน้อย 2 กลุ่ม (มาตราที่/ข้อที่)"

    print("\n[A+B] ยืนยันปัญหาของวิธีเดิมเรียบร้อย — ต่อไปทดสอบ splitter ใหม่")
    return True


def test_new_splitter():
    print()
    print("=" * 60)
    print("C) ทดสอบ thai_law_splitter ใหม่")
    print("=" * 60)
    from thai_law_splitter import split_law_sections, split_law_chunks

    sections = split_law_sections(SAMPLE_LAW)
    print(f"  พบ {len(sections)} sections:")
    for header, body in sections:
        print(f"    header={header!r:35} | body len={len(body)}")

    # ต้องจับได้ครบ 4 มาตรา/ข้อ
    headers = [h for h, _ in sections]
    assert any("7/1" in h for h in headers), "ต้องจับ 'มาตราที่ 7/1' ได้"
    assert any("๓๙" in h for h in headers), "ต้องจับเลขไทยได้"
    assert any(h.startswith("ข้อ") and "5" in h for h in headers), "ต้องจับ 'ข้อที่ 5' ได้"
    assert not any(h in ("มาตราท", "ข้อท") for h in headers), "ห้ามตัดกลางคำอีกต่อไป"

    # chunk ทุกอันต้องมีหัวมาตราติดอยู่เสมอ (ยกเว้น preamble = บรรทัดชื่อกฎหมายก่อนมาตราแรก)
    chunks = split_law_chunks(SAMPLE_LAW, chunk_size=150, chunk_overlap=20)
    print(f"\n  ซอยเป็น {len(chunks)} chunks (chunk_size=150):")
    bad = 0
    for doc in chunks:
        meta_header = doc.metadata.get("section_header", "")
        if meta_header and meta_header != "ส่วนเนื้อหา":
            ok = ("ส่วนของ:" in doc.page_content and "เนื้อหา:" in doc.page_content
                  and meta_header in doc.page_content)
        else:
            ok = "เนื้อหา:" in doc.page_content  # preamble
        print(f"    [{'OK ' if ok else 'FAIL'}] {doc.page_content[:70]!r}...")
        if not ok:
            bad += 1
    assert bad == 0, f"chunk {bad} ตัวไม่มีหัวมาตรากำกับ — ยังเสีย context อยู่"

    # metadata ต้องติด section_header ทุก chunk
    for doc in chunks:
        assert doc.metadata.get("section_header"), "metadata.section_header ต้องมีค่าทุก chunk"
    print("\n  [PASS] ทุก chunk มีหัวมาตรา + metadata ครบ")
    return True


if __name__ == "__main__":
    test_old_approach_problems()
    test_new_splitter()
    print("\n" + "=" * 60)
    print("ALL TESTS PASSED ✅")
    print("=" * 60)
