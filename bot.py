import os
import re
import time
import asyncio
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import discord
from google import genai

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
KNOWLEDGE_TEXT = ""
TRIGGER_WORDS = set()
KNOWLEDGE_FILE = "knowledge.txt"

if os.path.exists(KNOWLEDGE_FILE):
    with open(KNOWLEDGE_FILE, "r", encoding="utf-8") as f:
        KNOWLEDGE_TEXT = f.read()
        words = re.findall(r'\b\w+\b', KNOWLEDGE_TEXT.lower())
        TRIGGER_WORDS = set(words)
    print(f"Loaded {len(TRIGGER_WORDS)} trigger words and file content from {KNOWLEDGE_FILE}[cite: 1]")
else:
    print(f"Warning: {KNOWLEDGE_FILE} not found. Bot will only trigger on direct mentions.[cite: 1]")

# --- 3. CONFIGURATION & CLIENT SETUP ---
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

if not DISCORD_TOKEN or not GEMINI_API_KEY:
    raise ValueError("Missing DISCORD_TOKEN or GEMINI_API_KEY in environment variables.[cite: 1]")

ai_client = genai.Client(api_key=GEMINI_API_KEY)
MODEL_NAME = "gemini-3.8-flash"

intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)

user_cooldowns = {}
COOLDOWN_SECONDS = 30  # 30-second cooldown per user[cite: 1]

@bot.event
async def on_ready():
    print(f"Oatmeal AI logged in successfully as {bot.user}[cite: 1]")

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return[cite: 1]

    is_mentioned = bot.user in message.mentions
    is_dm = isinstance(message.channel, discord.DMChannel)

    message_words = set(re.findall(r'\b\w+\b', message.content.lower()))
    has_trigger_word = bool(TRIGGER_WORDS.intersection(message_words))

    if not is_mentioned and not is_dm and not has_trigger_word:
        return[cite: 1]

    # --- 4. RATE LIMITING ---
    user_id = message.author.id
    current_time = time.time()
    
    if user_id in user_cooldowns:
        elapsed = current_time - user_cooldowns[user_id]
        if elapsed < COOLDOWN_SECONDS:
            if is_mentioned or is_dm:
                remaining = int(COOLDOWN_SECONDS - elapsed)
                await message.channel.send(
                    f"⏳ Please wait {remaining} seconds before my next response to conserve API free-tier quotas.[cite: 1]",
                    delete_after=10
                )
            return[cite: 1]

    user_cooldowns[user_id] = current_time

    clean_prompt = message.content.replace(f"<@!{bot.user.id}>", "").replace(f"<@{bot.user.id}>", "").strip()
    if not clean_prompt:
        clean_prompt = "Hello!"[cite: 1]

    full_prompt = f"Use the following background knowledge base to answer the user if relevant:\n{KNOWLEDGE_TEXT}\n\nUser Message: {clean_prompt}"[cite: 1]

    # --- 5. GEMINI API GENERATION (Non-Blocking Executor) ---
    try:
        async with message.channel.typing():
            await asyncio.sleep(6)  # Safety spacer for per-minute rate limits[cite: 1]

            response = await bot.loop.run_in_executor(
                None,
                lambda: ai_client.models.generate_content(
                    model=MODEL_NAME,
                    contents=full_prompt,
                )
            )
            
            reply_text = response.text

        if len(reply_text) > 2000:
            for i in range(0, len(reply_text), 2000):
                await message.channel.send(reply_text[i:i+2000])[cite: 1]
        else:
            await message.channel.send(reply_text)[cite: 1]

    except Exception as e:
        print(f"CRITICAL API ERROR: {type(e).__name__} - {e}")
        if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
            print("Quota limit (429 / RESOURCE_EXHAUSTED) hit.")
            if is_mentioned or is_dm:
                await message.channel.send("⚠️ Free-tier request limit reached. Please try again later.[cite: 1]")
        else:
            if is_mentioned or is_dm:
                await message.channel.send("❌ An error occurred while communicating with the AI model.[cite: 1]")

bot.run(DISCORD_TOKEN)[cite: 1]
