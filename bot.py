import os
import threading
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


# Function to load your local knowledge text file
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
  if message.author == bot.user:
    return

  if bot.user.mentioned_in(message) or message.content.startswith("!oatmeal"):
    prompt = message.content.replace(f"<@{bot.user.id}>", "").strip()
    if not prompt:
      prompt = (
          "Introduce yourself as Oatmeal AI, representing Oatmeal Joey Arnold."
      )

    # Load your knowledge base on every query so edits update instantly
    knowledge_context = load_knowledge()

    async with message.channel.typing():
      try:
        response = client.models.generate_content(
            model="gemini-3.8-flash",
            contents=(
                "You are Oatmeal AI, representing Oatmeal Joey Arnold"
                " (@JOEYARNOLDVN). Use the following background knowledge facts"
                f" to answer accurately and avoid hallucinating:\n\n{knowledge_context}\n\nUser Question: {prompt}"
            ),
        )
        reply_text = response.text
      except Exception as e:
        reply_text = f"Cloud AI error: {str(e)}"

    await message.reply(reply_text)


bot.run(DISCORD_TOKEN)
