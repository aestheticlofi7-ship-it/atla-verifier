import discord
import os
import time
from dotenv import load_dotenv
from openai import OpenAI
from flask import Flask
from threading import Thread

load_dotenv()

# 🔑 ENV KEYS
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client_ai = OpenAI(api_key=OPENAI_API_KEY)

# ⚙️ DISCORD SETUP
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = discord.Client(intents=intents)
tree = discord.app_commands.CommandTree(bot)

# 🌐 FLASK KEEP ALIVE
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is alive"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


# 🧠 STORAGE
guild_config = {}
setup_sessions = {}

# ⛔ SAFETY SYSTEMS
processed_images = set()
user_cooldown = {}


# 🤖 AI CHECK
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
# 🔧 SLASH COMMANDS
# =========================

@tree.command(name="setup", description="Start setup wizard")
async def setup(interaction: discord.Interaction):
    gid = interaction.guild.id

    setup_sessions[gid] = {
        "step": 0,
        "data": {},
        "user": interaction.user.id,
        "channel_id": interaction.channel.id
    }

    await interaction.response.send_message(
        "⚙️ **Setup started!**\n\nReply here:\nWhat is your **Alliance name**?",
        ephemeral=True
    )


@tree.command(name="set_guest_role", description="Set guest role")
async def set_guest_role(interaction: discord.Interaction, role: discord.Role):
    gid = interaction.guild.id
    guild_config.setdefault(gid, {})["guest_role"] = role.id

    await interaction.response.send_message(
        f"⭐ Guest role set to {role.mention}",
        ephemeral=True
    )


@tree.command(name="set_role", description="Set verified role")
async def set_role(interaction: discord.Interaction, role: discord.Role):
    gid = interaction.guild.id
    guild_config.setdefault(gid, {})["role"] = role.id

    await interaction.response.send_message(
        f"🟢 Verified role set to {role.mention}",
        ephemeral=True
    )


@tree.command(name="set_channel", description="Set verification channel")
async def set_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    gid = interaction.guild.id
    guild_config.setdefault(gid, {})["channel"] = channel.id

    await interaction.response.send_message(
        f"📸 Verification channel set to {channel.mention}",
        ephemeral=True
    )


@tree.command(name="set_alliance", description="Set alliance name")
async def set_alliance(interaction: discord.Interaction, name: str):
    gid = interaction.guild.id
    guild_config.setdefault(gid, {})["alliance"] = name

    await interaction.response.send_message(
        f"✅ Alliance set to **{name}**",
        ephemeral=True
    )


# =========================
# 🤖 READY EVENT
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
# 🤖 SETUP + VERIFICATION
# =========================

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    gid = message.guild.id if message.guild else None
    if not gid:
        return

    # =========================
    # ⚙️ SETUP FLOW
    # =========================
    if gid in setup_sessions:
        session = setup_sessions[gid]

        if message.author.id != session["user"]:
            return

        if message.channel.id != session["channel_id"]:
            return

        step = session["step"]

        if step == 0:
            session["data"]["alliance"] = message.content
            session["step"] = 1
            await message.channel.send("📸 Mention verification channel:")
            return

        if step == 1:
            session["data"]["channel_id"] = message.channel_mentions[0].id if message.channel_mentions else None
            session["step"] = 2
            await message.channel.send("🟢 Mention verified role:")
            return

        if step == 2:
            session["data"]["role_id"] = message.role_mentions[0].id if message.role_mentions else None
            session["step"] = 3
            await message.channel.send("⭐ Mention guest role:")
            return

        if step == 3:
            session["data"]["guest_role_id"] = message.role_mentions[0].id if message.role_mentions else None

            guild_config[gid] = session["data"]
            del setup_sessions[gid]

            await message.channel.send("✅ Setup complete! Bot is ready.")
            return


    # =========================
    # 🤖 IMAGE VERIFICATION
    # =========================

    if gid not in guild_config:
        return

    config = guild_config[gid]

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
        return

    user_cooldown[message.author.id] = time.time() + 10

    await message.channel.send("🔍 Checking...")

    alliance_name = config.get("alliance", "UNKNOWN")
    result = analyze_image(attachment.url, alliance_name)

    guild = message.guild

    role = guild.get_role(config.get("role_id"))
    guest_role = guild.get_role(config.get("guest_role_id"))

    # 🟢 APPROVED
    if "APPROVED" in result.upper():
        if role:
            await message.author.add_roles(role)
        if guest_role:
            await message.author.remove_roles(guest_role)

        await message.channel.send(
            f"🔥 {message.author.mention} **APPROVED** — Welcome Member!"
        )

    # ⭐ REJECTED
    else:
        if guest_role:
            await message.author.add_roles(guest_role)
        if role:
            await message.author.remove_roles(role)

        await message.channel.send(
            f"⭐ {message.author.mention} **REJECTED** — Guest role assigned."
        )


# 🚀 START EVERYTHING
Thread(target=run_web).start()
bot.run(DISCORD_TOKEN)