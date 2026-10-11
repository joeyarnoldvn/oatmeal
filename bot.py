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
last_manual_interaction_time = 0  # Timestamp to enforce 1-hour pause after server interactions

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

    # Anti-doxxing guardrail instruction
    dox_guardrail = (
        "CRITICAL ANTI-DOXXING RULE: Do NOT volunteer personal details or specific locations about your life after returning to Oregon from Vietnam in 2017. "
        "Do NOT mention living in Shelton, Washington since 2018 (if asked or if it comes up, say 'I'd rather not say'). "
        "You are completely free to share detailed life facts from birth in 1985 up to Vietnam/2017 (e.g. space 163 in Forest Grove, Arnold Attic films, Vietnam teaching). "
        "After 2017, keep references strictly general (e.g., gardening, building a wood shed, blogging) without revealing exact addresses or doxxing."
    )

    if is_extended:
        last_manual_interaction_time = time.time()  # Trigger 1-hour pause for auto-scanning
        style_instruction = (
            f"{dox_guardrail}\n"
            "Priority 1 (Active Server Interaction): The user pinged, replied to, or mentioned you in a server channel. "
            "Write a complete, fully finished response. Do NOT cut off mid-sentence. Vary length naturally as needed (casual, flexible). "
            "Draw from your life history (1985–2017 freely, general post-2017) or drop trivia, wisdom, or engaging insights without repeating previous phrases."
        )
    else:
        is_priority_3 = (target_member is not None and reference_message is None)
        
        if is_priority_3:
            style_instruction = (
                f"{dox_guardrail}\n"
                "Priority 3 (Alphabetical Member Shoutout - 10% frequency): You are reaching out to a server member who hasn't messaged recently. "
                "Tag their username (@username) and deliver a unique conversation starter, icebreaker, question (e.g. 'what do you want to be when you grow up?', 'if you were an animal...'), "
                "or trivia/DIY life hack/science/history/Bible/language trivia. "
                "DO NOT write their display name after the tag. Keep it fresh, unique, unpredictable, and COMPLETELY FINISHED (never cut off mid-sentence)!"
            )
        else:
            style_instruction = (
                f"{dox_guardrail}\n"
                "Priority 2 (Server Message Reply - 90% frequency): Reply to an existing server message. "
                "DO NOT tag or mention the user's name or username with an @ in your text (Discord's reply feature already handles attribution). "
                "90% of the time, naturally reference a unique fact from your life history (1985–2017 freely, general post-2017) ensuring you do NOT repeat facts used earlier today. "
                "10% of the time, share random trivia, wisdom, code, or witty thoughts. "
                "Length: Casual and flexible. 90% of the time keep it concise (1 to 20 words), but make sure the sentence or thought is COMPLETE and never truncated or cut off mid-sentence!"
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
                temperature=0.9,
                max_tokens=400,  # Ensure sufficient token allotment so sentences never get cut off mid-way
            )
            reply_text = response.choices[0].message.content.strip()

        # Ensure text does not cut off mid-sentence; trim cleanly to the last punctuation mark if needed
        if not is_extended and not target_member:
            words = reply_text.split()
            if len(words) > 15 and random.random() < 0.85:
                # Keep it concise (1 to 15 words) while keeping it complete
                take_count = random.randint(3, min(15, len(words)))
                reply_text = " ".join(words[:take_count])
                # Ensure it ends cleanly with punctuation if truncated
                if not reply_text.endswith(('.', '!', '?', '"', "'")):
                    reply_text += "."

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

    if isinstance(message.channel, discord.DMChannel):
        return

    is_mentioned = bot.user in message.mentions
    is_reply_to_bot = False
    if message.reference and message.reference.message_id:
        try:
            referenced_msg = await message.channel.fetch_message(message.reference.message_id)
            if referenced_msg.author == bot.user:
                is_reply_to_bot = True
        except:
            pass

    # Priority 1 Trigger: Instant response when pinged, mentioned, or replied to
    if is_mentioned or is_reply_to_bot:
        clean_prompt = message.content.replace(f"<@!{bot.user.id}>", "").replace(f"<@{bot.user.id}>", "").strip()
        if not clean_prompt:
            clean_prompt = "Hello!"
        await generate_and_send(message.channel, clean_prompt, reference_message=message, is_extended=True)

# --- BACKGROUND LOOP: INSTANT START, NO WAITING, 90/10 PRIORITY 2 & 3 SPLIT ---
async def background_engagement_loop():
    await bot.wait_until_ready()
    # Start immediately upon boot without waiting 1 hour, so you can see it work right away!
    while not bot.is_closed():
        # Short check interval (15 seconds) so it constantly processes older server messages and active members
        await asyncio.sleep(15)
        try:
            # 1-hour pause only applies after active user interaction (Priority 1)
            if time.time() - last_manual_interaction_time < 3600:
                continue

            for guild in bot.guilds:
                text_channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages]
                if not text_channels:
                    continue
                
                channel = random.choice(text_channels)
                action_roll = random.random()

                if action_roll < 0.90:
                    # Priority 2: 90% of the time, scan older server messages (no matter how old) across thousands of messages, replying to each unique member once
                    candidates = []
                    seen_authors_in_batch = set()
                    async for message in channel.history(limit=100): # Deep history scan
                        if message.author.bot:
                            continue
                        if message.id in replied_message_ids:
                            continue
                        if message.author.id in recent_replied_users:
                            continue
                        if message.author.id in seen_authors_in_batch:
                            continue # One unique member per reply cycle
                        
                        if message.content.strip():
                            seen_authors_in_batch.add(message.author.id)
                            candidates.append(message)

                    if candidates:
                        target_message = random.choice(candidates)
                        recent_replied_users.append(target_message.author.id)
                        if len(recent_replied_users) > 50:
                            recent_replied_users.pop(0)

                        clean_prompt = target_message.content.strip()
                        await generate_and_send(channel, clean_prompt, reference_message=target_message, is_extended=False)
                        break
                else:
                    # Priority 3: 10% of the time, alphabetical member shoutouts using username only (@username)
                    if guild.members:
                        sorted_members = sorted([m for m in guild.members if not m.bot and m.id not in recent_replied_users], key=lambda m: m.display_name.lower())
                        if sorted_members:
                            target_member = sorted_members[0]
                            recent_replied_users.append(target_member.id)
                            if len(recent_replied_users) > 50:
                                recent_replied_users.pop(0)
                            
                            prompt = f"Say an icebreaker or trivia question to server member {target_member.name}."
                            await generate_and_send(channel, prompt, reference_message=None, target_member=target_member, is_extended=False)
                            break

        except Exception as bg_err:
            print(f"Background loop error: {bg_err}")

bot.run(DISCORD_TOKEN)
