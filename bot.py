import os
import re
import time
import asyncio
import threading
import random
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

# --- 2. LOAD KNOWLEDGE FILE CONTENT ---
FALLBACK_KNOWLEDGE = """
Hawaii is a wonderful topic. The bot should remember information about Hawaii, 
including its islands, culture, and history when asked.
"""

KNOWLEDGE_TEXT = ""
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

# --- 3. CONFIGURATION & CLIENT SETUP ---
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

if not DISCORD_TOKEN or not GROQ_API_KEY:
    raise ValueError("Missing DISCORD_TOKEN or GROQ_API_KEY in environment variables.")

ai_client = OpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=GROQ_API_KEY
)

MODEL_NAME = "openai/gpt-oss-20b"

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

    # Respond to 90% of random unmentioned messages (10% chance to skip so it feels natural and random)
    if not is_mentioned and not is_dm:
        if random.random() > 0.90:
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

    # --- 5. DYNAMIC SYSTEM INSTRUCTIONS BASED ON INTERACTION TYPE ---
    if is_mentioned or is_dm:
        style_instruction = (
            "The user directly mentioned or messaged you. "
            "Provide a thorough, extended, and detailed response incorporating facts from your knowledge base."
        )
    else:
        style_instruction = (
            "This is a casual chat message. You must write a very short, punchy, casual message "
            "(1 to 2 short sentences max). Act just like Oatmeal Joey Arnold. Cleverly and randomly "
            "relate what the user said to facts, inside-baseball details, or easter eggs about your life "
            "found in the knowledge base."
        )

    system_prompt = (
        f"You are Oatmeal AI, embodying Joseph Scott Arnold (Oatmeal Joey Arnold). "
        f"Use the following background knowledge base to inform your persona and life details:\n{KNOWLEDGE_TEXT}\n\n"
        f"Guidelines: {style_instruction}"
    )

    # --- 6. GROQ API GENERATION (Non-Blocking Executor) ---
    try:
        async with message.channel.typing():
            response = await bot.loop.run_in_executor(
                None,
                lambda: ai_client.chat.completions.create(
                    model=MODEL_NAME,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": clean_prompt}
                    ],
                    temperature=0.8,
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
