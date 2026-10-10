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
intents.guilds = True
intents.messages = True
bot = discord.Client(intents=intents)

# Track replied message IDs to prevent getting stuck in loops
replied_message_ids = set()
user_cooldowns = {}
COOLDOWN_SECONDS = 10  

@bot.event
async def on_ready():
    print(f"Oatmeal AI logged in successfully as {bot.user}")
    # Start the background idle-activity loop to engage with older/other messages
    bot.loop.create_task(background_engagement_loop())

async def generate_and_send(message, prompt_text, is_extended=False):
    if message.id in replied_message_ids:
        return
    replied_message_ids.add(message.id)

    # Limit set size to prevent memory bloat over time
    if len(replied_message_ids) > 1000:
        list(replied_message_ids)[:200]

    if is_extended:
        style_instruction = (
            "The user directly mentioned or replied to you. "
            "Provide a thorough, extended, and detailed response incorporating verified facts from your knowledge base."
        )
    else:
        style_instruction = (
            "This is a casual chat message. Write a very short, punchy, casual message (1 to 2 short sentences max). "
            "Act just like Oatmeal Joey Arnold. CLEVERLY AND STRICTLY AVOID FABRICATING FALSE EVENTS: "
            "Do not make up fake activities or lie about things you didn't do. Instead, use real facts, "
            "history, or inside-baseball details from the knowledge base, or ask a clever, witty, thought-provoking "
            "question related to what the user said."
        )

    system_prompt = (
        f"You are Oatmeal AI, embodying Joseph Scott Arnold (Oatmeal Joey Arnold). "
        f"Use the following background knowledge base to inform your persona and life details. "
        f"CRITICAL RULE: Never fabricate or hallucinate random activities. Stick to true facts, real history, "
        f"or ask clever, engaging questions:\n{KNOWLEDGE_TEXT}\n\n"
        f"Guidelines: {style_instruction}"
    )

    try:
        async with message.channel.typing():
            response = ai_client.chat.completions.create(
                model=MODEL_NAME,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt_text}
                ],
                temperature=0.7,
            )
            reply_text = response.choices[0].message.content

        # Use message.reply() so it explicitly mentions and references the target message
        if len(reply_text) > 2000:
            for i in range(0, len(reply_text), 2000):
                await message.reply(reply_text[i:i+2000], mention_author=True)
        else:
            await message.reply(reply_text, mention_author=True)

    except Exception as e:
        print(f"CRITICAL API ERROR: {type(e).__name__} - {e}")

@bot.event
async def on_message(message):
    # Ignore messages from the bot itself or other bots (avoids bot loops and welcome message loops)
    if message.author.bot:
        return

    is_mentioned = bot.user in message.mentions
    is_dm = isinstance(message.channel, discord.DMChannel)

    # Random unmentioned filter (90% response rate for live messages)
    if not is_mentioned and not is_dm and random.random() > 0.90:
        return

    clean_prompt = message.content.replace(f"<@!{bot.user.id}>", "").replace(f"<@{bot.user.id}>", "").strip()
    if not clean_prompt:
        clean_prompt = "Hello!"

    await generate_and_send(message, clean_prompt, is_extended=(is_mentioned or is_dm))

# --- BACKGROUND LOOP FOR OLDER MESSAGE ENGAGEMENT ---
async def background_engagement_loop():
    await bot.wait_until_ready()
    while not bot.is_closed():
        await asyncio.sleep(45)  # Check periodically to stay active without burning limits too fast
        try:
            for guild in bot.guilds:
                # Find text channels the bot can send messages in
                text_channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages]
                if not text_channels:
                    continue
                
                # Pick a random channel to keep things distributed
                channel = random.choice(text_channels)
                
                # Fetch recent messages in reverse chronological order
                async for message in channel.history(limit=30):
                    # Skip bot messages (prevents picking up welcome bots or self-loops)
                    if message.author.bot:
                        continue
                    # Skip already replied messages
                    if message.id in replied_message_ids:
                        continue
                    
                    # Found an unreplied human message in history!
                    clean_prompt = message.content.strip()
                    if clean_prompt:
                        print(f"Background replying to older message from {message.author.name}: {clean_prompt[:30]}...")
                        await generate_and_send(message, clean_prompt, is_extended=False)
                        break  # Engage one message per cycle to space out API calls safely
        except Exception as bg_err:
            print(f"Background loop error: {bg_err}")

bot.run(DISCORD_TOKEN)
