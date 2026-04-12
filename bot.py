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
# SAFE DELETE FIX
# =========================
async def safe_delete(message):
    try:
        if message.guild.me.guild_permissions.manage_messages:
            await message.delete()
    except:
        pass

# =========================
# 🔥 ONLY CHANGE: LOG SYSTEM
# =========================
async def send_log(guild, status, user, user_id, role_name=None, reason=None):
    config = load_guild(guild.id)
    if not config:
        return

    channel = guild.get_channel(config.get("log_channel_id"))
    if not channel:
        return

    now = time.strftime("%Y-%m-%d %H:%M:%S")

    # 🟢 APPROVED
    if status == "APPROVED":
        embed = Embed(
            title="🟢 VERIFIED MEMBER",
            color=Color.green()
        )

        embed.add_field(name="User", value=f"{user} ({user_id})", inline=False)
        embed.add_field(name="Role Given", value=role_name or "None", inline=False)
        embed.add_field(name="Status", value="Approved", inline=False)
        embed.add_field(name="Time", value=now, inline=False)
        embed.set_footer(text="Alliance Sentinel")

    # 🔴 REJECTED
    else:
        embed = Embed(
            title="🔴 REJECTED MEMBER",
            color=Color.red()
        )

        embed.add_field(name="User", value=f"{user} ({user_id})", inline=False)
        embed.add_field(name="Reason", value=reason or "Invalid AI response", inline=False)
        embed.add_field(name="Action", value="Guest role assigned", inline=False)
        embed.add_field(name="Time", value=now, inline=False)
        embed.set_footer(text="Alliance Sentinel")

    await channel.send(embed=embed)

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
# SETUP WIZARD
# =========================
@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return

    session = setup_sessions.get(message.author.id)

    if session:
        step = session["step"]
        data = session["data"]
        content = message.content.strip()

        if step == 0:
            data["alliance"] = content
            await message.channel.send("🏷️ Step 2: Send TAG")

        elif step == 1:
            data["tag"] = content
            await message.channel.send("📸 Step 3: Mention verification channel")

        elif step == 2 and message.channel_mentions:
            data["channel_id"] = message.channel_mentions[0].id
            await message.channel.send("🟢 Step 4: Mention VERIFIED role")

        elif step == 3 and message.role_mentions:
            data["role_id"] = message.role_mentions[0].id
            await message.channel.send("⭐ Step 5: Mention GUEST role")

        elif step == 4 and message.role_mentions:
            data["guest_role_id"] = message.role_mentions[0].id
            await message.channel.send("📁 Step 6: Mention LOG channel")

        elif step == 5 and message.channel_mentions:
            data["log_channel_id"] = message.channel_mentions[0].id

            save_guild(message.guild.id, data)
            setup_sessions.pop(message.author.id, None)

            await message.channel.send("✅ Setup completed successfully!")
            return

        session["data"] = data
        session["step"] += 1

        await safe_delete(message)
        return

    # =========================
    # IMAGE CHECK
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

    # =========================
    # RESULT HANDLING (UNCHANGED)
    # =========================
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

        embed.add_field(
            name="Role Given",
            value=role.name if role else "None",
            inline=False
        )

        embed.set_footer(text="Alliance Sentinel")

        await message.channel.send(embed=embed)

        # 🔥 NEW LOG CALL
        await send_log(
            message.guild,
            "APPROVED",
            message.author,
            message.author.id,
            role.name if role else "None"
        )

    else:
        if guest:
            await message.author.add_roles(guest)
        if role:
            await message.author.remove_roles(role)

        reason = result.replace("REJECTED:", "").strip()
        guest_name = guest.name if guest else "Guest role"

        embed = Embed(
            title="❌ Verification Rejected",
            description=f"{message.author.mention} has been rejected.",
            color=Color.red()
        )

        embed.add_field(
            name="Reason",
            value=reason if reason else "No reason provided",
            inline=False
        )

        embed.add_field(
            name="Role Given",
            value=guest_name,
            inline=False
        )

        embed.set_footer(text="Alliance Sentinel")

        await message.channel.send(embed=embed)

        # 🔥 NEW LOG CALL
        await send_log(
            message.guild,
            "REJECTED",
            message.author,
            message.author.id,
            guest.name if guest else "Guest role",
            reason
        )

# =========================
# SETUP COMMAND
# =========================
@tree.command(name="setup")
async def setup(interaction: discord.Interaction):
    setup_sessions[interaction.user.id] = {
        "step": 0,
        "data": {},
        "start": time.time()
    }

    await interaction.response.send_message(
        "⚙️ Setup started.\nStep 1: Type your Alliance name",
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
# START
# =========================
Thread(target=run_web).start()
bot.run(DISCORD_TOKEN)