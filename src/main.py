from agent import LegalAgent
import warnings

# Suppress warnings for cleaner output
warnings.filterwarnings("ignore")


def main():
    print("----------------------------------------------------------------")
    print("   Thai Law Chatbot (LangGraph + Llama 3.2 Local)   ")
    print("----------------------------------------------------------------")

    try:
        agent = LegalAgent()
    except Exception as e:
        print(f"\nError initializing system: {e}")
        print("Please ensure:")
        print("1. Data has been ingested (run src/ingest_core.py etc)")
        print("2. Ollama is running and model llama3.2 is pulled")
        return

    print("\nSystem ready! คุยได้หลายเทิร์น (จำ context ต่อเนื่อง) — พิมพ์ 'exit' เพื่อออก, 'reset' เพื่อเริ่มใหม่\n")

    session_id = "cli-session"
    while True:
        query = input("คุณ: ").strip()
        if query.lower() in ['exit', 'quit', 'q']:
            break
        if query.lower() == 'reset':
            agent.reset(session_id)
            print("เริ่มบทสนทนาใหม่แล้ว\n")
            continue

        if not query:
            continue

        print("\nกำลังประมวลผล...\n")
        try:
            out = agent.chat(session_id, query)
            print("=" * 50)
            print("บอท:")
            print(out["answer"])
            if out["sources"]:
                print("\nอ้างอิง:")
                for s in out["sources"][:3]:
                    title = s.get('title', '')
                    section = s.get('section', '')
                    print(f"  📄 {title} {section}".rstrip())
            print("=" * 50 + "\n")
        except Exception as e:
            print(f"Error processing query: {e}")


if __name__ == "__main__":
    main()
