import asyncio
from datetime import datetime
import os
import threading
from flask import Flask
import discord
from google import genai
from google.genai import types

# Simple web server to satisfy Render's port binding requirement
app = Flask(__name__)


@app.route("/")
def home():
  return "Oatmeal AI is running!"


def run_web():
  app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))


threading.Thread(target=run_web, daemon=True).start()

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")

client = genai.Client(api_key=GEMINI_API_KEY)

intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)


@bot.event
async def on_ready():
  print(f"Bot connected successfully as {bot.user}")
  print(f"Active in {len(bot.guilds)} servers.")


@bot.event
async def on_message(message):
  # Ignore messages from the bot itself
  if message.author == bot.user:
    return

  print(f"Received message from {message.author}: {message.content}")

  try:
    prompt = message.content.replace(f"<@{bot.user.id}>", "").strip()
    if not prompt:
      prompt = "Say something in your signature style."

    config = types.GenerateContentConfig(
        safety_settings=[
            types.SafetySetting(
                category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                threshold=types.HarmBlockThreshold.BLOCK_NONE,
            ),
            types.SafetySetting(
                category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                threshold=types.HarmBlockThreshold.BLOCK_NONE,
            ),
            types.SafetySetting(
                category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                threshold=types.HarmBlockThreshold.BLOCK_NONE,
            ),
            types.SafetySetting(
                category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                threshold=types.HarmBlockThreshold.BLOCK_NONE,
            ),
        ],
        temperature=1.2,
    )

    async with message.channel.typing():
      response = await asyncio.to_thread(
          client.models.generate_content,
          model="gemini-3.8-flash",
          contents=(
              "You are Oatmeal AI, embodying Oatmeal Joey Arnold"
              " (@JOEYARNOLDVN). Act entirely like a real person hanging out"
              " in chat. VARIETY RULE: 80% of the time, give short, punchy,"
              " casual 1-2 sentence quips or reactions. 20% of the time, mix in"
              " deeper thoughts, creative associations, or retro pop-culture"
              " nods (Star Trek, 90s, Vietnam teaching days). NEVER repeat"
              " canned phrases or sound like a repetitive record player—always"
              " phrase things freshly."
              f"Incoming Message: {prompt}"
          ),
          config=config,
      )

      if (
          response
          and response.candidates
          and response.candidates[0].content.parts[0].text
      ):
        reply_text = response.candidates[0].content.parts[0].text
        await message.reply(reply_text)

  except Exception as e:
    print(f"Error handling message: {e}")


bot.run(DISCORD_TOKEN)
