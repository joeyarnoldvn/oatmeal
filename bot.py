import os
import re
import time
import asyncio
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import discord
from openai import OpenAI

# --- 1. RENDER HTTP SERVER BINDING ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Oatmeal AI is alive and running!")

def run_http_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_http_server, daemon=True).start()

# --- 2. LOAD KNOWLEDGE FILE CONTENT & TRIGGERS ---
FALLBACK_KNOWLEDGE = """
Hawaii is a wonderful topic. The bot should remember information about Hawaii, 
including its islands, culture, and history when asked.
"""

KNOWLEDGE_TEXT = ""
TRIGGER_WORDS = set()
KNOWLEDGE_FILE = "knowledge.txt"
current_dir = os.path.dirname(os.path.abspath(__file__))
file_path = os.path.join(current_dir, KNOWLEDGE_FILE)

print(f"Looking for knowledge file at: {file_path}")

if os.path.exists(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        KNOWLEDGE_TEXT = f.read()
    print(f"SUCCESS: Loaded knowledge from {KNOWLEDGE_FILE}")
else:
    print(f"WARNING: {KNOWLEDGE_FILE} not found on disk. Using embedded fallback knowledge.")
    KNOWLEDGE_TEXT = FALLBACK_KNOWLEDGE

# Extract trigger words from knowledge text
words = re.findall(r'\b\w+\b', KNOWLEDGE_TEXT.lower())
TRIGGER_WORDS = {w for w in words if len(w) > 3}
print(f"Active trigger word count: {len(TRIGGER_WORDS)}")

# --- 3. CONFIGURATION & CLIENT SETUP ---
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

if not DISCORD_TOKEN or not GROQ_API_KEY:
    raise ValueError("Missing DISCORD_TOKEN or GROQ_API_KEY in environment variables.")

ai_client = OpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=GROQ_API_KEY
)

# Active Groq model with a 14,400 requests/day free tier limit
MODEL_NAME = "llama-3.1-8b-instant"

intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)

user_cooldowns = {}
COOLDOWN_SECONDS = 10  

@bot.event
async def on_ready():
    print(f"Oatmeal AI logged in successfully as {bot.user}")

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    is_mentioned = bot.user in message.mentions
    is_dm = isinstance(message.channel, discord.DMChannel)

    message_words = set(re.findall(r'\b\w+\b', message.content.lower()))
    has_trigger_word = bool(TRIGGER_WORDS.intersection(message_words))

    if not is_mentioned and not is_dm and not has_trigger_word:
        return

    # --- 4. RATE LIMITING ---
    user_id = message.author.id
    current_time = time.time()
    
    if user_id in user_cooldowns:
        elapsed = current_time - user_cooldowns[user_id]
        if elapsed < COOLDOWN_SECONDS:
            if is_mentioned or is_dm:
                remaining = int(COOLDOWN_SECONDS - elapsed)
                await message.channel.send(
                    f"⏳ Please wait {remaining} seconds before my next response.",
                    delete_after=10
                )
            return

    user_cooldowns[user_id] = current_time

    clean_prompt = message.content.replace(f"<@!{bot.user.id}>", "").replace(f"<@{bot.user.id}>", "").strip()
    if not clean_prompt:
        clean_prompt = "Hello!"

    # --- 5. GROQ API GENERATION (Non-Blocking Executor) ---
    try:
        async with message.channel.typing():
            response = await bot.loop.run_in_executor(
                None,
                lambda: ai_client.chat.completions.create(
                    model=MODEL_NAME,
                    messages=[
                        {"role": "system", "content": f"You are Oatmeal AI. Use the following background knowledge base to help inform your answers if relevant:\n{KNOWLEDGE_TEXT}"},
                        {"role": "user", "content": clean_prompt}
                    ],
                    temperature=0.7,
                )
            )
            
            reply_text = response.choices[0].message.content

        if len(reply_text) > 2000:
            for i in range(0, len(reply_text), 2000):
                await message.channel.send(reply_text[i:i+2000])
        else:
            await message.channel.send(reply_text)

    except Exception as e:
        print(f"CRITICAL API ERROR: {type(e).__name__} - {e}")
        if is_mentioned or is_dm:
            await message.channel.send("❌ An error occurred while communicating with the AI model.")

bot.run(DISCORD_TOKEN)
