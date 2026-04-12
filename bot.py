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
        title="🟢 APPROVED VERIFICATION" if status == "APPROVED" else "🔴 REJECTED VERIFICATION",
        color=discord.Color.green() if status == "APPROVED" else discord.Color.red()
    )

    embed.add_field(name="User", value=f"{user} ({user_id})", inline=False)

    if status == "APPROVED":
        embed.add_field(name="Roles", value=", ".join(roles) if roles else "None", inline=False)
    else:
        embed.add_field(name="Reason", value=reason or "Could not detect valid alliance/tag", inline=False)
        embed.add_field(name="Action", value=action or "Guest role assigned", inline=False)

    embed.add_field(name="Time", value=time.strftime("%Y-%m-%d %H:%M:%S"), inline=False)

    await ch.send(embed=embed)

# =========================
# AI ANALYSIS (FIXED IMAGE RELIABILITY)
# =========================
def analyze(url, guild_id):
    cursor.execute("SELECT alliance, tag FROM alliances WHERE guild_id=?", (guild_id,))
    alliances = cursor.fetchall()

    data = "\n".join([f"- {a} | {t}" for a, t in alliances])

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

IMPORTANT RULES:
- Read image carefully
- Try OCR-like reading
- Even partial matches count
- If unsure → try to interpret instead of rejecting
- Only reject if image is completely unreadable

Return ONLY JSON:

{{
 "status":"APPROVED or REJECTED",
 "matches":[{{"alliance":"name"}}],
 "reason":"short reason"
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
        return res.output_text.strip()

    except Exception:
        return '{"status":"REJECTED","matches":[],"reason":"AI image parsing failed"}'

# =========================
# SETUP COMMAND
# =========================
@tree.command(name="setup", description="Alliance Sentinel setup")
async def setup(interaction: discord.Interaction):
    setup_sessions[interaction.user.id] = {
        "step": 0,
        "alliances": [],
        "current": {},
        "config": {}
    }

    await interaction.response.send_message(
        "⚙️ Setup started\n🏷️ Type your first alliance name.",
        ephemeral=True
    )

# =========================
# READY
# =========================
@bot.event
async def on_ready():
    await tree.sync()

    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.playing,
            name="/setup • Alliance Sentinel"
        )
    )

    print("Online")

# =========================
# SETUP WIZARD
# =========================
@bot.event
async def on_message(message):
    if message.author.bot or message.guild is None:
        return

    if message.author.id in setup_sessions:
        s = setup_sessions[message.author.id]
        c = message.content.strip()
        step = s["step"]

        if step == 0:
            s["current"]["alliance"] = c
            s["step"] = 1
            await message.channel.send("🏷️ TAG?")
            return

        if step == 1:
            s["current"]["tag"] = c
            s["step"] = 2
            await message.channel.send("⭐ VERIFIED ROLE?")
            return

        if step == 2:
            if not message.role_mentions:
                await message.channel.send("❌ mention role")
                return

            s["current"]["role_id"] = message.role_mentions[0].id
            s["alliances"].append(s["current"])
            s["current"] = {}
            s["step"] = 3
            await message.channel.send("➕ Add another alliance? (yes/no)")
            return

        if step == 3:
            if c.lower() == "yes":
                s["step"] = 0
                await message.channel.send("⚙️ NEW alliance name:")
                return

            text = "🧾 **SETUP OVERVIEW**\n\n"
            for i, a in enumerate(s["alliances"], 1):
                text += f"{i}. {a['alliance']} → {a['tag']} → <@&{a['role_id']}>\n"

            text += "\n📸 Mention verification channel:"
            s["step"] = 4
            await message.channel.send(text)
            return

        if step == 4:
            if not message.channel_mentions:
                return
            s["config"]["channel_id"] = message.channel_mentions[0].id
            s["step"] = 5
            await message.channel.send("👤 Guest role?")
            return

        if step == 5:
            if not message.role_mentions:
                return
            s["config"]["guest_role_id"] = message.role_mentions[0].id
            s["step"] = 6
            await message.channel.send("🪵 Log channel?")
            return

        if step == 6:
            if not message.channel_mentions:
                return
            s["config"]["log_channel_id"] = message.channel_mentions[0].id
            s["step"] = 7
            await message.channel.send("Confirm setup? (yes/no)")
            return

        if step == 7:
            if c.lower() != "yes":
                setup_sessions.pop(message.author.id)
                await message.channel.send("❌ cancelled")
                return

            gid = message.guild.id

            cursor.execute("INSERT OR REPLACE INTO guilds VALUES (?,?,?,?)",
                (gid,
                 s["config"]["channel_id"],
                 s["config"]["guest_role_id"],
                 s["config"]["log_channel_id"])
            )

            for a in s["alliances"]:
                cursor.execute("""
                INSERT INTO alliances (guild_id, alliance, tag, role_id)
                VALUES (?,?,?,?)
                """, (gid, a["alliance"], a["tag"], a["role_id"]))

            conn.commit()
            setup_sessions.pop(message.author.id)

            await message.channel.send("✅ Setup complete!")
            return

    # =========================
    # VERIFY SYSTEM (FINAL FIX)
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

    if cooldown.get(message.author.id, 0) > time.time():
        return

    cooldown[message.author.id] = time.time() + 10

    await message.channel.send("🔍 Checking...")

    # 🔥 FIX: try BOTH url + proxy_url
    image_url = att.proxy_url or att.url

    raw = analyze(image_url, message.guild.id)

    try:
        data = json.loads(raw)
    except:
        data = {
            "status": "REJECTED",
            "matches": [],
            "reason": "Could not read screenshot properly"
        }

    guest = message.guild.get_role(guest_role_id)
    roles = []

    if data.get("status") == "APPROVED" and data.get("matches"):

        for m in data["matches"]:
            cursor.execute("""
            SELECT role_id FROM alliances WHERE guild_id=? AND alliance=?
            """, (message.guild.id, m.get("alliance")))

            r = cursor.fetchone()
            if r:
                role = message.guild.get_role(r[0])
                if role:
                    await message.author.add_roles(role)
                    roles.append(role.name)

        if not roles and guest:
            await message.author.add_roles(guest)
            roles.append("Guest")

        await send_log(message.guild, "APPROVED", message.author, message.author.id, roles=roles)
        await message.channel.send("✅ Verification approved!")

    else:
        if guest:
            await message.author.add_roles(guest)

        await send_log(
            message.guild,
            "REJECTED",
            message.author,
            message.author.id,
            reason=data.get("reason"),
            action="Guest role assigned"
        )

        await message.channel.send("❌ Verification rejected → Guest role given")

# =========================
# WEB SERVER
# =========================
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running"

def run_web():
    app.run(host="0.0.0.0", port=8080)

Thread(target=run_web).start()
bot.run(DISCORD_TOKEN)