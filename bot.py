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

# --- 2. FETCH THE SPECIFIC HIVE ARTICLE FOR THE TEST ---
def fetch_target_article():
    # Fetching JSON/HTML data from the Hive post endpoint or fallback text
    url = "https://api.hive.blog"
    headers = {'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'}
    payload = json.dumps({
        "jsonrpc": "2.0",
        "method": "condenser_api.get_content",
        "params": ["joeyarnoldvn", "npc-lolcow-trolls-v-oatmeal"],
        "id": 1
    }).encode('utf-8')
    
    try:
        req = urllib.request.Request(url, data=payload, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as response:
            res_data = json.loads(response.read().decode('utf-8'))
            if 'result' in res_data and res_data['result'] and 'body' in res_data['result']:
                body_text = res_data['result']['body']
                print("SUCCESS: Fetched target article content from Hive API!")
                return body_text
    except Exception as e:
        print(f"Could not fetch target article via API, using fallback text: {e}")
    
    # Fallback text summarizing the article theme if API is restricted
    return (
        "I titled this article 'NPC Lolcow Trolls v Oatmeal' to talk about me versus the haters. "
        "People fixated on a fake invisible shadow, easily debunkable lies, and trolls acting like NPCs "
        "obsessed with talking about Crazy Oatmeal with the goal of pressuring me to leave the Internet."
    )

ARTICLE_CONTENT = fetch_target_article()

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
            "Provide a detailed response drawing directly on the thoughts and rants from your 'NPC Lolcow Trolls v Oatmeal' article."
        )
    else:
        style_instruction = (
            "Write an EXTREMELY SHORT, punchy message (1 word to maximum 1 short sentence). "
            "Do NOT mention VHS tapes or 1996. Instead, draw strictly from the themes of trolls, NPCs, haters, "
            "and commentary found in your 'NPC Lolcow Trolls v Oatmeal' article. Keep it random and varied."
        )

    system_prompt = (
        f"You are Oatmeal AI, embodying Joseph Scott Arnold (Oatmeal Joey Arnold). "
        f"Base your persona, thoughts, and words EXCLUSIVELY on this specific article text:\n\n{ARTICLE_CONTENT}\n\n"
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
                temperature=0.95,  # High temperature for maximum variety and zero repetition
            )
            reply_text = response.choices[0].message.content

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
        await asyncio.sleep(50)
        try:
            for guild in bot.guilds:
                text_channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages]
                if not text_channels:
                    continue
                
                channel = random.choice(text_channels)
                action_choice = random.choice(["chat", "member", "member"])

                if action_choice == "member" and guild.members:
                    non_bot_members = [m for m in guild.members if not m.bot and m.id not in recent_replied_users]
                    if non_bot_members:
                        target_member = random.choice(non_bot_members)
                        recent_replied_users.append(target_member.id)
                        if len(recent_replied_users) > 10:
                            recent_replied_users.pop(0)
                        
                        prompt = f"Say something quick and random about internet trolls or NPCs to server member {target_member.display_name}."
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
