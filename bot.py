import asyncio
from datetime import datetime
import os
import random
import threading
import time
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

# Queue, Quota Safeguards, and Activity Tracking
message_queue = asyncio.Queue(maxsize=100)
channel_cooldowns = {}  # Prevents quota drain from rapid spam in one channel
daily_request_count = 0
last_reset_date = datetime.now().date()
last_bot_activity_time = time.time()  # Tracks when the bot last spoke


def load_knowledge():
  try:
    if os.path.exists("knowledge.txt"):
      with open("knowledge.txt", "r", encoding="utf-8") as f:
        return f.read()
  except Exception as e:
    print(f"Error reading knowledge file: {e}")
  return "No background info available."


async def worker(worker_id):
  """Concurrent background worker that balances responsiveness, variety, and API conservation."""
  global daily_request_count, last_reset_date, last_bot_activity_time
  print(f"Worker {worker_id} started.")

  while True:
    message = await message_queue.get()
    try:
      # Drop stale queue build-ups under heavy load
      if message_queue.qsize() > 20:
        message_queue.task_done()
        continue

      current_date = datetime.now().date()
      if current_date != last_reset_date:
        daily_request_count = 0
        last_reset_date = current_date

      # Hard stop at 950 requests to safely stay under the 1,000/day free limit
      if daily_request_count >= 950:
        message_queue.task_done()
        continue

      # Enforce a short 6-second cooldown per channel to conserve quota against spam
      channel_id = message.channel.id
      if channel_id in channel_cooldowns:
        elapsed = time.time() - channel_cooldowns[channel_id]
        if elapsed < 6:
          message_queue.task_done()
          continue

      prompt = message.content.replace(f"<@{bot.user.id}>", "").strip()
      if not prompt:
        prompt = "Say something in your signature style."

      knowledge_context = load_knowledge()

      # High temperature and strict formatting rules to eliminate repetition
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
          temperature=1.2,  # Boosts randomness and variety
      )

      reply_text = None
      async with message.channel.typing():
        try:
          response = await asyncio.to_thread(
              client.models.generate_content,
              model="gemini-3.8-flash",
              contents=(
                  "You are Oatmeal AI, embodying Oatmeal Joey Arnold"
                  " (@JOEYARNOLDVN). Act entirely like a real person hanging"
                  " out in chat. VARIETY RULE: 80% of the time, give short,"
                  " punchy, casual 1-2 sentence quips or reactions. 20% of the"
                  " time, mix in deeper thoughts, creative associations, or"
                  " retro pop-culture nods (Star Trek, 90s, Vietnam teaching"
                  " days). NEVER repeat canned phrases or sound like a"
                  " repetitive record player—always phrase things freshly."
                  f" Context facts:\n\n{knowledge_context}\n\nIncoming"
                  f" Message: {prompt}"
              ),
              config=config,
          )

          if response and response.candidates:
            candidate = response.candidates[0]
            if (
                candidate.content
                and candidate.content.parts
                and candidate.content.parts[0].text
            ):
              reply_text = candidate.content.parts[0].text
              daily_request_count += 1
              channel_cooldowns[channel_id] = time.time()
        except Exception as e:
          print(f"API Error in worker {worker_id}: {str(e)}")

      if reply_text:
        try:
          await message.reply(reply_text)
          last_bot_activity_time = time.time()  # Update last active timestamp
        except Exception as send_error:
          print(f"Failed to send reply: {send_error}")

    except Exception as worker_error:
      print(f"Worker {worker_id} exception: {worker_error}")
    finally:
      message_queue.task_done()


async def idle_search_loop():
  """If the bot hasn't spoken in 10 minutes (600 seconds), search history backwards to revive chat."""
  global last_bot_activity_time
  await bot.wait_until_ready()
  await asyncio.sleep(30)  # Initial warm-up delay on bot startup

  while True:
    await asyncio.sleep(60)  # Check every 1 minute
    try:
      # 600 seconds = 10 minutes
      if time.time() - last_bot_activity_time > 60
        print(
            "Bot idle for over 10 minutes. Searching history for a message to"
            " revive..."
        )

        for guild in bot.guilds:
          for channel in guild.text_channels:
            try:
              perms = channel.permissions_for(guild.me)
              if perms.read_message_history and perms.read_messages:
                # Search back through history in reverse chronological order (up to 50 messages)
                async for message in channel.history(limit=50):
                  if message.author == bot.user:
                    continue

                  # Push the historical message into the queue to reply and revive chat
                  await message_queue.put(message)
                  print(
                      f"Idle revive selected message from {message.author}:"
                      f" {message.content}"
                  )
                  last_bot_activity_time = (
                      time.time()
                  )  # Reset timer to prevent rapid triggers
                  break
            except Exception as e:
              print(f"Error scanning history in #{channel.name}: {e}")
            break  # Process one channel per idle cycle
    except Exception as loop_err:
      print(f"Idle search loop error: {loop_err}")


@bot.event
async def on_ready():
  print(f"Oatmeal AI logged in successfully as {bot.user}")

  # Spawn 2 concurrent workers for balanced throughput
  for i in range(2):
    bot.loop.create_task(worker(i + 1))

  # Start the background idle revival task
  bot.loop.create_task(idle_search_loop())


@bot.event
async def on_message(message):
  if message.author == bot.user:
    return
  try:
    if message_queue.full():
      try:
        message_queue.get_nowait()
        message_queue.task_done()
      except asyncio.QueueEmpty:
        pass
    await message_queue.put(message)
  except Exception as e:
    print(f"Queue insertion error: {e}")


bot.run(DISCORD_TOKEN)
