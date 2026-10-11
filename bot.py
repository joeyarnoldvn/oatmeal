import os
import re
import time
import asyncio
import threading
import random
import urllib.request
import json
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

# --- 2. FETCH KNOWLEDGE FROM GITHUB REPO (knowledge.txt) ---
def fetch_knowledge_from_github():
    github_url = os.environ.get("KNOWLEDGE_GITHUB_URL", "https://raw.githubusercontent.com/joeyarnoldvn/oatmeal/main/knowledge.txt")
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    try:
        req = urllib.request.Request(github_url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as response:
            knowledge_text = response.read().decode('utf-8')
            print("SUCCESS: Fetched knowledge base from GitHub repository (knowledge.txt)!")
            return knowledge_text
    except Exception as e:
        print(f"Could not fetch knowledge from GitHub, using fallback text: {e}")
    
    return (
        "You are Joseph Scott Arnold, also known as Oatmeal Joey Arnold (joeyarnoldvn), "
        "born February 11, 1985 in Forest Grove, Oregon. You are a prolific blogger, filmmaker, "
        "and digital archivist with decades of internet history."
    )

KNOWLEDGE_CONTENT = fetch_knowledge_from_github()

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
intents.members = True
bot = discord.Client(intents=intents)

replied_message_ids = set()
recent_replied_users = []
last_manual_interaction_time = 0  # Timestamp to enforce 10-minute cooldown after mentions/DMs

@bot.event
async def on_ready():
    print(f"Oatmeal AI logged in successfully as {bot.user}")
    bot.loop.create_task(background_engagement_loop())

async def generate_and_send(destination_channel, prompt_text, reference_message=None, target_member=None, is_extended=False):
    global last_manual_interaction_time

    if reference_message and reference_message.id in replied_message_ids:
        return
    if reference_message:
        replied_message_ids.add(reference_message.id)

    if len(replied_message_ids) > 1000:
        list(replied_message_ids)[:200]

    if is_extended:
        last_manual_interaction_time = time.time()  # Trigger 10-minute pause for auto-scanning
        style_instruction = (
            "The user directly mentioned, replied to, or DM'd you. Write an extended, detailed answer drawing directly "
            "from your life facts and knowledge base in knowledge.txt. Talk like Joseph Scott Arnold / Oatmeal Joey Arnold: "
            "casual, reflective, high-energy, self-aware, and sequence-focused."
        )
    else:
        style_instruction = (
            "Be extremely unpredictable, random, and varied. Mix it up completely so no one can guess what you will do! "
            "CRITICAL REQUIREMENT (50% frequency): Naturally reference specific details from your life history in knowledge.txt "
            "(e.g., growing up at space 163 in Forest Grove, traveling or teaching English in Vietnam, moving to Shelton Washington, "
            "filming Arnold Attic home videos, farming/gardening, VCR/Vtt archives, working landscaping). "
            "For the other 50%, ask unique unexpected questions, drop wisdom, or share witty thoughts without being repetitive. "
            "Length: Unpredictable and mixed. Sometimes 1 word, sometimes a short phrase, sometimes up to 5-8 words. Never use repetitive templates!"
        )

    system_prompt = (
        f"You are Oatmeal AI, embodying Joseph Scott Arnold (Oatmeal Joey Arnold / joeyarnoldvn), born Feb 11, 1985 in Forest Grove, OR. "
        f"Base your persona, history, and knowledge exclusively on this knowledge base fetched from GitHub (knowledge.txt):\n\n{KNOWLEDGE_CONTENT}\n\n"
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
                temperature=1.0,
            )
            reply_text = response.choices[0].message.content.strip()

        if not is_extended:
            # Vary sentence/word count unpredictably (1 to 7 words mostly, occasionally slightly longer)
            words = reply_text.split()
            if words and random.random() < 0.80:
                take_count = random.randint(1, min(7, len(words)))
                reply_text = " ".join(words[:take_count])

        if len(reply_text) > 2000:
            reply_text = reply_text[:2000]

        if reference_message:
            await reference_message.reply(reply_text, mention_author=True)
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

    if not is_mentioned and not is_dm and random.random() > 0.94:
        return

    clean_prompt = message.content.replace(f"<@!{bot.user.id}>", "").replace(f"<@{bot.user.id}>", "").strip()
    if not clean_prompt:
        clean_prompt = "Oatmeal"

    await generate_and_send(message.channel, clean_prompt, reference_message=message, is_extended=(is_mentioned or is_dm))

# --- BACKGROUND LOOP: SLOWED DOWN TO CONSERVE TOKENS (<80% TPD) & 10-MIN PAUSE ---
async def background_engagement_loop():
    await bot.wait_until_ready()
    while not bot.is_closed():
        # Slowed down significantly (600 seconds / 10 minutes per background check) to stay well below 80% TPD limit
        await asyncio.sleep(600)
        try:
            # Enforce 10-minute pause if someone recently mentioned, replied to, or DM'd the bot
            if time.time() - last_manual_interaction_time < 600:
                continue

            for guild in bot.guilds:
                text_channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages]
                if not text_channels:
                    continue
                
                channel = random.choice(text_channels)
                
                # Priority 1 & 2: Scan channel history for unique users (one reply per user, skipping duplicates)
                candidates = []
                seen_authors_in_batch = set()
                async for message in channel.history(limit=30):
                    if message.author.bot:
                        continue
                    if message.id in replied_message_ids:
                        continue
                    if message.author.id in recent_replied_users:
                        continue
                    if message.author.id in seen_authors_in_batch:
                        continue # Skip consecutive/duplicate messages from the same user!
                    
                    if message.content.strip():
                        seen_authors_in_batch.add(message.author.id)
                        candidates.append(message)

                if candidates:
                    target_message = random.choice(candidates)
                    recent_replied_users.append(target_message.author.id)
                    if len(recent_replied_users) > 25:
                        recent_replied_users.pop(0)

                    clean_prompt = target_message.content.strip()
                    await generate_and_send(channel, clean_prompt, reference_message=target_message, is_extended=False)
                    break
                
                # Priority 3: If no unique messages in channel history, fallback to alphabetical member shoutouts
                if guild.members:
                    sorted_members = sorted([m for m in guild.members if not m.bot and m.id not in recent_replied_users], key=lambda m: m.display_name.lower())
                    if sorted_members:
                        target_member = sorted_members[0] # Pick alphabetically first available
                        recent_replied_users.append(target_member.id)
                        if len(recent_replied_users) > 25:
                            recent_replied_users.pop(0)
                        
                        prompt = f"Say something varied, random, or referencing your life history to server member {target_member.display_name}."
                        await generate_and_send(channel, prompt, reference_message=None, target_member=target_member, is_extended=False)
                        break

        except Exception as bg_err:
            print(f"Background loop error: {bg_err}")

bot.run(DISCORD_TOKEN)
