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

# NEW: multi alliance table
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
    config = load_guild(guild.id)
    if not config:
        return

    channel = guild.get_channel(config.get("log_channel_id"))
    if channel:
        await channel.send(text)

# =========================
# AI CHECK
# =========================
def analyze_image(url, alliance, tag):
    try:
        res = client_ai.responses.create(
            model="gpt-4o-mini",
            input=[{
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": f"""
Check screenshot.

Alliance must match: {alliance}
Tag must match: {tag}

Return ONLY:
APPROVED
or
REJECTED: <reason>
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
        return "REJECTED: AI error"

# =========================
# SETUP COMMAND (MULTI-ALLIANCE)
# =========================
@tree.command(name="setup")
async def setup(interaction: discord.Interaction):
    setup_sessions[interaction.user.id] = {
        "step": 0,
        "alliances": [],
        "current": {},
        "confirming": False,
        "start": time.time()
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
    # SETUP WIZARD
    # =========================
    if session:
        content = message.content.strip()

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

                await message.channel.send("✅ Setup completed & saved!")
                await safe_delete(message)
                return

            elif content.lower() == "no":
                session["confirming"] = False
                await message.channel.send("❌ Setup cancelled. Continue setup.")
                await safe_delete(message)
                return

            return

        step = session["step"]

        if step == 0:
            session["current"]["alliance"] = content
            await message.channel.send("🏷️ Step 2: Send TAG")

        elif step == 1:
            session["current"]["tag"] = content
            await message.channel.send("⭐ Step 3: Mention VERIFIED role")

        elif step == 2 and message.role_mentions:
            session["current"]["role_id"] = message.role_mentions[0].id

            session["alliances"].append(session["current"])
            session["current"] = {}

            session["step"] = 3

            await message.channel.send("➕ Alliance added! Add another? (yes/no)")
            await safe_delete(message)
            return

        elif step == 3:
            if content.lower() == "yes":
                session["step"] = 0
                await message.channel.send("⚙️ Step 1: Type alliance name")
            else:

                overview = "🧾 **You added:**\n\n"

                for i, a in enumerate(session["alliances"], start=1):
                    overview += f"{i}. {a['alliance']} → {a['tag']} → <@&{a['role_id']}>\n"

                overview += "\nDo you want to confirm? (yes/no)"

                session["confirming"] = True
                await message.channel.send(overview)

        await safe_delete(message)
        return

    # =========================
    # IMAGE CHECK (UNCHANGED)
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

        embed = Embed(
            title="✅ Verification Approved",
            description=f"{message.author.mention} has been approved!",
            color=Color.green()
        )

        await message.channel.send(embed=embed)

    else:
        if guest:
            await message.author.add_roles(guest)
        if role:
            await message.author.remove_roles(role)

        reason = result.replace("REJECTED:", "").strip()

        embed = Embed(
            title="❌ Verification Rejected",
            description=f"{message.author.mention} has been rejected.",
            color=Color.red()
        )

        embed.add_field(name="Reason", value=reason or "No reason")

        await message.channel.send(embed=embed)

# =========================
# START
# =========================
Thread(target=run_web).start()
bot.run(DISCORD_TOKEN)