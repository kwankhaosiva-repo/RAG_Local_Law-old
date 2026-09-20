"""
Discord Bot Adapter
-------------------
รันบอท Discord แยก process จากเว็บเซิร์ฟเวอร์ (Discord ใช้ websocket gateway ของตัวเอง)

ตั้งค่าใน Discord Developer Portal (https://discord.com/developers/applications):
1. New Application → Bot → Reset Token → คัดลอกไปใส่ DISCORD_BOT_TOKEN ใน .env
2. OAuth2 → URL Generator → scopes: bot + applications.commands
   permissions: Send Messages, Read Message History
   → เปิด URL ที่ได้เพื่อเชิญบอทเข้า server
3. หน้า Bot → เปิด "Message Content Intent" (จำเป็นสำหรับคำสั่ง !)

รัน:
    python src/discord_bot.py
"""
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import discord
from discord import app_commands
from discord.ext import commands

import config
from runtime import ask, reset

MAX_TEXT = 1950  # Discord จำกัด 2000 ตัวอักษรต่อข้อความ

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")

intents = discord.Intents.default()
intents.message_content = True  # ต้องเปิดใน Developer Portal ด้วย

bot = commands.Bot(command_prefix="!", intents=intents)

SESSION_PREFIX = "discord:"


def split_message(text: str) -> list:
    """แบ่งข้อความยาวเป็นหลายข้อความ (ตัดที่ขึ้นบรรทัดก่อนถ้าทำได้)"""
    if len(text) <= MAX_TEXT:
        return [text]
    parts = []
    while len(text) > MAX_TEXT:
        cut = text.rfind("\n", 0, MAX_TEXT)
        if cut < MAX_TEXT // 2:
            cut = MAX_TEXT
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text:
        parts.append(text)
    return parts


def answer_text(session_id: str, text: str) -> str:
    """เรียก agent พร้อมจัดการคำสั่ง /reset และ fallback error"""
    if text.strip() in ("/reset", "!reset", "ล้างแชท"):
        reset(session_id)
        return "เริ่มบทสนทนาใหม่แล้วครับ สอบถามกฎหมายได้เลยครับ"
    try:
        return ask(text, session_id)["answer"]
    except Exception as e:
        print(f"[discord_bot] error: {e}")
        return "ขออภัยครับ เกิดข้อผิดพลาดในการประมวลผล กรุณาลองใหม่อีกครั้งครับ"


@bot.event
async def on_ready():
    print(f"[discord_bot] logged in as {bot.user} (id {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"[discord_bot] synced {len(synced)} slash commands")
    except Exception as e:
        print(f"[discord_bot] slash sync failed: {e}")


@bot.event
async def on_message(message: discord.Message):
    # ข้ามข้อความของบอทเองและบอทอื่น
    if message.author.bot:
        return

    # คำสั่ง !ask <คำถาม> หรือ reply บอทด้วยข้อความใดๆ
    content = message.content.strip()
    if content.startswith("!ask "):
        question = content[len("!ask "):].strip()
    elif content.startswith("!reset"):
        await message.reply(reset_reply(message.channel.id))
        await bot.process_commands(message)
        return
    elif bot.user and bot.user.mentioned_in(message):
        question = content.replace(f"<@{bot.user.id}>", "").strip()
    else:
        await bot.process_commands(message)
        return

    if not question:
        await message.reply("พิมพ์คำถามกฎหมายมาได้เลยครับ เช่น `!ask จดทะเบียนธุรกิจต้องทำอย่างไร`")
        return

    async with message.channel.typing():
        session_id = f"{SESSION_PREFIX}{message.channel.id}"
        answer = answer_text(session_id, question)

    for part in split_message(answer):
        await message.reply(part)


def reset_reply(channel_id) -> str:
    reset(f"{SESSION_PREFIX}{channel_id}")
    return "เริ่มบทสนทนาใหม่แล้วครับ สอบถามกฎหมายได้เลยครับ"


# ---------- Slash command: /law <คำถาม> ----------
@bot.tree.command(name="law", description="ถามคำถามกฎหมายไทย")
@app_commands.describe(question="คำถามเกี่ยวกับกฎหมายที่ต้องการสอบถาม")
async def law_command(interaction: discord.Interaction, question: str):
    await interaction.response.defer(thinking=True)
    session_id = f"{SESSION_PREFIX}{interaction.channel_id}"
    answer = answer_text(session_id, question)

    # Slash command จำกัดผลลัพธ์ 2000 ตัวอักษรต่อข้อความเหมือนกัน
    first = answer[:MAX_TEXT]
    rest = answer[MAX_TEXT:]
    await interaction.followup.send(first)
    for part in split_message(rest):
        await interaction.channel.send(part)


def main():
    if not DISCORD_BOT_TOKEN:
        print("[discord_bot] DISCORD_BOT_TOKEN ยังไม่ได้ตั้งค่า — ดูวิธีใน .env_example")
        sys.exit(1)
    bot.run(DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    main()
