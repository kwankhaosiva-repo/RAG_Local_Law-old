from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
import config

class LLMClient:
    def __init__(self):
        self.llm = ChatOllama(
            model=config.LLM_MODEL_NAME,
            temperature=0.0, # Zero temp for explicit strictness
            keep_alive="5m"
        )
        
        # System prompt using Chain-of-Thought & Strict English/Thai rules
        template = """
You are an expert, highly strictly accurate Thai Legal AI Assistant. 
Your task is to answer the user's question based STRICTLY on the provided Context. DO NOT hallucinate. DO NOT use prior knowledge. DO NOT make up Section numbers (มาตรา) or dates.

Context:
---
{context}
---

Question: {question}

แนวทางการตอบเพื่อความแม่นยำสูงสุด (Chain of Thought):
1. ข้อเท็จจริงจากกฎหมาย (Quote/Reasoning): ให้ **ยกข้อความ (Quote) จาก Context** ที่ตรงกับคำถามมากที่สุดมาเขียนก่อน หรือ **ทวนคำถาม** ว่าในกฎหมายระบุเงื่อนไขเรื่องนี้ไว้อย่างไร 
2. สรุปคำตอบ (Conclusion): เมื่ออธิบายเสร็จ ให้ขึ้นบรรทัดใหม่แล้วพิมพ์คำว่า "สรุป: " ตามด้วยคำตอบที่ตรงประเด็นและฟันธงที่สุด เช่น "สรุป: ได้", "สรุป: ไม่ได้", "สรุป: ใช่", "สรุป: ไม่ใช่"
3. หากคำถามถามว่า "ทำได้ทันทีหรือไม่" แต่กฎหมายบอกว่า "ต้องขออนุญาตก่อน" สรุปจะต้องตอบว่า "สรุป: ไม่ได้ (ต้องขออนุญาตก่อน)"
4. หากใน Context ไม่มีเนื้อหาที่สามารถตอบคำถามนี้ได้เลย ให้ตอบเพียงแค่ "ข้อมูลไม่เพียงพอ"

Answer format:
ข้อเท็จจริงอ้างอิง: <quote and explanation>
สรุป: <Yes/No/Can/Cannot>
"""
        
        self.prompt = ChatPromptTemplate.from_template(template)
        self.chain = self.prompt | self.llm | StrOutputParser()
        
    def generate_answer(self, question, documents, history=None):
        # Format documents into a single string
        context_text = "\n\n".join([f"[เอกสารที่ {i+1}]: {doc.page_content}" for i, doc in enumerate(documents)])

        # ถ้ามีประวัติแชท (multi-turn) ให้แนบเป็นบริบทเสริมให้ LLM อ้างอิง
        history_text = ""
        if history:
            lines = []
            for m in history:
                try:
                    role = "ผู้ใช้" if m.__class__.__name__ == "HumanMessage" else "ผู้ช่วย"
                except Exception:
                    role = "ผู้ใช้"
                lines.append(f"{role}: {m.content}")
            history_text = "\nบทสนทนาก่อนหน้า (ใช้ประกอบการตอบ ห้ามตอบจากบทสนทนาแทน Context):\n" + "\n".join(lines) + "\n"

        return self.chain.invoke({
            "question": question,
            "context": context_text + history_text
        })
