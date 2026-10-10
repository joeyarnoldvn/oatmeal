import asyncio
from datetime import datetime
import os
import threading
import time
from flask import Flask
import discord
from google import genai
from google.genai import types

# 1. Setup Flask web server in a background thread for Render port binding
app = Flask(__name__)


@app.route("/")
def home():
  return "Oatmeal AI is running!"


def run_web():
  port = int(os.environ.get("PORT", 10000))
  app.run(host="0.0.0.0", port=port, use_reloader=False)


threading.Thread(target=run_web, daemon=True).start()

# 2. Initialize Discord and Gemini API
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")

client = genai.Client(api_key=GEMINI_API_KEY)

intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)

message_queue = asyncio.Queue(maxsize=100)
channel_cooldowns = {}
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


async def worker(worker_id):
  global daily_request_count, last_reset_date
  print(f"[WORKER {worker_id}] Started successfully.")

  while True:
    message = await message_queue.get()
    try:
      if message_queue.qsize() > 20:
        message_queue.task_done()
        continue

      current_date = datetime.now().date()
      if current_date != last_reset_date:
        daily_request_count = 0
        last_reset_date = current_date

      if daily_request_count >= 950:
        message_queue.task_done()
        continue

      channel_id = message.channel.id
      if channel_id in channel_cooldowns:
        if time.time() - channel_cooldowns[channel_id] < 6:
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
          print(f"[WORKER {worker_id}] Gemini API request timed out.")
        except Exception as e:
          print(f"[WORKER {worker_id}] API Error: {e}")

      if reply_text:
        try:
          await message.reply(reply_text)
        except Exception as send_error:
          print(f"Failed to send reply: {send_error}")

    except Exception as worker_error:
      print(f"Worker {worker_id} exception: {worker_error}")
    finally:
      message_queue.task_done()


@bot.event
async def on_ready():
  print(f"[CONNECTED] Logged in successfully as {bot.user}")
  print(f"[SERVERS] Active in {len(bot.guilds)} Discord server(s).")
  for i in range(2):
    bot.loop.create_task(worker(i + 1))


@bot.event
async def on_message(message):
  if message.author == bot.user:
    return
  print(
      f"[MESSAGE RECEIVED] From {message.author} in #{message.channel.name}:"
      f" {message.content}"
  )
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


if __name__ == "__main__":
  bot.run(DISCORD_TOKEN)
