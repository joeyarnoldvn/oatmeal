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

# Queue and Safeguards
message_queue = asyncio.Queue()
channel_cooldowns = {}
global_error_lockout_until = 0
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


async def process_queue():
  """Background worker that processes messages one by one, respecting cooldowns."""
  global daily_request_count, last_reset_date, global_error_lockout_until
  print("Queue worker started successfully.")

  while True:
    message = await message_queue.get()
    try:
      print(f"Picked up message from queue: '{message.content}'")
      current_date = datetime.now().date()
      if current_date != last_reset_date:
        daily_request_count = 0
        last_reset_date = current_date

      if daily_request_count >= 1000:
        print("Daily request limit reached.")
        message_queue.task_done()
        continue

      now = time.time()
      if now < global_error_lockout_until:
        wait_time = global_error_lockout_until - now
        print(f"Error lockout active. Waiting {int(wait_time)}s...")
        await asyncio.sleep(wait_time)

      channel_id = message.channel.id
      if channel_id in channel_cooldowns:
        elapsed = time.time() - channel_cooldowns[channel_id]
        if elapsed < 15:
          wait_cd = 15 - elapsed
          print(f"Channel cooldown active. Waiting {int(wait_cd)}s...")
          await asyncio.sleep(wait_cd)

      prompt = message.content.replace(f"<@{bot.user.id}>", "").strip()
      if not prompt:
        prompt = "Say something in your signature style."

      knowledge_context = load_knowledge()
      print(f"Calling Gemini API for prompt: {prompt}")

      reply_text = None
      async with message.channel.typing():
        try:
          response = await client.aio.models.generate_content(
              model="gemini-3.8-flash",
              contents=(
                  "You are Oatmeal AI, embodying Oatmeal Joey Arnold"
                  " (@JOEYARNOLDVN). Respond to the incoming message with high"
                  " variety—mix short sentences, long thoughts, creative"
                  " associations, retro pop-culture nods (Star Trek, 90s,"
                  " Vietnam teaching days), or philosophical twists. Relate"
                  " the subject back to your worldview and background using"
                  f" the following facts:\n\n{knowledge_context}\n\nIncoming"
                  f" Message: {prompt}"
              ),
          )
          if response and response.text:
            reply_text = response.text
            channel_cooldowns[channel_id] = time.time()
            daily_request_count += 1
            print("Gemini response generated successfully.")
          else:
            print("Gemini returned an empty response.")
        except Exception as api_err:
          import traceback

          print(f"API Error caught during generate_content: {str(api_err)}")
          traceback.print_exc()
          global_error_lockout_until = time.time() + 60

      if reply_text:
        try:
          await message.reply(reply_text)
          print("Reply successfully sent to Discord.")
        except Exception as send_error:
          print(f"Failed to send reply to Discord: {send_error}")

    except Exception as worker_error:
      import traceback

      print(f"Queue worker inner exception: {worker_error}")
      traceback.print_exc()
    finally:
      message_queue.task_done()


@bot.event
async def on_ready():
  print(f"Oatmeal AI is live in the cloud as {bot.user}")
  bot.loop.create_task(process_queue())


@bot.event
async def on_message(message):
  if message.author == bot.user:
    return
  print(f"Message received from {message.author}: {message.content}")
  await message_queue.put(message)


bot.run(DISCORD_TOKEN)
