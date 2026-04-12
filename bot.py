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
# DISCORD
# =========================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

# =========================
# DB
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
# SYSTEM
# =========================
setup_sessions = {}
processed = set()
cooldown = {}

# =========================
# OCR
# =========================
def try_ocr(image_url):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(image_url, headers=headers, timeout=10)
        img = Image.open(BytesIO(r.content)).convert("RGB")
        return pytesseract.image_to_string(img).lower()
    except:
        return ""

# =========================
# SAFE JSON PARSER (🔥 FIX)
# =========================
def safe_json(raw):
    try:
        raw = raw.strip()

        # remove markdown
        raw = re.sub(r"```json|```", "", raw)

        start = raw.find("{")
        end = raw.rfind("}") + 1

        if start != -1 and end != -1:
            raw = raw[start:end]

        return json.loads(raw)

    except Exception as e:
        print("JSON ERROR:", e)
        print("RAW:", raw)
        return None

# =========================
# LOG SYSTEM
# =========================
async def send_log(guild, status, user, user_id, roles=None, reason=None, action=None):
    cursor.execute("SELECT log_channel_id FROM guilds WHERE guild_id=?", (guild.id,))
    row = cursor.fetchone()
    if not row:
        return

    ch = guild.get_channel(row[0])
    if not ch:
        return

    embed = discord.Embed(
        title="🟢 APPROVED" if status == "APPROVED" else "🔴 REJECTED",
        color=discord.Color.green() if status == "APPROVED" else discord.Color.red()
    )

    embed.add_field(name="User", value=f"{user} ({user_id})", inline=False)

    if status == "APPROVED":
        embed.add_field(name="Roles", value=", ".join(roles) if roles else "None", inline=False)
    else:
        embed.add_field(name="Reason", value=reason or "No match", inline=False)
        embed.add_field(name="Action", value=action or "Guest role", inline=False)

    await ch.send(embed=embed)

# =========================
# AI
# =========================
def analyze(url, guild_id):
    cursor.execute("SELECT alliance, tag FROM alliances WHERE guild_id=?", (guild_id,))
    alliances = cursor.fetchall()

    data = "\n".join([f"- {a} | {t}" for a, t in alliances])
    ocr_text = try_ocr(url)

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
{data}

OCR:
{ocr_text}

RULES:
- OCR can be wrong or empty
- ALWAYS use image as main source
- If ANY alliance OR tag is visible → APPROVE
- Only reject if absolutely nothing matches

IMPORTANT:
Return ONLY valid JSON (no markdown, no text):

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
# SETUP
# =========================
@tree.command(name="setup", description="setup bot")
async def setup(interaction: discord.Interaction):
    setup_sessions[interaction.user.id] = {"step": 0, "alliances": [], "config": {}}
    await interaction.response.send_message("Start setup", ephemeral=True)

# =========================
# READY
# =========================
@bot.event
async def on_ready():
    await tree.sync()
    print("Online")

# =========================
# VERIFY
# =========================
@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return

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

    await message.channel.send("Checking...")

    raw = analyze(att.url, message.guild.id)
    data = safe_json(raw)

    guest = message.guild.get_role(guest_role_id)
    roles = []

    # 🔥 FIXED LOGIC
    if data and data.get("status") == "APPROVED":

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
            roles.append("Guest")

        await message.channel.send("✅ APPROVED")

    else:
        if guest:
            await message.author.add_roles(guest)

        await message.channel.send("❌ REJECTED")

# =========================
# WEB
# =========================
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot running"

Thread(target=app.run, kwargs={"host":"0.0.0.0","port":8080}).start()

bot.run(DISCORD_TOKEN)