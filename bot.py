import discord
import os
import time
import sqlite3
from dotenv import load_dotenv
from openai import OpenAI
from flask import Flask
from threading import Thread
from discord import app_commands
import json
import re

import pytesseract
from PIL import Image
import requests
from io import BytesIO

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client_ai = OpenAI(api_key=OPENAI_API_KEY)

# =========================
# DISCORD SETUP
# =========================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

# =========================
# DATABASE
# =========================
conn = sqlite3.connect("bot.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS guilds (
    guild_id INTEGER PRIMARY KEY,
    channel_id INTEGER,
    guest_role_id INTEGER,
    log_channel_id INTEGER
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS alliances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER,
    alliance TEXT,
    tag TEXT,
    role_id INTEGER
)
""")

conn.commit()

# =========================
# MEMORY
# =========================
sessions = {}
processed = set()
cooldown = {}

# =========================
# OCR
# =========================
def try_ocr(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=10)
        img = Image.open(BytesIO(r.content)).convert("RGB")
        return pytesseract.image_to_string(img).lower()
    except:
        return ""

# =========================
# SAFE JSON PARSER
# =========================
def parse_json(text):
    try:
        text = text.strip()
        text = re.sub(r"```json|```", "", text)

        start = text.find("{")
        end = text.rfind("}") + 1

        if start == -1 or end == -1:
            return None

        return json.loads(text[start:end])

    except Exception as e:
        print("JSON ERROR:", e)
        return None

# =========================
# AI ANALYSIS
# =========================
def analyze(url, guild_id):
    cursor.execute("SELECT alliance, tag FROM alliances WHERE guild_id=?", (guild_id,))
    alliances = cursor.fetchall()

    valid = "\n".join([f"- {a} | {t}" for a, t in alliances])
    ocr = try_ocr(url)

    try:
        res = client_ai.responses.create(
            model="gpt-4o-mini",
            input=[{
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": f"""
You are a Discord verification AI.

VALID ALLIANCES:
{valid}

OCR TEXT:
{ocr}

RULES:
- OCR may be wrong
- IMAGE is leading source
- If ANY alliance or tag is visible → APPROVE
- Only reject if nothing matches at all

Return ONLY JSON:
{{
 "status": "APPROVED or REJECTED",
 "matches": [{{"alliance": "name"}}],
 "reason": "short reason"
}}
"""
                    },
                    {
                        "type": "input_image",
                        "image_url": url
                    }
                ]
            }]
        )

        return res.output_text

    except Exception as e:
        print("AI ERROR:", e)
        return '{"status":"REJECTED","matches":[],"reason":"AI failed"}'

# =========================
# SETUP COMMAND (FIXED STATE FLOW)
# =========================
@tree.command(name="setup", description="setup bot")
async def setup(interaction: discord.Interaction):
    sessions[interaction.user.id] = {
        "step": 1,
        "alliances": [],
        "current": {},
        "config": {}
    }

    await interaction.response.send_message(
        "🏷️ Typ alliance naam:",
        ephemeral=True
    )

# =========================
# READY
# =========================
@bot.event
async def on_ready():
    await tree.sync()
    print("ONLINE")

# =========================
# MESSAGE FLOW (SETUP ENGINE)
# =========================
@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return

    # =========================
    # SETUP FLOW
    # =========================
    if message.author.id in sessions:
        s = sessions[message.author.id]
        c = message.content.strip()

        # STEP 1: alliance
        if s["step"] == 1:
            s["current"] = {"alliance": c}
            s["step"] = 2
            await message.channel.send("🏷️ TAG?")
            return

        # STEP 2: tag
        if s["step"] == 2:
            s["current"]["tag"] = c
            s["step"] = 3
            await message.channel.send("⭐ ROLE MENTION?")
            return

        # STEP 3: role
        if s["step"] == 3:
            if not message.role_mentions:
                await message.channel.send("❌ mention role")
                return

            s["current"]["role_id"] = message.role_mentions[0].id
            s["alliances"].append(s["current"])
            s["step"] = 4
            await message.channel.send("➕ nog eentje? yes/no")
            return

        # STEP 4: loop or finish
        if s["step"] == 4:
            if c.lower() == "yes":
                s["step"] = 1
                await message.channel.send("🏷️ nieuwe alliance naam:")
                return

            text = "SETUP DONE:\n\n"
            for a in s["alliances"]:
                text += f"{a['alliance']} → {a['tag']}\n"

            await message.channel.send(text)
            sessions.pop(message.author.id)
            return

    # =========================
    # VERIFY SYSTEM
    # =========================
    cursor.execute("SELECT channel_id, guest_role_id FROM guilds WHERE guild_id=?", (message.guild.id,))
    cfg = cursor.fetchone()

    if not cfg:
        return

    channel_id, guest_role_id = cfg

    if message.channel.id != channel_id:
        return

    if not message.attachments:
        return

    att = message.attachments[0]

    if att.url in processed:
        return
    processed.add(att.url)

    await message.channel.send("🔍 Checking...")

    data = parse_json(analyze(att.url, message.guild.id))

    if not data:
        data = {"status": "REJECTED", "matches": [], "reason": "parse fail"}

    guest = message.guild.get_role(guest_role_id)
    roles = []

    if data["status"] == "APPROVED":

        for m in data.get("matches", []):
            key = m.get("alliance")

            cursor.execute("""
                SELECT role_id FROM alliances WHERE guild_id=? AND alliance=?
            """, (message.guild.id, key))

            r = cursor.fetchone()
            if r:
                role = message.guild.get_role(r[0])
                if role:
                    await message.author.add_roles(role)
                    roles.append(role.name)

        if guest and not roles:
            await message.author.add_roles(guest)

        await message.channel.send("✅ APPROVED")

    else:
        if guest:
            await message.author.add_roles(guest)

        await message.channel.send("❌ REJECTED")

# =========================
# WEB SERVER
# =========================
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot running"

Thread(target=app.run, kwargs={"host":"0.0.0.0","port":8080}).start()

bot.run(DISCORD_TOKEN)