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

# ⚙️ Discord setup
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = discord.Client(intents=intents)
tree = discord.app_commands.CommandTree(bot)

# 🌐 Flask keep-alive
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is alive"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


# 🧠 CONFIG STORAGE (later database upgrade)
guild_config = {}

# ⛔ anti spam / cache
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


# 🔧 SLASH COMMANDS
@tree.command(name="set_alliance", description="Set alliance name")
async def set_alliance(interaction: discord.Interaction, name: str):
    gid = interaction.guild.id

    if gid not in guild_config:
        guild_config[gid] = {}

    guild_config[gid]["alliance"] = name

    await interaction.response.send_message(
        f"✅ Alliance ingesteld op: {name}",
        ephemeral=True
    )


@tree.command(name="set_channel", description="Set verification channel")
async def set_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    gid = interaction.guild.id

    if gid not in guild_config:
        guild_config[gid] = {}

    guild_config[gid]["channel"] = channel.name

    await interaction.response.send_message(
        f"📸 Verification channel: {channel.name}",
        ephemeral=True
    )


@tree.command(name="set_role", description="Set verified role")
async def set_role(interaction: discord.Interaction, role: discord.Role):
    gid = interaction.guild.id

    if gid not in guild_config:
        guild_config[gid] = {}

    guild_config[gid]["role"] = role.name

    await interaction.response.send_message(
        f"🟢 Verified role: {role.name}",
        ephemeral=True
    )


# 🤖 BOT EVENTS
@bot.event
async def on_ready():
    await tree.sync()
    print(f"✅ Bot online als {bot.user}")


@bot.event
async def on_message(message):
    if message.author.bot:
        return

    guild = message.guild
    if not guild:
        return

    gid = guild.id

    if gid not in guild_config:
        return

    config = guild_config[gid]

    if "channel" not in config:
        return

    if message.channel.name != config["channel"]:
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

    role_name = config.get("role")

    role = discord.utils.get(guild.roles, name=role_name)

    if "APPROVED" in result.upper():
        if role:
            await message.author.add_roles(role)
        await message.channel.send("🟢 Verified!")
    else:
        await message.channel.send("🔴 Not verified.")


# 🚀 START EVERYTHING
Thread(target=run_web).start()
bot.run(DISCORD_TOKEN)