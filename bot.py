import os
import discord
from google import genai

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
        # Standard fast generation using gemini-3.8-flash without search overhead
        response = client.models.generate_content(
            model="gemini-3.8-flash",
            contents=(
                "You are Oatmeal AI, representing Oatmeal Joey Arnold"
                " (@JOEYARNOLDVN). Answer the user's question accurately:"
                f" {prompt}"
            ),
        )
        reply_text = response.text
      except Exception as e:
        reply_text = f"Cloud AI error: {str(e)}"

    await message.reply(reply_text)


bot.run(DISCORD_TOKEN)
