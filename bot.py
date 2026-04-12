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
# LOGS (FIXED)
# =========================
async def send_log(guild, text):
    cursor.execute("SELECT log_channel_id FROM guilds WHERE guild_id = ?", (guild.id,))
    row = cursor.fetchone()
    if not row or not row[0]:
        return

    channel = guild.get_channel(row[0])
    if channel:
        await channel.send(text)

# =========================
# AI
# =========================
def analyze_image(url, guild_id):
    cursor.execute("""
        SELECT alliance, tag FROM alliances WHERE guild_id = ?
    """, (guild_id,))
    alliances = cursor.fetchall()

    alliance_list = "\n".join([f"- {a[0]} | {a[1]}" for a in alliances])

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

Valid alliances:
{alliance_list}

Return ONLY JSON:
{{
  "status": "APPROVED or REJECTED",
  "matches": [{{"alliance":"name"}}],
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
# SETUP
# =========================
@tree.command(name="setup")
async def setup(interaction: discord.Interaction):
    setup_sessions[interaction.user.id] = {
        "step": 0,
        "alliances": [],
        "current": {},
        "config": {}
    }

    await interaction.response.send_message(
        "⚙️ Setup started\nStep 1: alliance name",
        ephemeral=True
    )

# =========================
# READY
# =========================
@bot.event
async def on_ready():
    await tree.sync()
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
    # SETUP WIZARD (FULL FIXED)
    # =========================
    if session:
        content = message.content.strip()
        step = session["step"]

        # STEP 0 alliance
        if step == 0:
            session["current"]["alliance"] = content
            session["step"] = 1
            await message.channel.send("🏷️ Send TAG")
            await safe_delete(message)
            return

        # STEP 1 tag
        if step == 1:
            session["current"]["tag"] = content
            session["step"] = 2
            await message.channel.send("⭐ Mention VERIFIED role")
            await safe_delete(message)
            return

        # STEP 2 verified role
        if step == 2:
            if not message.role_mentions:
                await message.channel.send("❌ mention role")
                return

            session["current"]["role_id"] = message.role_mentions[0].id
            session["alliances"].append(session["current"])
            session["current"] = {}
            session["step"] = 3

            await message.channel.send("📸 verification channel? (mention)")
            await safe_delete(message)
            return

        # STEP 3 verification channel
        if step == 3:
            if not message.channel_mentions:
                await message.channel.send("❌ mention channel")
                return

            session["config"]["channel_id"] = message.channel_mentions[0].id
            session["step"] = 4
            await message.channel.send("👤 guest role?")
            await safe_delete(message)
            return

        # STEP 4 guest role
        if step == 4:
            if not message.role_mentions:
                await message.channel.send("❌ mention role")
                return

            session["config"]["guest_role_id"] = message.role_mentions[0].id
            session["step"] = 5
            await message.channel.send("🪵 log channel?")
            await safe_delete(message)
            return

        # STEP 5 log channel
        if step == 5:
            if not message.channel_mentions:
                await message.channel.send("❌ mention channel")
                return

            session["config"]["log_channel_id"] = message.channel_mentions[0].id

            # SHOW OVERVIEW
            text = "🧾 Setup Overview:\n\n"
            for i, a in enumerate(session["alliances"], 1):
                text += f"{i}. {a['alliance']} → {a['tag']} → <@&{a['role_id']}>\n"

            text += f"\nVerification: <#{session['config']['channel_id']}>\n"
            text += f"Guest Role: <@&{session['config']['guest_role_id']}>\n"
            text += f"Log Channel: <#{session['config']['log_channel_id']}>\n"
            text += "\nConfirm? (yes/no)"

            session["step"] = 6
            await message.channel.send(text)
            await safe_delete(message)
            return

        # STEP 6 confirm
        if step == 6:
            if content.lower() == "yes":

                guild_id = message.guild.id

                cursor.execute("""
                INSERT OR REPLACE INTO guilds VALUES (?,?,?,?)
                """, (
                    guild_id,
                    session["config"]["channel_id"],
                    session["config"]["guest_role_id"],
                    session["config"]["log_channel_id"]
                ))

                for a in session["alliances"]:
                    cursor.execute("""
                    INSERT INTO alliances (guild_id, alliance, tag, role_id)
                    VALUES (?,?,?,?)
                    """, (guild_id, a["alliance"], a["tag"], a["role_id"]))

                conn.commit()
                setup_sessions.pop(message.author.id, None)

                await message.channel.send("✅ Setup complete!")
                return

            else:
                setup_sessions.pop(message.author.id, None)
                await message.channel.send("❌ cancelled")
                return

    # =========================
    # VERIFY SYSTEM
    # =========================
    cursor.execute("SELECT channel_id, guest_role_id, log_channel_id FROM guilds WHERE guild_id=?", (message.guild.id,))
    cfg = cursor.fetchone()
    if not cfg:
        return

    channel_id, guest_role_id, log_channel_id = cfg

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

    raw = analyze_image(att.url, message.guild.id)

    try:
        data = json.loads(raw)
    except:
        data = {"status":"REJECTED","matches":[],"reason":"parse error"}

    guest = message.guild.get_role(guest_role_id)

    roles_given = []

    if data["status"] == "APPROVED":

        for m in data.get("matches", []):
            name = m.get("alliance")

            cursor.execute("""
            SELECT role_id FROM alliances WHERE guild_id=? AND alliance=?
            """, (message.guild.id, name))

            r = cursor.fetchone()
            if r:
                role = message.guild.get_role(r[0])
                if role:
                    await message.author.add_roles(role)
                    roles_given.append(role.name)

        if not roles_given and guest:
            await message.author.add_roles(guest)
            roles_given.append("Guest")

        embed = Embed(
            title="✅ Approved",
            description=f"{message.author.mention}",
            color=Color.green()
        )
        embed.add_field(name="Roles", value=", ".join(roles_given))

        await message.channel.send(embed=embed)

        await send_log(message.guild,
            f"✅ APPROVED\nUser: {message.author}\nRoles: {', '.join(roles_given)}"
        )

    else:

        if guest:
            await message.author.add_roles(guest)

        reason = data.get("reason", "unknown")

        embed = Embed(
            title="❌ Rejected",
            description=f"{message.author.mention}",
            color=Color.red()
        )
        embed.add_field(name="Reason", value=reason)
        embed.add_field(name="Role", value="Guest")

        await message.channel.send(embed=embed)

        await send_log(message.guild,
            f"❌ REJECTED\nUser: {message.author}\nReason: {reason}\nRole: Guest"
        )

# =========================
# START
# =========================
Thread(target=run_web).start()
bot.run(DISCORD_TOKEN)