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


from google.genai import types


async def process_queue():
  """Background worker that processes every message immediately and safely handles responses."""
  global daily_request_count, last_reset_date
  print("Background queue worker loop started successfully.")

  while True:
    message = await message_queue.get()
    try:
      print(f"Queue worker picked up message: {message.content}")
      current_date = datetime.now().date()
      if current_date != last_reset_date:
        daily_request_count = 0
        last_reset_date = current_date

      if daily_request_count >= 1000:
        message_queue.task_done()
        continue

      prompt = message.content.replace(f"<@{bot.user.id}>", "").strip()
      if not prompt:
        prompt = "Say something in your signature style."

      knowledge_context = load_knowledge()
      print(f"Sending prompt to Gemini: {prompt}")

      reply_text = None
      async with message.channel.typing():
        try:
          # Use loose safety settings so random words or casual chat aren't blocked
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
              temperature=0.9,
          )

          response = await asyncio.to_thread(
              client.models.generate_content,
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
              config=config,
          )

          # Safely extract text without crashing if candidates are empty or filtered
          if response and response.candidates:
            candidate = response.candidates[0]
            if (
                candidate.content
                and candidate.content.parts
                and candidate.content.parts[0].text
            ):
              reply_text = candidate.content.parts[0].text
              daily_request_count += 1
              print("Gemini successfully generated a response.")
            else:
              print(
                  f"Gemini response candidate had no text. Finish reason:"
                  f" {candidate.finish_reason}"
              )
          else:
            print("Gemini returned no candidates.")

        except Exception as e:
          import traceback

          print(f"Gemini API Error: {str(e)}")
          traceback.print_exc()

      if reply_text:
        try:
          await message.reply(reply_text)
          print("Reply sent to Discord successfully.")
        except Exception as send_error:
          print(f"Failed to send reply to Discord: {send_error}")

    except Exception as worker_error:
      print(f"Queue worker exception: {worker_error}")
    finally:
      message_queue.task_done()

@bot.event
async def on_ready():
  print(f"Oatmeal AI logged in successfully as {bot.user}")
  bot.loop.create_task(process_queue())


@bot.event
async def on_message(message):
  # Print EVERY single packet received from Discord immediately
  print(
      f"DEBUG EVENT: author={message.author}, content='{message.content}',"
      f" mention_bot={bot.user.mentioned_in(message)}"
  )

  if message.author == bot.user:
    return

  await message_queue.put(message)

bot.run(DISCORD_TOKEN)
