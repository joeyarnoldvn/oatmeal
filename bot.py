from datetime import datetime, timedelta
import os
import random
import threading
import time
from flask import Flask
import discord
from google import genai

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

# Safeguards & Tracking
cooldowns = {}  # Tracks last trigger timestamps per channel
daily_request_count = 0
last_reset_date = datetime.now().date()

# Unique random fallback messages when the API hits high demand (503 / errors)
ERROR_FALLBACKS = [
    "My brain is currently marinating in oatmeal, try back in a bit.",
    (
        "The Arnold Attic circuits are taking a quick nap, check back in an"
        " hour."
    ),
    (
        "Too much data flowing through the network right now, give me a brief"
        " breather."
    ),
    "Spike in the mainframe! I'm resting my digital eyes for a moment.",
    "Even Oatmeal Joey needs a coffee break. Catch you in a little bit!",
]


def load_knowledge():
  try:
    if os.path.exists("knowledge.txt"):
      with open("knowledge.txt", "r", encoding="utf-8") as f:
        return f.read()
  except Exception as e:
    print(f"Error reading knowledge file: {e}")
  return "No background info available."


@bot.event
async def on_ready():
  print(f"Oatmeal AI is live in the cloud as {bot.user}")


@bot.event
async def on_message(message):
  global daily_request_count, last_reset_date

  # Ignore messages sent by the bot itself
  if message.author == bot.user:
    return

  # Reset daily counter if a new calendar day has started
  current_date = datetime.now().date()
  if current_date != last_reset_date:
    daily_request_count = 0
    last_reset_date = current_date

  # Enforce 1,000 Daily Requests Limit safely below the free tier ceiling
  if daily_request_count >= 1000:
    return

  # Enforce 10-Second Cooldown per channel to keep things organic and avoid spam loops
  channel_id = message.channel.id
  now = time.time()
  if channel_id in cooldowns and (now - cooldowns[channel_id] < 10):
    return

  cooldowns[channel_id] = now
  daily_request_count += 1

  # Clean up the prompt text
  prompt = message.content.replace(f"<@{bot.user.id}>", "").strip()
  if not prompt:
    prompt = "Say something in your signature style."

  knowledge_context = load_knowledge()

  async with message.channel.typing():
    try:
      response = client.models.generate_content(
          model="gemini-3.8-flash",
          contents=(
              "You are Oatmeal AI, embodying Oatmeal Joey Arnold"
              " (@JOEYARNOLDVN). Respond to the incoming message with high"
              " variety—mix short sentences, long thoughts, creative"
              " associations, retro pop-culture nods (Star Trek, 90s, Vietnam"
              " teaching days), or philosophical twists. Relate the subject"
              " back to your worldview and background using the following"
              f" facts:\n\n{knowledge_context}\n\nIncoming"
              f" Message: {prompt}"
          ),
      )
      reply_text = response.text
    except Exception as e:
      # If any 503 or quota error happens, pick a random unique friendly message instead of a raw traceback
      reply_text = random.choice(ERROR_FALLBACKS)

  await message.reply(reply_text)


bot.run(DISCORD_TOKEN)
