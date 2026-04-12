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
# LOGGING
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
# SETUP WIZARD
# =========================
class SetupView(View):
    def __init__(self, user_id):
        super().__init__(timeout=120)
        self.user_id = user_id

    def session(self):
        return setup_sessions.get(self.user_id)

    async def refresh(self, interaction):
        s = self.session()
        if not s:
            return

        steps = [
            "Alliance name",
            "Tag",
            "Verification channel",
            "Verified role",
            "Guest role",
            "Log channel"
        ]

        await interaction.response.edit_message(
            content=f"⚙️ Setup Wizard\n\nStep {s['step']+1}/6\n➡️ {steps[s['step']]}",
            view=self
        )

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: Button):
        s = self.session()
        if s and s["step"] > 0:
            s["step"] -= 1
        await self.refresh(interaction)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary)
    async def next(self, interaction: discord.Interaction, button: Button):
        s = self.session()
        if not s:
            return

        s["step"] += 1

        if s["step"] >= 6:
            data = s["data"]

            embed = discord.Embed(
                title="🧾 Confirm Setup",
                description=f"""
Alliance: {data.get('alliance')}
Tag: {data.get('tag')}
Channel: <#{data.get('channel_id')}>
Role: <@&{data.get('role_id')}>
Guest: <@&{data.get('guest_role_id')}>
Logs: <#{data.get('log_channel_id')}>
""",
                color=discord.Color.green()
            )

            await interaction.response.edit_message(embed=embed, view=None)
            return

        await self.refresh(interaction)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: Button):
        setup_sessions.pop(self.user_id, None)
        await interaction.response.edit_message(content="❌ Cancelled", view=None)

# =========================
# SETUP START
# =========================
@tree.command(name="setup")
async def setup(interaction: discord.Interaction):
    setup_sessions[interaction.user.id] = {
        "step": 0,
        "data": {},
        "start": time.time()
    }

    await interaction.response.send_message(
        "⚙️ Setup Wizard Started",
        view=SetupView(interaction.user.id),
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
            name="/setup • Memento Guard"
        )
    )
    print(f"Online as {bot.user}")

# =========================
# IMAGE CHECK
# =========================
@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return

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

# =========================
# START
# =========================
Thread(target=run_web).start()
bot.run(DISCORD_TOKEN)