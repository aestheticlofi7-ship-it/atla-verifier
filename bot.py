import discord
import os
import time
import sqlite3
import json
from dotenv import load_dotenv
from openai import OpenAI
from flask import Flask
from threading import Thread

# =========================
# ENV
# =========================
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)

# =========================
# DISCORD SETUP
# =========================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = discord.Client(intents=intents)
tree = discord.app_commands.CommandTree(bot)

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
setup_sessions = {}
processed = set()
cooldown = {}

# =========================
# AI FUNCTION (FIXED 100%)
# =========================
def analyze(image_url, guild_id):
    cursor.execute(
        "SELECT alliance, tag FROM alliances WHERE guild_id=?",
        (guild_id,)
    )
    alliances = cursor.fetchall()

    alliance_list = "\n".join([f"- {a} | {t}" for a, t in alliances])

    try:
        response = client.responses.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            input=[{
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": f"""
You are a Discord verification AI.

VALID ALLIANCES:
{alliance_list}

RULES:
- Be VERY LENIENT
- If ANY alliance/tag is visible → APPROVE
- Only reject if image is empty or irrelevant

Return ONLY JSON:
{{
  "status": "APPROVED or REJECTED",
  "matches": [{{"alliance": "name"}}],
  "reason": "short explanation"
}}
"""
                    },
                    {
                        "type": "input_image",
                        "image_url": image_url
                    }
                ]
            })
        
        return response.output[0].content[0].text

    except Exception as e:
        return json.dumps({
            "status": "REJECTED",
            "matches": [],
            "reason": f"AI error: {str(e)}"
        })

# =========================
# LOGGING
# =========================
async def send_log(guild, status, user, user_id, roles=None, reason=None):
    cursor.execute("SELECT log_channel_id FROM guilds WHERE guild_id=?", (guild.id,))
    row = cursor.fetchone()
    if not row:
        return

    channel = guild.get_channel(row[0])
    if not channel:
        return

    if status == "APPROVED":
        embed = discord.Embed(title="🟢 VERIFIED MEMBER", color=discord.Color.green())
        embed.add_field(name="User", value=f"{user} ({user_id})", inline=False)
        embed.add_field(name="Roles", value=", ".join(roles or []), inline=False)
        embed.add_field(name="Status", value="Approved", inline=False)
    else:
        embed = discord.Embed(title="🔴 REJECTED MEMBER", color=discord.Color.red())
        embed.add_field(name="User", value=f"{user} ({user_id})", inline=False)
        embed.add_field(name="Reason", value=reason or "Unknown", inline=False)
        embed.add_field(name="Action", value="Guest role assigned", inline=False)

    embed.add_field(name="Time", value=time.strftime("%Y-%m-%d %H:%M:%S"))
    await channel.send(embed=embed)

# =========================
# /setup COMMAND
# =========================
@tree.command(name="setup", description="Setup verification system")
async def setup(interaction: discord.Interaction):
    setup_sessions[interaction.user.id] = {
        "step": 0,
        "alliances": [],
        "current": {},
        "config": {}
    }

    await interaction.response.send_message(
        "⚙️ Setup started\nEnter alliance name:",
        ephemeral=True
    )

# =========================
# READY
# =========================
@bot.event
async def on_ready():
    await tree.sync()

    await bot.change_presence(
        status=discord.Status.online,
        activity=discord.Activity(
            type=discord.ActivityType.playing,
            name="/setup • Alliance Sentinel"
        )
    )

    print("Bot is online")

# =========================
# MESSAGE HANDLER
# =========================
@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return

    # =========================
    # SETUP FLOW
    # =========================
    if message.author.id in setup_sessions:
        s = setup_sessions[message.author.id]
        c = message.content.strip()

        if s["step"] == 0:
            s["current"]["alliance"] = c
            s["step"] = 1
            await message.channel.send("Enter TAG:")
            return

        if s["step"] == 1:
            s["current"]["tag"] = c
            s["step"] = 2
            await message.channel.send("Mention VERIFIED ROLE")
            return

        if s["step"] == 2:
            if not message.role_mentions:
                await message.channel.send("Mention a role!")
                return

            s["current"]["role_id"] = message.role_mentions[0].id
            s["alliances"].append(s["current"])
            s["current"] = {}
            s["step"] = 3
            await message.channel.send("Add another? (yes/no)")
            return

        if s["step"] == 3:
            if c.lower() == "yes":
                s["step"] = 0
                await message.channel.send("New alliance name:")
                return

            await message.channel.send("Mention verification channel")
            s["step"] = 4
            return

        if s["step"] == 4:
            if not message.channel_mentions:
                return
            s["config"]["channel_id"] = message.channel_mentions[0].id
            s["step"] = 5
            await message.channel.send("Guest role?")
            return

        if s["step"] == 5:
            if not message.role_mentions:
                return
            s["config"]["guest_role_id"] = message.role_mentions[0].id
            s["step"] = 6
            await message.channel.send("Log channel?")
            return

        if s["step"] == 6:
            if not message.channel_mentions:
                return
            s["config"]["log_channel_id"] = message.channel_mentions[0].id
            s["step"] = 7
            await message.channel.send("Confirm? (yes/no)")
            return

        if s["step"] == 7:
            if c.lower() != "yes":
                setup_sessions.pop(message.author.id)
                await message.channel.send("Cancelled")
                return

            gid = message.guild.id

            cursor.execute(
                "INSERT OR REPLACE INTO guilds VALUES (?,?,?,?)",
                (
                    gid,
                    s["config"]["channel_id"],
                    s["config"]["guest_role_id"],
                    s["config"]["log_channel_id"]
                )
            )

            for a in s["alliances"]:
                cursor.execute(
                    "INSERT INTO alliances VALUES (?,?,?,?)",
                    (gid, a["alliance"], a["tag"], a["role_id"])
                )

            conn.commit()
            setup_sessions.pop(message.author.id)

            await message.channel.send("Setup complete!")
            return

    # =========================
    # VERIFY SYSTEM
    # =========================
    cursor.execute(
        "SELECT channel_id, guest_role_id FROM guilds WHERE guild_id=?",
        (message.guild.id,)
    )
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

    cooldown[message.author.id] = time.time() + 8

    await message.channel.send("Checking image...")

    raw = analyze(att.url, message.guild.id)

    try:
        data = json.loads(raw)
    except:
        data = {
            "status": "REJECTED",
            "matches": [],
            "reason": "Invalid JSON from AI"
        }

    guest_role = message.guild.get_role(guest_role_id)
    roles = []

    if data.get("status") == "APPROVED":
        for m in data.get("matches", []):
            name = m.get("alliance", "")

            cursor.execute(
                "SELECT role_id FROM alliances WHERE guild_id=? AND alliance=?",
                (message.guild.id, name)
            )

            r = cursor.fetchone()
            if r:
                role = message.guild.get_role(r[0])
                if role:
                    await message.author.add_roles(role)
                    roles.append(role.name)

        if guest_role:
            await message.author.add_roles(guest_role)
            roles.append("Guest")

        await send_log(message.guild, "APPROVED", message.author, message.author.id, roles, None)
        await message.channel.send("✅ Approved!")

    else:
        if guest_role:
            await message.author.add_roles(guest_role)

        await send_log(
            message.guild,
            "REJECTED",
            message.author,
            message.author.id,
            [],
            data.get("reason")
        )

        await message.channel.send("❌ Rejected → Guest assigned")

# =========================
# FLASK KEEP ALIVE
# =========================
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot running"

def run():
    app.run(host="0.0.0.0", port=8080)

Thread(target=run).start()

# =========================
# RUN BOT
# =========================
bot.run(DISCORD_TOKEN)