import os
import time
import random
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

# Start background web server thread for Render
threading.Thread(target=run_http_server, daemon=True).start()

# --- 2. CONFIGURATION & CLIENT SETUP ---
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

if not DISCORD_TOKEN or not GEMINI_API_KEY:
    raise ValueError("Missing DISCORD_TOKEN or GEMINI_API_KEY in environment variables.")

# Initialize the official Google GenAI SDK client
ai_client = genai.Client(api_key=GEMINI_API_KEY)
MODEL_NAME = "gemini-3.8-flash"

# Setup Discord intents
intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)

# Rate limiting and probability settings
user_cooldowns = {}
COOLDOWN_SECONDS = 30  # Enforces a 1-minute pause per user
CHANCE_TO_RESPOND = 0.05  # 5% chance to respond to random unmentioned chat messages

@bot.event
async def on_ready():
    print(f"Oatmeal AI logged in successfully as {bot.user}")

@bot.event
async def on_message(message):
    # Ignore messages from the bot itself
    if message.author == bot.user:
        return

    # Check mention / DM conditions
    is_mentioned = bot.user in message.mentions
    is_dm = isinstance(message.channel, discord.DMChannel)

    # If NOT mentioned and NOT a DM, roll the probability check
    if not is_mentioned and not is_dm:
        if random.random() > CHANCE_TO_RESPOND:
            return  # Skip responding to save API quota

    # --- 3. RATE LIMITING & QUOTA PROTECTION ---
    user_id = message.author.id
    current_time = time.time()
    
    if user_id in user_cooldowns:
        elapsed = current_time - user_cooldowns[user_id]
        if elapsed < COOLDOWN_SECONDS:
            if is_mentioned or is_dm:
                remaining = int(COOLDOWN_SECONDS - elapsed)
                await message.channel.send(
                    f"⏳ Please wait {remaining} seconds before my next response to conserve API free-tier quotas.",
                    delete_after=10
                )
            return

    user_cooldowns[user_id] = current_time

    # Clean message content by stripping the bot mention
    clean_prompt = message.content.replace(f"<@!{bot.user.id}>", "").replace(f"<@{bot.user.id}>", "").strip()
    if not clean_prompt:
        clean_prompt = "Hello!"

    # --- 4. GEMINI API GENERATION ---
    try:
        async with message.channel.typing():
            await asyncio.sleep(6)  # Safety spacer for per-minute rate limits

            response = ai_client.models.generate_content(
                model=MODEL_NAME,
                contents=clean_prompt,
            )
            
            reply_text = response.text

        if len(reply_text) > 2000:
            for i in range(0, len(reply_text), 2000):
                await message.channel.send(reply_text[i:i+2000])
        else:
            await message.channel.send(reply_text)

    except Exception as e:
        print(f"API Error: {e}")
        if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
            if is_mentioned or is_dm:
                await message.channel.send("⚠️ Free-tier daily or minute request limit reached (Quota exhausted). Please try again later.")
        else:
            if is_mentioned or is_dm:
                await message.channel.send("❌ An error occurred while communicating with the AI model.")

bot.run(DISCORD_TOKEN)
