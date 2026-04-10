import discord
import os
from dotenv import load_dotenv
from openai import OpenAI
from flask import Flask
from threading import Thread

load_dotenv()

# 🔑 Keys
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
client_ai = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# 🏷️ Settings
ALLIANCE_NAME = "Memento Mori"
VERIFIED_ROLE = "💨 ARC 1081"
UNVERIFIED_ROLE = "@everyone"
VERIFICATION_CHANNEL = "📸・verification"

# 🤖 Discord bot
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = discord.Client(intents=intents)

# 🌐 Web server (keep alive)
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is alive"

def run():
import os

app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

def keep_alive():
    t = Thread(target=run)
    t.start()

# 🤖 AI function
def analyze_image(url):
    try:
        response = client_ai.responses.create(
            model="gpt-4o-mini",
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": f"Check this screenshot. Alliance must be {ALLIANCE_NAME}. Reply ONLY APPROVED or REJECTED."
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

# 🔎 helper
async def get_role(guild, name):
    for role in guild.roles:
        if role.name == name:
            return role
    return None

# 🚀 events
@bot.event
async def on_ready():
    print(f"✅ Bot online als {bot.user}")

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if message.channel.name != VERIFICATION_CHANNEL:
        return

    if not message.attachments:
        return

    attachment = message.attachments[0]

    if "image" not in (attachment.content_type or ""):
        await message.channel.send("❌ Alleen afbeeldingen toegestaan.")
        return

    await message.channel.send("🔍 Checking...")

    result = analyze_image(attachment.url)

    guild = message.guild
    member = message.author

    verified_role = await get_role(guild, VERIFIED_ROLE)
    unverified_role = await get_role(guild, UNVERIFIED_ROLE)

    if "APPROVED" in result.upper():
        if verified_role:
            await member.add_roles(verified_role)
        if unverified_role:
            await member.remove_roles(unverified_role)

        await message.channel.send("🟢 Verified!")
    else:
        await message.channel.send("🔴 Not verified.")

# 🚀 START EVERYTHING
keep_alive()
bot.run(DISCORD_TOKEN)