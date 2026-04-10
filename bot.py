import discord
import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = discord.Client(intents=intents)import discord
import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# 🔑 Keys (UIT .env)
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
client_ai = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# 🏷️ Alliance
ALLIANCE_NAME = "Memento Mori"

# 🧷 Settings
VERIFIED_ROLE = "💨 ARC 1081"
UNVERIFIED_ROLE = "Unverified"
VERIFICATION_CHANNEL = "📸・verification"

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = discord.Client(intents=intents)


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
                            "text": (
                                f"Check this screenshot.\n"
                                f"Alliance must be: {ALLIANCE_NAME}\n"
                                "Only APPROVED if alliance is clearly visible.\n"
                                "Otherwise REJECTED."
                            )
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
        print("🔥 AI ERROR:", repr(e))
        return "REJECTED - AI ERROR"


async def get_role(guild, name):
    for role in guild.roles:
        if role.name == name:
            return role
    return None


@bot.event
async def on_ready():
    print(f"✅ Bot is online als {bot.user}")


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

    await message.channel.send("🔍 Checking alliance membership...")

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
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

bot.run(DISCORD_TOKEN)        await message.channel.send("🔴 Not verified.")


bot.run(DISCORD_TOKEN)
