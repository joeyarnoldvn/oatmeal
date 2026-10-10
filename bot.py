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
replied_message_ids = set()  # Tracks replied messages to prevent repeats


def load_knowledge():
  try:
    if os.path.exists("knowledge.txt"):
      with open("knowledge.txt", "r", encoding="utf-8") as f:
        return f.read()
  except Exception as e:
    print(f"Error reading knowledge file: {e}")
  return "No background info available."


async def worker(worker_id):
  """Concurrent background worker with strict timeouts to prevent freezing."""
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

      reply_text = None
      async with message.channel.typing():
        try:
          response = await asyncio.wait_for(
              asyncio.to_thread(
                  client.models.generate_content,
                  model="gemini-3.8-flash",
                  contents=(
                      "You are Oatmeal AI, embodying Oatmeal Joey Arnold"
                      " (@JOEYARNOLDVN). Act entirely like a real person hanging"
                      " out in chat. VARIETY RULE: 80% of the time, give short,"
                      " punchy, casual 1-2 sentence quips or reactions. 20% of"
                      " the time, mix in deeper thoughts, creative"
                      " associations, or retro pop-culture nods (Star Trek,"
                      " 90s, Vietnam teaching days). NEVER repeat canned"
                      " phrases or sound like a repetitive record"
                      " player—always phrase things freshly."
                      f" Context facts:\n\n{knowledge_context}\n\nIncoming"
                      f" Message: {prompt}"
                  ),
                  config=config,
              ),
              timeout=15.0,
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
        except asyncio.TimeoutError:
          print(f"Worker {worker_id}: Gemini API request timed out after 15s.")
        except Exception as e:
          print(f"API Error in worker {worker_id}: {str(e)}")

      if reply_text:
        try:
          await message.reply(reply_text)
          replied_message_ids.add(message.id)
          last_bot_activity_time = time.time()
        except Exception as send_error:
          print(f"Failed to send reply: {send_error}")

    except Exception as worker_error:
      print(f"Worker {worker_id} exception: {worker_error}")
    finally:
      message_queue.task_done()


async def idle_search_loop():
  """Lightweight idle loop: picks ONE random channel per cycle to avoid rate limits."""
  global last_bot_activity_time
  await bot.wait_until_ready()
  await asyncio.sleep(15)  # Startup warm-up

  while True:
    await asyncio.sleep(30)  # Check every 30 seconds
    try:
      # Use > 600 (10 minutes) for production; keep > 30 for quick testing
      if time.time() - last_bot_activity_time > 30:
        print("[IDLE LOOP] Searching for an inactive channel to revive...")

        # Collect all valid text channels the bot has access to across all servers
        valid_channels = []
        for guild in bot.guilds:
          for channel in guild.text_channels:
            perms = channel.permissions_for(guild.me)
            if perms.read_message_history and perms.read_messages:
              valid_channels.append(channel)

        if not valid_channels:
          print("[IDLE LOOP] No valid channels found.")
          continue

        # Pick ONE random channel out of the 100-200 channels instead of scanning all of them!
        chosen_channel = random.choice(valid_channels)
        print(f"[IDLE LOOP] Randomly selected #{chosen_channel.name} to check.")

        eligible_messages = []
        try:
          async for message in chosen_channel.history(limit=50):
            if message.author == bot.user:
              continue
            if message.type != discord.MessageType.default:
              continue
            if message.id in replied_message_ids:
              continue
            if not message.content.strip():
              continue

            eligible_messages.append(message)
        except Exception as chan_err:
          print(f"[IDLE LOOP] Error reading history in #{chosen_channel.name}: {chan_err}")

        # If we found unreplied messages in this channel, pick one at random
        if eligible_messages:
          chosen_message = random.choice(eligible_messages)
          replied_message_ids.add(chosen_message.id)

          await message_queue.put(chosen_message)
          print(
              f"[IDLE SUCCESS] Reviving chat with message from {chosen_message.author}"
              f" in #{chosen_channel.name}: '{chosen_message.content}'"
          )
          last_bot_activity_time = time.time()
        else:
          print(f"[IDLE LOOP] No unreplied messages found in #{chosen_channel.name} this cycle.")

    except Exception as loop_err:
      print(f"[IDLE LOOP ERROR]: {loop_err}")

@bot.event
async def on_ready():
  print(f"Oatmeal AI logged in successfully as {bot.user}")
  for i in range(2):
    bot.loop.create_task(worker(i + 1))
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
