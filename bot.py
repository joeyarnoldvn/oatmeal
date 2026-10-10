from datetime import datetime, timedelta
import os
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

# Configuration for Keywords and Safeguards
KEYWORDS = ["oatmeal joey", "joeyarnoldvn", "oatmeal english"]
cooldowns = {}  # Tracks last trigger timestamps
daily_request_count = 0
last_reset_date = datetime.now().date()


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

  if message.author == bot.user:
    return

  # Reset daily counter if a new calendar day has started
  current_date = datetime.now().date()
  if current_date != last_reset_date:
    daily_request_count = 0
    last_reset_date = current_date

  # Check triggers: Direct mention, command prefix, or distinct keywords
  content_lower = message.content.lower()
  has_keyword = any(kw in content_lower for kw in KEYWORDS)
  is_mentioned = bot.user.mentioned_in(message) or message.content.startswith(
      "!oatmeal"
  )

  if is_mentioned or has_keyword:
    # Enforce 1,000 Daily Requests Limit
    if daily_request_count >= 1000:
      return  # Silently skip if daily cap is reached

    # Enforce 10-Second Cooldown per channel
    channel_id = message.channel.id
    now = time.time()
    if channel_id in cooldowns and (now - cooldowns[channel_id] < 10):
      return  # Skip if triggered less than 10 seconds ago

    cooldowns[channel_id] = now
    daily_request_count += 1

    # Strip out the mention tag if present to clean up the prompt
    prompt = message.content.replace(f"<@{bot.user.id}>", "").strip()
    if not prompt:
      prompt = (
          "Introduce yourself as Oatmeal AI, representing Oatmeal Joey Arnold."
      )

    knowledge_context = load_knowledge()

    async with message.channel.typing():
      try:
        response = client.models.generate_content(
            model="gemini-3.8-flash",
            contents=(
                "You are Oatmeal AI, representing Oatmeal Joey Arnold"
                " (@JOEYARNOLDVN). Use the following background knowledge facts"
                f" to answer accurately and avoid hallucinating:\n\n{knowledge_context}\n\nUser Message: {prompt}"
            ),
        )
        reply_text = response.text
      except Exception as e:
        reply_text = f"Cloud AI error: {str(e)}"

    await message.reply(reply_text)


bot.run(DISCORD_TOKEN)
