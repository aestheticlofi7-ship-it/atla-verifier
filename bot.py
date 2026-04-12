import discord
import os
import time
import sqlite3
from dotenv import load_dotenv
from openai import OpenAI
from flask import Flask
from threading import Thread
from discord import app_commands
from discord import Embed, Color
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
processed_images = set()
user_cooldown = {}
setup_sessions = {}

# =========================
# SAFE DELETE
# =========================
async def safe_delete(message):
    try:
        if message.guild.me.guild_permissions.manage_messages:
            await message.delete()
    except:
        pass

# =========================
# LOGS
# =========================
async def send_log(guild, text):
    cursor.execute("SELECT log_channel_id FROM guilds WHERE guild_id = ?", (guild.id,))
    row = cursor.fetchone()
    if not row:
        return

    channel = guild.get_channel(row[0])
    if channel:
        await channel.send(text)

# =========================
# AI
# =========================
def analyze_image(url, guild_id):
    try:
        cursor.execute("""
        SELECT alliance, tag FROM alliances WHERE guild_id = ?
        """, (guild_id,))
        alliances = cursor.fetchall()

        alliance_list = "\n".join([f"- {a[0]} | {a[1]}" for a in alliances])

        res = client_ai.responses.create(
            model="gpt-4o-mini",
            input=[{
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": f"""
Return ONLY JSON.

Valid alliances:
{alliance_list}

FORMAT:
{{
  "status": "APPROVED or REJECTED",
  "matches": [{{"alliance": "name"}}],
  "reason": "string"
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

    except:
        return '{"status":"REJECTED","matches":[],"reason":"AI error"}'

# =========================
# SAFE JSON PARSE
# =========================
def safe_parse_json(text):
    try:
        text = text.strip()
        text = text.replace("```json", "").replace("```", "")

        start = text.find("{")
        end = text.rfind("}")

        if start == -1 or end == -1:
            return None

        return json.loads(text[start:end+1])

    except:
        return None

# =========================
# SETUP COMMAND
# =========================
@tree.command(name="setup")
async def setup(interaction: discord.Interaction):
    setup_sessions[interaction.user.id] = {
        "step": 0,
        "alliances": [],
        "current": {},
        "config": {},
        "confirming": False
    }

    await interaction.response.send_message(
        "⚙️ Setup started.\nStep 1: Type alliance name",
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

    print(f"Online as {bot.user}")

# =========================
# MESSAGE HANDLER
# =========================
@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return

    session = setup_sessions.get(message.author.id)

    # =========================
    # SETUP FLOW
    # =========================
    if session:
        content = message.content.strip()
        step = session["step"]

        # =========================
        # CONFIRM FIX (IMPORTANT)
        # =========================
        if session.get("confirming"):

            if content.lower() == "yes":

                for a in session["alliances"]:
                    cursor.execute("""
                    INSERT INTO alliances (guild_id, alliance, tag, role_id)
                    VALUES (?, ?, ?, ?)
                    """, (message.guild.id, a["alliance"], a["tag"], a["role_id"]))

                cursor.execute("""
                INSERT OR REPLACE INTO guilds (
                    guild_id, channel_id, guest_role_id, log_channel_id
                ) VALUES (?, ?, ?, ?)
                """, (
                    message.guild.id,
                    session["config"]["channel_id"],
                    session["config"]["guest_role_id"],
                    session["config"]["log_channel_id"]
                ))

                conn.commit()

                # 🔥 FIX: stop session completely
                setup_sessions.pop(message.author.id, None)

                await message.channel.send("✅ Setup completed successfully!")
                return

            elif content.lower() == "no":
                setup_sessions.pop(message.author.id, None)
                await message.channel.send("❌ Setup cancelled.")
                return

            return

        # STEP 0
        if step == 0:
            session["current"]["alliance"] = content
            session["step"] = 1
            await message.channel.send("🏷️ Step 2: Send TAG")
            return

        # STEP 1
        if step == 1:
            session["current"]["tag"] = content
            session["step"] = 2
            await message.channel.send("⭐ Step 3: Mention VERIFIED role")
            return

        # STEP 2
        if step == 2:
            if not message.role_mentions:
                await message.channel.send("❌ Mention a role")
                return

            session["current"]["role_id"] = message.role_mentions[0].id
            session["alliances"].append(session["current"])
            session["current"] = {}

            session["step"] = 3
            await message.channel.send("➕ Add another? (yes/no)")
            return

        # STEP 3
        if step == 3:
            if content.lower() == "yes":
                session["step"] = 0
                await message.channel.send("⚙️ Step 1: Type alliance name")
            else:
                session["step"] = 4
                await message.channel.send("📌 Mention verification channel")
            return

        # STEP 4
        if step == 4:
            if not message.channel_mentions:
                await message.channel.send("❌ Mention channel")
                return

            session["config"]["channel_id"] = message.channel_mentions[0].id
            session["step"] = 5
            await message.channel.send("👤 Mention guest role")
            return

        # STEP 5
        if step == 5:
            if not message.role_mentions:
                await message.channel.send("❌ Mention role")
                return

            session["config"]["guest_role_id"] = message.role_mentions[0].id
            session["step"] = 6
            await message.channel.send("📁 Mention log channel")
            return

        # STEP 6
        if step == 6:
            if not message.channel_mentions:
                await message.channel.send("❌ Mention channel")
                return

            session["config"]["log_channel_id"] = message.channel_mentions[0].id

            overview = "🧾 Setup Overview:\n\n"

            for i, a in enumerate(session["alliances"], start=1):
                overview += f"{i}. {a['alliance']} → {a['tag']} → <@&{a['role_id']}>\n"

            overview += f"\nVerification: <#{session['config']['channel_id']}>\n"
            overview += f"Guest: <@&{session['config']['guest_role_id']}>\n"
            overview += f"Logs: <#{session['config']['log_channel_id']}>\n\n"
            overview += "Confirm? (yes/no)"

            session["confirming"] = True
            await message.channel.send(overview)
            return

    # =========================
    # VERIFICATION SYSTEM
    # =========================
    cursor.execute("SELECT channel_id, guest_role_id FROM guilds WHERE guild_id = ?", (message.guild.id,))
    cfg = cursor.fetchone()
    if not cfg:
        return

    channel_id, guest_role_id = cfg

    if message.channel.id != channel_id:
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

    result = analyze_image(att.url, message.guild.id)
    data = safe_parse_json(result)

    if not data:
        data = {"status": "REJECTED", "reason": "parse error", "matches": []}

    guest = message.guild.get_role(guest_role_id)

    if data["status"] == "APPROVED":

        for match in data.get("matches", []):
            cursor.execute("""
            SELECT role_id FROM alliances WHERE guild_id = ? AND alliance = ?
            """, (message.guild.id, match.get("alliance")))

            row = cursor.fetchone()

            if row:
                role = message.guild.get_role(row[0])
                if role:
                    await message.author.add_roles(role)

        if guest:
            await message.author.add_roles(guest)

        await message.channel.send(
            embed=Embed(title="✅ Verified", description="Approved", color=Color.green())
        )

    else:
        if guest:
            await message.author.add_roles(guest)

        await message.channel.send(
            embed=Embed(title="❌ Rejected", description=data.get("reason"), color=Color.red())
        )

# =========================
# START
# =========================
Thread(target=run_web).start()
bot.run(DISCORD_TOKEN)