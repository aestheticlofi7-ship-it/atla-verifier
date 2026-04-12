import discord
import os
import time
import sqlite3
from dotenv import load_dotenv
from openai import OpenAI
from flask import Flask
from threading import Thread
from discord import app_commands

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
# 🗄️ DATABASE
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
    INSERT OR REPLACE INTO guilds
    (guild_id, alliance, tag, channel_id, role_id, guest_role_id, log_channel_id)
    VALUES (?, ?, ?, ?, ?, ?, ?)
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
# 🧠 SAFETY
# =========================
processed_images = set()
user_cooldown = {}

# =========================
# 🤖 AI CHECK
# =========================
def analyze_image(url, alliance_name, tag):
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
Check this screenshot carefully.

STRICT REQUIREMENTS:
- Alliance must EXACTLY match: "{alliance_name}"
- Player tag must EXACTLY contain: "{tag}"

Reply ONLY:

APPROVED

or

REJECTED: <short reason>
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
        return "REJECTED: AI error"

# =========================
# 📁 LOGS
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
# ⚙️ SETUP COMMAND GROUP
# =========================
setup_group = app_commands.Group(name="setup", description="Configure the bot")
tree.add_command(setup_group)

def admin_only():
    return app_commands.checks.has_permissions(administrator=True)

@setup_group.command(name="alliance")
@admin_only()
async def setup_alliance(interaction: discord.Interaction, name: str):
    data = load_guild(interaction.guild.id) or {}
    data["alliance"] = name
    save_guild(interaction.guild.id, data)
    await interaction.response.send_message(f"✅ Alliance: {name}", ephemeral=True)

@setup_group.command(name="tag")
@admin_only()
async def setup_tag(interaction: discord.Interaction, tag: str):
    data = load_guild(interaction.guild.id) or {}
    data["tag"] = tag
    save_guild(interaction.guild.id, data)
    await interaction.response.send_message(f"🏷️ Tag: {tag}", ephemeral=True)

@setup_group.command(name="channel")
@admin_only()
async def setup_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    data = load_guild(interaction.guild.id) or {}
    data["channel_id"] = channel.id
    save_guild(interaction.guild.id, data)
    await interaction.response.send_message(f"📸 Channel: {channel.mention}", ephemeral=True)

@setup_group.command(name="role")
@admin_only()
async def setup_role(interaction: discord.Interaction, role: discord.Role):
    data = load_guild(interaction.guild.id) or {}
    data["role_id"] = role.id
    save_guild(interaction.guild.id, data)
    await interaction.response.send_message(f"🟢 Role: {role.mention}", ephemeral=True)

@setup_group.command(name="guest")
@admin_only()
async def setup_guest(interaction: discord.Interaction, role: discord.Role):
    data = load_guild(interaction.guild.id) or {}
    data["guest_role_id"] = role.id
    save_guild(interaction.guild.id, data)
    await interaction.response.send_message(f"⭐ Guest: {role.mention}", ephemeral=True)

@setup_group.command(name="logs")
@admin_only()
async def setup_logs(interaction: discord.Interaction, channel: discord.TextChannel):
    data = load_guild(interaction.guild.id) or {}
    data["log_channel_id"] = channel.id
    save_guild(interaction.guild.id, data)
    await interaction.response.send_message(f"📁 Logs: {channel.mention}", ephemeral=True)

# =========================
# 🤖 READY
# =========================
@bot.event
async def on_ready():
    await tree.sync()
    print(f"✅ Bot online as {bot.user}")

# =========================
# 🤖 IMAGE VERIFICATION
# =========================
@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return

    config = load_guild(message.guild.id)
    if not config:
        return

    if message.channel.id != config.get("channel_id"):
        return

    if not message.attachments:
        return

    attachment = message.attachments[0]

    if "image" not in (attachment.content_type or ""):
        return

    if attachment.url in processed_images:
        return
    processed_images.add(attachment.url)

    if user_cooldown.get(message.author.id, 0) > time.time():
        await message.channel.send(f"⏳ {message.author.mention} cooldown active.")
        return

    user_cooldown[message.author.id] = time.time() + 10

    await message.channel.send("🔍 Checking...")

    result = analyze_image(
        attachment.url,
        config.get("alliance", "UNKNOWN"),
        config.get("tag", "UNKNOWN")
    )

    role = message.guild.get_role(config.get("role_id"))
    guest = message.guild.get_role(config.get("guest_role_id"))

    # =========================
    # 🟢 APPROVED
    # =========================
    if result.upper().startswith("APPROVED"):
        if role:
            await message.author.add_roles(role)
        if guest:
            await message.author.remove_roles(guest)

        msg = f"🔥 {message.author.mention} APPROVED — Welcome Member!"
        await message.channel.send(msg)
        await send_log(message.guild, msg)

    # =========================
    # ⭐ REJECTED
    # =========================
    else:
        if guest:
            await message.author.add_roles(guest)
        if role:
            await message.author.remove_roles(role)

        if "REJECTED:" in result:
            reason = result.split("REJECTED:", 1)[1].strip()
        else:
            reason = ""

        if not reason:
            reason = "Requirements not met (alliance/tag check failed)"

        msg = (
            f"⭐ {message.author.mention} REJECTED\n"
            f"Reason: {reason}\n"
            f"Action: Guest role assigned"
        )

        await message.channel.send(msg)
        await send_log(message.guild, msg)

# =========================
# 🚀 START
# =========================
Thread(target=run_web).start()
bot.run(DISCORD_TOKEN)