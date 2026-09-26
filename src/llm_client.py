from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import Runnable
import os

import config


def _make_provider_llm(provider: str):
    """คืน LLM ของ provider ตัวเดียว (ollama/openai/.../gateway)"""
    provider = (provider or "ollama").lower()

    if provider == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(
            model=config.LLM_MODEL_NAME,
            temperature=0.0,  # Zero temp for explicit strictness
            base_url=config.OLLAMA_HOST,
            keep_alive="5m",
        )

    if provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=config.OPENAI_MODEL,
            temperature=0.0,
            api_key=config.OPENAI_API_KEY,
        )

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=config.ANTHROPIC_MODEL,
            temperature=0.0,
            api_key=config.ANTHROPIC_API_KEY,
        )

    if provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            model=config.GOOGLE_MODEL,
            temperature=0.0,
            google_api_key=config.GOOGLE_API_KEY,
        )

    if provider == "openrouter":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=config.OPENROUTER_MODEL,
            temperature=0.0,
            api_key=config.OPENROUTER_API_KEY,
            base_url=config.OPENROUTER_BASE_URL,
        )

    if provider == "groq":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=config.GROQ_MODEL,
            temperature=0.0,
            api_key=config.GROQ_API_KEY,
            base_url=config.GROQ_BASE_URL,
        )

    if provider == "mistral":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=config.MISTRAL_MODEL,
            temperature=0.0,
            api_key=config.MISTRAL_API_KEY,
            base_url=config.MISTRAL_BASE_URL,
        )

    if provider == "cloudflare":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=config.CLOUDFLARE_MODEL,
            temperature=0.0,
            api_key=config.CLOUDFLARE_API_KEY,
            base_url=config.CLOUDFLARE_BASE_URL,
        )

    # --- UNOROUTER (unorouter.com — free OpenAI-compatible) ---
    if provider == "unorouter":
        if not config.UNOROUTER_BASE_URL:
            raise ValueError("UNOROUTER_BASE_URL is required")
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=config.UNOROUTER_MODEL,
            temperature=0.0,
            api_key=config.UNOROUTER_API_KEY or "not-needed",
            base_url=config.UNOROUTER_BASE_URL,
        )

    # --- Generic OpenAI-compatible gateway (9router.com ฯลฯ) ---
    # ใช้ได้กับทุกเจ้าที่ API เป็นมาตรฐาน OpenAI: แค่เปลี่ยน GATEWAY_BASE_URL
    if provider == "gateway":
        if not config.GATEWAY_BASE_URL:
            raise ValueError("GATEWAY_BASE_URL is required when LLM_PROVIDER=gateway")
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=config.GATEWAY_MODEL,
            temperature=0.0,
            api_key=config.GATEWAY_API_KEY or "not-needed",  # บางเจ้า local/free ไม่ต้องมี key
            base_url=config.GATEWAY_BASE_URL,
        )

    raise ValueError(
        f"Unknown LLM_PROVIDER: {provider!r} "
        "(use ollama/openai/anthropic/google/openrouter/groq/mistral/cloudflare/gateway/unorouter)"
    )


# key ที่จำเป็นต่อ provider — ใช้กรองตัวที่ยังไม่มี key ออกจาก failover chain
_PROVIDER_REQUIRED_KEY = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "cloudflare": "CLOUDFLARE_API_KEY",
    "gateway": "GATEWAY_API_KEY",  # บางเจ้าไม่บังคับ — ถ้าไม่มี key ก็ยังลองได้
    "unorouter": "UNOROUTER_API_KEY",
    "ollama": None,
}


def _provider_ready(provider: str) -> bool:
    key_name = _PROVIDER_REQUIRED_KEY.get(provider)
    if not key_name:
        return True
    return bool(getattr(config, key_name, "") or os.environ.get(key_name))


class FallbackLLM(Runnable):
    """ห่อ LLM หลายตัวเรียงตามลำดับ — ตัวไหน error/quota หมด ข้ามไปตัวถัดไปทันที

    Inherit จาก langchain Runnable แท้จริง เพื่อให้ใช้ใน chain แบบ
    `prompt | llm | parser` ได้ปกติ — เดิมเป็น plain class ทำให้ LangChain
    ปฏิเสธ ("Expected a Runnable")
    """

    def __init__(self, llms: list):
        super().__init__()
        self._llms = llms

    def invoke(self, input_data, config=None, **kwargs):
        last_err: Exception | None = None
        for llm in self._llms:
            try:
                return llm.invoke(input_data, config=config, **kwargs)
            except Exception as e:
                last_err = e
                print(
                    f"[llm_client] {llm.__class__.__name__} failed, "
                    f"trying next in failover chain: {type(e).__name__}: {e}"
                )
        raise last_err  # ทุกตัวล้มเหลว

    def bind(self, **kwargs):
        # bind ของ chat model (เช่น tools) — fallback ต่อระดับ invoke ตามเดิม
        return FallbackLLM([llm.bind(**kwargs) for llm in self._llms])

    @property
    def providers(self) -> list[str]:
        return [llm.__class__.__name__ for llm in self._llms]


def make_llm():
    """คืน LLM หลัก — ถ้าตั้ง LLM_FAILOVER_CHAIN ไว้ จะได้ FallbackLLM ที่
    ส่งต่อ context/prompt เดิมให้ provider ถัดไปเมื่อตัวหลักล้ม (quota หมด/ล่ม)
    ตัวอย่าง: LLM_FAILOVER_CHAIN="gateway,groq,mistral"
    """
    provider = (config.LLM_PROVIDER or "ollama").lower()
    primary = _make_provider_llm(provider)

    chain = [
        p.strip().lower()
        for p in (config.LLM_FAILOVER_CHAIN or "").split(",")
        if p.strip()
    ]
    llms = [primary]
    for p in chain:
        if p == provider:
            continue
        if not _provider_ready(p):
            print(f"[llm_client] failover skip {p!r} (missing API key)")
            continue
        try:
            llms.append(_make_provider_llm(p))
        except Exception as e:
            print(f"[llm_client] failover skip {p!r}: {e}")

    if len(llms) == 1:
        return primary
    print(f"[llm_client] failover chain: {[type(x).__name__ for x in llms]}")
    return FallbackLLM(llms)


class LLMClient:
    def __init__(self):
        self.llm = make_llm()

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
