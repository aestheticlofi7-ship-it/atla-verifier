import discord
import os
import time
import sqlite3
from dotenv import load_dotenv
from openai import OpenAI
from flask import Flask
from threading import Thread

load_dotenv()

# =========================
# 🔑 ENV KEYS
# =========================
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client_ai = OpenAI(api_key=OPENAI_API_KEY)

# =========================
# ⚙️ DISCORD SETUP
# =========================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = discord.Client(intents=intents)
tree = discord.app_commands.CommandTree(bot)

# =========================
# 🌐 FLASK KEEP ALIVE
# =========================
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is alive"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# =========================
# 🗄️ DATABASE (REAL STORAGE)
# =========================
conn = sqlite3.connect("bot.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS guilds (
    guild_id INTEGER PRIMARY KEY,
    alliance TEXT,
    channel_id INTEGER,
    role_id INTEGER,
    guest_role_id INTEGER,
    log_channel_id INTEGER
)
""")
conn.commit()

def save_guild(gid, data):
    cursor.execute("""
    INSERT OR REPLACE INTO guilds
    (guild_id, alliance, channel_id, role_id, guest_role_id, log_channel_id)
    VALUES (?, ?, ?, ?, ?, ?)
    """, (
        gid,
        data.get("alliance"),
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
        "channel_id": row[2],
        "role_id": row[3],
        "guest_role_id": row[4],
        "log_channel_id": row[5],
    }

# =========================
# 🧠 SAFETY SYSTEMS
# =========================
processed_images = set()
user_cooldown = {}

# =========================
# 🤖 AI IMAGE CHECK
# =========================
def analyze_image(url, alliance_name):
    try:
        response = client_ai.responses.create(
            model="gpt-4o-mini",
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": f"""
Check this screenshot.

Alliance must be: {alliance_name}

Reply ONLY:
APPROVED or REJECTED
"""
                        },
                        {
                            "type": "input_image",
                            "image_url": url
                        }
                    ]
                }
            ]
        )
        return response.output_text.strip()

    except Exception as e:
        print("AI ERROR:", repr(e))
        return "REJECTED"

# =========================
# 📁 LOG SYSTEM
# =========================
async def send_log(guild, text):
    config = load_guild(guild.id)
    if not config:
        return

    log_id = config.get("log_channel_id")
    if not log_id:
        return

    channel = guild.get_channel(log_id)
    if channel:
        await channel.send(text)

# =========================
# 🔧 SLASH COMMANDS
# =========================

@tree.command(name="setup", description="Start setup wizard")
async def setup(interaction: discord.Interaction):
    await interaction.response.send_message(
        "⚙️ Setup is now manual.\nUse /set commands:\n"
        "/set_alliance\n/set_channel\n/set_role\n/set_guest_role\n/set_logs",
        ephemeral=True
    )

@tree.command(name="set_alliance")
async def set_alliance(interaction: discord.Interaction, name: str):
    gid = interaction.guild.id
    data = load_guild(gid) or {}
    data["alliance"] = name
    save_guild(gid, data)

    await interaction.response.send_message(f"✅ Alliance set: {name}", ephemeral=True)

@tree.command(name="set_channel")
async def set_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    gid = interaction.guild.id
    data = load_guild(gid) or {}
    data["channel_id"] = channel.id
    save_guild(gid, data)

    await interaction.response.send_message(f"📸 Channel set: {channel.mention}", ephemeral=True)

@tree.command(name="set_role")
async def set_role(interaction: discord.Interaction, role: discord.Role):
    gid = interaction.guild.id
    data = load_guild(gid) or {}
    data["role_id"] = role.id
    save_guild(gid, data)

    await interaction.response.send_message(f"🟢 Verified role: {role.mention}", ephemeral=True)

@tree.command(name="set_guest_role")
async def set_guest_role(interaction: discord.Interaction, role: discord.Role):
    gid = interaction.guild.id
    data = load_guild(gid) or {}
    data["guest_role_id"] = role.id
    save_guild(gid, data)

    await interaction.response.send_message(f"⭐ Guest role: {role.mention}", ephemeral=True)

@tree.command(name="set_logs")
async def set_logs(interaction: discord.Interaction, channel: discord.TextChannel):
    gid = interaction.guild.id
    data = load_guild(gid) or {}
    data["log_channel_id"] = channel.id
    save_guild(gid, data)

    await interaction.response.send_message(f"📁 Logs: {channel.mention}", ephemeral=True)

# =========================
# 🤖 READY
# =========================
@bot.event
async def on_ready():
    await tree.sync()

    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.playing,
            name="/setup to start | Memento Guard"
        )
    )

    print(f"✅ Bot online as {bot.user}")

# =========================
# 🤖 IMAGE VERIFICATION
# =========================
@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if not message.guild:
        return

    guild = message.guild
    config = load_guild(guild.id)

    if not config:
        return

    if message.channel.id != config.get("channel_id"):
        return

    if not message.attachments:
        return

    attachment = message.attachments[0]

    if "image" not in (attachment.content_type or ""):
        return

    # ⛔ anti spam
    if attachment.url in processed_images:
        return
    processed_images.add(attachment.url)

    # ⛔ cooldown
    if user_cooldown.get(message.author.id, 0) > time.time():
        await message.channel.send(f"⏳ {message.author.mention} cooldown active.")
        return

    user_cooldown[message.author.id] = time.time() + 10

    await message.channel.send("🔍 Checking...")

    result = analyze_image(attachment.url, config.get("alliance", "UNKNOWN"))

    role = guild.get_role(config.get("role_id"))
    guest = guild.get_role(config.get("guest_role_id"))

    # =========================
    # 🟢 APPROVED
    # =========================
    if "APPROVED" in result.upper():
        if role:
            await message.author.add_roles(role)
        if guest:
            await message.author.remove_roles(guest)

        msg = f"🔥 {message.author.mention} APPROVED — Welcome Member!"
        await message.channel.send(msg)
        await send_log(guild, msg)

    # =========================
    # ⭐ REJECTED
    # =========================
    else:
        if guest:
            await message.author.add_roles(guest)
        if role:
            await message.author.remove_roles(role)

        msg = f"⭐ {message.author.mention} REJECTED — Guest role assigned."
        await message.channel.send(msg)
        await send_log(guild, msg)

# =========================
# 🚀 START
# =========================
Thread(target=run_web).start()
bot.run(DISCORD_TOKEN)