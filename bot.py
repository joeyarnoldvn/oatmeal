import os
import re
import time
import asyncio
import threading
import random
import urllib.request
import xml.etree.ElementTree as ET
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

# --- 2. LOAD KNOWLEDGE FILE & HIVERSS FEED ---
FALLBACK_KNOWLEDGE = """
Joseph Scott Arnold (Oatmeal Joey Arnold) manages online channels, YouTube, Rumble, 
and Hive Blog, born in Oregon in 1985. Home video projects started in 1996.
"""

KNOWLEDGE_TEXT = ""
KNOWLEDGE_FILE = "knowledge.txt"
current_dir = os.path.dirname(os.path.abspath(__file__))
file_path = os.path.join(current_dir, KNOWLEDGE_FILE)

if os.path.exists(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        KNOWLEDGE_TEXT = f.read()
else:
    KNOWLEDGE_TEXT = FALLBACK_KNOWLEDGE

# Fetch recent blog titles from your Hiverss feed (100% free, zero external API keys needed)
def fetch_hiverss_updates():
    try:
        url = "https://hiverss.com/@joeyarnoldvn"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            xml_data = response.read()
            root = ET.fromstring(xml_data)
            titles = []
            for item in root.findall('.//item')[:5]:
                title = item.find('title')
                if title is not None and title.text:
                    titles.append(title.text)
            if titles:
                return "\nRecent Hiverss Blog Posts:\n- " + "\n- ".join(titles)
    except Exception as e:
        print(f"Could not fetch Hiverss feed: {e}")
    return ""

hiverss_updates = fetch_hiverss_updates()
FULL_KNOWLEDGE_BASE = KNOWLEDGE_TEXT + "\n\n" + hiverss_updates

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
intents.members = True  # Required to rotate through all server members
bot = discord.Client(intents=intents)

replied_message_ids = set()
recent_replied_users = []
user_cooldowns = {}
COOLDOWN_SECONDS = 10  

@bot.event
async def on_ready():
    print(f"Oatmeal AI logged in successfully as {bot.user}")
    bot.loop.create_task(background_engagement_loop())

async def generate_and_send(destination_channel, prompt_text, reference_message=None, target_member=None, is_extended=False):
    if reference_message and reference_message.id in replied_message_ids:
        return
    if reference_message:
        replied_message_ids.add(reference_message.id)

    if len(replied_message_ids) > 1000:
        list(replied_message_ids)[:200]

    if is_extended:
        style_instruction = (
            "The user directly mentioned or replied to you. "
            "Provide a thorough, detailed response incorporating verified facts from your knowledge base and blog."
        )
    else:
        style_instruction = (
            "Write an EXTREMELY SHORT, punchy message (maximum 1 short sentence, or even just a single word). "
            "Never use 'just kidding'. Stick strictly to true facts and real history from your background knowledge "
            "(remember: home video projects started in 1996, NOT 1995). Act just like Joseph Scott Arnold (Oatmeal Joey Arnold)."
        )

    system_prompt = (
        f"You are Oatmeal AI, embodying Joseph Scott Arnold (Oatmeal Joey Arnold). "
        f"Use the following background knowledge base and recent Hiverss blog updates:\n{FULL_KNOWLEDGE_BASE}\n\n"
        f"Guidelines: {style_instruction}"
    )

    try:
        async with destination_channel.typing():
            response = ai_client.chat.completions.create(
                model=MODEL_NAME,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt_text}
                ],
                temperature=0.7,
            )
            reply_text = response.choices[0].message.content

        if len(reply_text) > 2000:
            reply_text = reply_text[:2000]

        # If replying to an older message
        if reference_message:
            await reference_message.reply(reply_text, mention_author=True)
        # If doing a silent member shoutout, explicitly include their mention tag
        elif target_member:
            await destination_channel.send(f"{target_member.mention} {reply_text}")
        else:
            await destination_channel.send(reply_text)

    except Exception as e:
        print(f"CRITICAL API ERROR: {type(e).__name__} - {e}")

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    is_mentioned = bot.user in message.mentions
    is_dm = isinstance(message.channel, discord.DMChannel)

    if not is_mentioned and not is_dm and random.random() > 0.90:
        return

    clean_prompt = message.content.replace(f"<@!{bot.user.id}>", "").replace(f"<@{bot.user.id}>", "").strip()
    if not clean_prompt:
        clean_prompt = "Hello!"

    await generate_and_send(message.channel, clean_prompt, reference_message=message, is_extended=(is_mentioned or is_dm))

# --- BACKGROUND LOOP: FAIR USER ROTATION & SILENT MEMBER SHOUTOUTS ---
async def background_engagement_loop():
    await bot.wait_until_ready()
    while not bot.is_closed():
        await asyncio.sleep(50)  # Safe interval to stay well within Groq limits
        try:
            for guild in bot.guilds:
                text_channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages]
                if not text_channels:
                    continue
                
                channel = random.choice(text_channels)

                # Alternate between engaging with chat history and mentioning a silent/un-engaged member
                action_choice = random.choice(["chat", "member", "member"])

                if action_choice == "member" and guild.members:
                    non_bot_members = [m for m in guild.members if not m.bot and m.id not in recent_replied_users]
                    if non_bot_members:
                        target_member = random.choice(non_bot_members)
                        recent_replied_users.append(target_member.id)
                        if len(recent_replied_users) > 10:
                            recent_replied_users.pop(0)
                        
                        prompt = f"Say something quick and clever nodding to server member {target_member.display_name}."
                        await generate_and_send(channel, prompt, reference_message=None, target_member=target_member, is_extended=False)
                        break

                candidates = []
                async for message in channel.history(limit=35):
                    if message.author.bot:
                        continue
                    if message.id in replied_message_ids:
                        continue
                    if message.author.id in recent_replied_users:
                        continue  
                    if message.content.strip():
                        candidates.append(message)

                if not candidates:
                    async for message in channel.history(limit=15):
                        if not message.author.bot and message.id not in replied_message_ids and message.content.strip():
                            candidates.append(message)

                if candidates:
                    target_message = random.choice(candidates)
                    recent_replied_users.append(target_message.author.id)
                    if len(recent_replied_users) > 10:
                        recent_replied_users.pop(0)

                    clean_prompt = target_message.content.strip()
                    await generate_and_send(channel, clean_prompt, reference_message=target_message, is_extended=False)
                    break

        except Exception as bg_err:
            print(f"Background loop error: {bg_err}")

bot.run(DISCORD_TOKEN)
