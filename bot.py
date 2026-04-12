import discord
import os
import time
import sqlite3
from dotenv import load_dotenv
from openai import OpenAI
from flask import Flask
from threading import Thread
from discord import app_commands
from discord.ui import View, Button

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
# FLASK KEEP ALIVE
# =========================
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is alive"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# =========================
# DATABASE
# =========================
conn = sqlite3.connect("bot.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS guilds (
    guild_id INTEGER PRIMARY KEY,
    alliance TEXT,
    tag TEXT,
    channel_id INTEGER,
    role_id INTEGER,
    guest_role_id INTEGER,
    log_channel_id INTEGER
)
""")
conn.commit()

def save_guild(gid, data):
    cursor.execute("""
    INSERT OR REPLACE INTO guilds VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        gid,
        data.get("alliance"),
        data.get("tag"),
        data.get("channel_id"),
        data.get("role_id"),
        data.get("guest_role_id"),
        data.get("log_channel_id")
    ))
    conn.commit()

def load_guild(gid):
    cursor.execute("SELECT * FROM guilds WHERE guild_id = ?", (gid,))
    row = cursor.fetchone()
    if not row:
        return None

    return {
        "alliance": row[1],
        "tag": row[2],
        "channel_id": row[3],
        "role_id": row[4],
        "guest_role_id": row[5],
        "log_channel_id": row[6],
    }

# =========================
# SYSTEM
# =========================
processed_images = set()
user_cooldown = {}
setup_sessions = {}

# =========================
# 🧠 FIX: SETUP INPUT HANDLER (NIEUW)
# =========================
@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return

    # =========================
    # 🧩 SETUP INPUT FLOW
    # =========================
    session = setup_sessions.get(message.author.id)

    if session:
        step = session["step"]
        data = session["data"]

        content = message.content.strip()

        # STEP MAP
        if step == 0:
            data["alliance"] = content

        elif step == 1:
            data["tag"] = content

        elif step == 2 and message.channel_mentions:
            data["channel_id"] = message.channel_mentions[0].id

        elif step == 3 and message.role_mentions:
            data["role_id"] = message.role_mentions[0].id

        elif step == 4 and message.role_mentions:
            data["guest_role_id"] = message.role_mentions[0].id

        elif step == 5 and message.channel_mentions:
            data["log_channel_id"] = message.channel_mentions[0].id

        session["data"] = data

        # auto move next step
        session["step"] += 1

        await message.delete()
        return

    # =========================
    # IMAGE CHECK (JOUW BESTAANDE LOGIC)
    # =========================
    config = load_guild(message.guild.id)
    if not config:
        return

    if message.channel.id != config["channel_id"]:
        return

    if not message.attachments:
        return

    att = message.attachments[0]

    if att.url in processed_images:
        return
    processed_images.add(att.url)

    if user_cooldown.get(message.author.id, 0) > time.time():
        return

    user_cooldown[message.author.id] = time.time() + 10

    await message.channel.send("🔍 Checking...")

    result = analyze_image(
        att.url,
        config.get("alliance", "UNKNOWN"),
        config.get("tag", "UNKNOWN")
    )

    role = message.guild.get_role(config.get("role_id"))
    guest = message.guild.get_role(config.get("guest_role_id"))

    if result.startswith("APPROVED"):
        if role:
            await message.author.add_roles(role)
        if guest:
            await message.author.remove_roles(guest)

        msg = f"🔥 {message.author.mention} APPROVED"
    else:
        if guest:
            await message.author.add_roles(guest)
        if role:
            await message.author.remove_roles(role)

        reason = result.replace("REJECTED:", "").strip()
        msg = f"⭐ {message.author.mention} REJECTED — {reason}"

    await message.channel.send(msg)
    await send_log(message.guild, msg)