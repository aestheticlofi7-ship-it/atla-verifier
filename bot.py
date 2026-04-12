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
    alliance TEXT,
    tag TEXT,
    channel_id INTEGER,
    role_id INTEGER,
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
# AI (MULTI-ALLIANCE)
# =========================
def analyze_image(url, guild_id):
    try:
        cursor.execute("""
        SELECT alliance, tag, role_id FROM alliances WHERE guild_id = ?
        """, (guild_id,))
        alliances = cursor.fetchall()

        alliance_list = "\n".join([
            f"- {a[0]} | {a[1]}"
            for a in alliances
        ])

        res = client_ai.responses.create(
            model="gpt-4o-mini",
            input=[{
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": f"""
Check this screenshot.

Here are valid alliances:
{alliance_list}

Return ONLY JSON:
{{
  "status": "APPROVED or REJECTED",
  "matches": [
    {{
      "alliance": "name"
    }}
  ],
  "reason": "if rejected"
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
# SETUP COMMAND
# =========================
@tree.command(name="setup")
async def setup(interaction: discord.Interaction):
    setup_sessions[interaction.user.id] = {
        "step": 0,
        "alliances": [],
        "current": {},
        "confirming": False
    }

    await interaction.response.send_message(
        "⚙️ Setup started.\n\nStep 1: Type alliance name",
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
    # SETUP WIZARD (FIXED)
    # =========================
    if session:
        content = message.content.strip()
        step = session["step"]

        # CONFIRM STEP
        if session.get("confirming"):
            if content.lower() == "yes":

                for a in session["alliances"]:
                    cursor.execute("""
                    INSERT INTO alliances (guild_id, alliance, tag, role_id)
                    VALUES (?, ?, ?, ?)
                    """, (
                        message.guild.id,
                        a["alliance"],
                        a["tag"],
                        a["role_id"]
                    ))

                conn.commit()
                setup_sessions.pop(message.author.id, None)

                await message.channel.send("✅ Setup completed!")
                await safe_delete(message)
                return

            elif content.lower() == "no":
                session["confirming"] = False
                await message.channel.send("❌ Cancelled. Continue setup.")
                await safe_delete(message)
                return

            return

        # STEP 0 → FIXED
        if step == 0:
            session["current"]["alliance"] = content
            session["step"] = 1
            await message.channel.send("🏷️ Step 2: Send TAG")
            await safe_delete(message)
            return

        # STEP 1 → FIXED (YOUR BUG WAS HERE)
        elif step == 1:
            session["current"]["tag"] = content
            session["step"] = 2
            await message.channel.send("⭐ Step 3: Mention VERIFIED role")
            await safe_delete(message)
            return

        # STEP 2 → ROLE PICK
        elif step == 2:
            if not message.role_mentions:
                await message.channel.send("❌ Mention a valid role")
                await safe_delete(message)
                return

            session["current"]["role_id"] = message.role_mentions[0].id

            session["alliances"].append(session["current"])
            session["current"] = {}
            session["step"] = 3

            await message.channel.send("➕ Add another? (yes/no)")
            await safe_delete(message)
            return

        # STEP 3 → LOOP / CONFIRM
        elif step == 3:
            if content.lower() == "yes":
                session["step"] = 0
                await message.channel.send("⚙️ Step 1: Type alliance name")
            else:
                overview = "🧾 **You added:**\n\n"

                for i, a in enumerate(session["alliances"], start=1):
                    overview += f"{i}. {a['alliance']} → {a['tag']} → <@&{a['role_id']}>\n"

                overview += "\nConfirm? (yes/no)"

                session["confirming"] = True
                await message.channel.send(overview)

        await safe_delete(message)
        return

    # =========================
    # IMAGE CHECK (MULTI AI)
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

    guest = message.guild.get_role(guest_role_id)

    try:
        data = json.loads(result)
    except:
        data = {"status": "REJECTED", "matches": [], "reason": "parse error"}

    role_given = False

    if data.get("status") == "APPROVED":

        for match in data.get("matches", []):
            name = match.get("alliance")

            cursor.execute("""
            SELECT role_id FROM alliances
            WHERE guild_id = ? AND alliance = ?
            """, (message.guild.id, name))

            row = cursor.fetchone()

            if row:
                role = message.guild.get_role(row[0])
                if role:
                    await message.author.add_roles(role)
                    role_given = True

        if not role_given and guest:
            await message.author.add_roles(guest)

        await message.channel.send(
            embed=Embed(
                title="✅ Verified",
                description=f"{message.author.mention} approved",
                color=Color.green()
            )
        )

    else:
        if guest:
            await message.author.add_roles(guest)

        await message.channel.send(
            embed=Embed(
                title="❌ Rejected",
                description=data.get("reason", "No reason"),
                color=Color.red()
            )
        )

# =========================
# START
# =========================
Thread(target=run_web).start()
bot.run(DISCORD_TOKEN)