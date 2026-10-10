import asyncio
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
channel_cooldowns = {}  # Tracks last successful response per channel
global_error_lockout_until = 0  # Locks the bot completely if an error happens
daily_request_count = 0
last_reset_date = datetime.now().date()

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
  global daily_request_count, last_reset_date, global_error_lockout_until

  if message.author == bot.user:
    return

  current_date = datetime.now().date()
  if current_date != last_reset_date:
    daily_request_count = 0
    last_reset_date = current_date

  if daily_request_count >= 1000:
    return

  now = time.time()

  # 1. Check if the bot is currently in an error timeout lockout period (e.g., 60 seconds)
  if now < global_error_lockout_until:
    return  # Completely ignore all messages while locked out

  # 2. Enforce 15-second channel cooldown for normal messages
  channel_id = message.channel.id
  if channel_id in channel_cooldowns and (
      now - channel_cooldowns[channel_id] < 15
  ):
    return

  prompt = message.content.replace(f"<@{bot.user.id}>", "").strip()
  if not prompt:
    prompt = "Say something in your signature style."

  knowledge_context = load_knowledge()

  print(f"Generating response for prompt: {prompt}")

  async with message.channel.typing():
    try:
      response = await asyncio.to_thread(
          client.models.generate_content,
          model="gemini-2.5-flash",
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
      if not reply_text:
        raise ValueError("Empty response text")

      # Success: Update channel cooldown normally (e.g., 15 seconds)
      channel_cooldowns[channel_id] = time.time()
      daily_request_count += 1

    except Exception as e:
      print(f"API Error encountered: {str(e)}")
      # ERROR TIMEOUT: Lock out the bot completely for 60 seconds after any error
      global_error_lockout_until = time.time() + 60
      reply_text = random.choice(ERROR_FALLBACKS)

  try:
    await message.reply(reply_text)
    print("Reply successfully sent to Discord.")
  except Exception as send_error:
    print(f"Failed to send reply to Discord: {send_error}")


bot.run(DISCORD_TOKEN)
