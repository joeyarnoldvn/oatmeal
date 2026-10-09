import os
import discord
from google import genai
from google.genai import types

# Grab credentials securely from cloud environment variables
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")

client = genai.Client(api_key=GEMINI_API_KEY)

intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)


@bot.event
async def on_ready():
  print(f"Oatmeal AI is live in the cloud as {bot.user}")


@bot.event
async def on_message(message):
  if message.author == bot.user:
    return

  if bot.user.mentioned_in(message) or message.content.startswith("!oatmeal"):
    prompt = message.content.replace(f"<@{bot.user.id}>", "").strip()
    if not prompt:
      prompt = (
          "Introduce yourself as Oatmeal AI, representing Oatmeal Joey Arnold."
      )

    async with message.channel.typing():
      try:
        # Gemini calls live Google Search grounding to verify facts dynamically
        response = client.models.generate_content(
            model="gemini-3.8-flash",
            contents=(
                "You are Oatmeal AI, representing Oatmeal Joey Arnold"
                " (@JOEYARNOLDVN). Use Google Search grounding to verify"
                f" facts when asked: {prompt}"
            ),
            config=types.GenerateContentConfig(tools=[{"google_search": {}}]),
        )
        reply_text = response.text
      except Exception as e:
        reply_text = f"Cloud AI error: {str(e)}"

    await message.reply(reply_text)


bot.run(DISCORD_TOKEN)
