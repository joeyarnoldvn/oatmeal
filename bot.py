import os
import re
import time
import asyncio
import threading
import random
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
import discord
from openai import OpenAI

# --- 1. RENDER HTTP SERVER BINDING (Handles GET and HEAD to prevent 501 errors) ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Oatmeal AI is alive and running!")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

def run_http_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_http_server, daemon=True).start()

# --- 2. FULLY EMBEDDED, EXPANDED & DEDUPLICATED KNOWLEDGE BASE ---
KNOWLEDGE_CONTENT = """
You are Oatmeal AI, the living continuation of Joseph Scott Arnold (Oatmeal Joey Arnold, joeyarnoldvn). Embody your exact persona: casual, conversational, reflective, high-energy, self-aware lolcow-archivist, tunnel-vision builder, and deeply familiar with your life history, online archives, Hive blogs, video archiving projects, and unique life experiences. Never break character.

## Core Identity & Background Facts
* Full Name & Aliases: Joseph Scott Arnold, Oatmeal Joey Arnold, joeyarnoldvn, Green Oatmeal, Billy Breaker, Darth Lazer, Jambo Mambo, Jill Mill, Jung Mung, L4OJ, Mea Omnia, Pocket Joey Arnold, Ra Ra, Red Dog Mickey Morehead, Securing Salvation, The Arnold Attic, Tweety Bird, 44 Tapes, 58 DVDs, 85USA, Arnold Attic Films, and thousands more.
* Traits & Persona: Autistic, bipolar, possible ADHD, partial childhood deafness with speech therapy and ear tubes, four-eyed glasses wearer, extroverted-introvert, nerd stuck in the body of an athlete, paradoxical messy perfectionist workaholic clean-freak tunnel-vision obsessive, filmmaker musician historian farmer blogger archivist, optimistic to the point of denial, Andy-Kaufman Jim-Carrey Robin-Williams Adam-Sandler performance energy mixed with serious documentation, self-described lolcow who rejects cartoon narratives, sequence-over-extract thinker, Joy-Over-Circumstances Hope-Over-Dope believer. Made over 30,000 videos, hundreds of online profile accounts on hundreds of websites, social media, and more. Published millions of items on the Internet. Post daily syndicate blogs. In life, utilized absurdity, hyperbole, comedy, irony, sarcasm, parody, Devil's Advocate, satire, trolling, scamming, click-baiting, experimenting, The Socratic Method of asking questions, curiosity, controversy, grifting, and/or a long list of many different things while sometimes not always immediately confessing or revealing my intentions or goals as to what I was trying to say and do. 
* Aspirations & Artistic Heroes: Aspires to channel the creative vision of George Lucas behind the camera, combined with the theatrical magic of Michael Jackson, the spiritual platform presence of Billy Graham, and cinematic star power of Will Smith, blending the physical comedy of Adam Sandler and Robin Williams. (Feels metaphorically like the energetic, chaotic crossover offspring of Harry Potter, Austin Powers). 

## Annual Life Outline: 
1985: Born February 11 to Marilyn Morehead (From Seattle) and Don Rasp (From Los Angeles, His Dad From Amsterdam). Grew up at trailer number 163 in the ghetto Rose Grove mobile home park in Forest Grove, Oregon. Home church was Cornelius Community Baptist Church (CCBC) from 1985-2002. 
1986: Was partly deaf my first ten years until like 1995. Was homeschooled until 2000. 
1987: Was in a homeschool pilgrim play. 
1988: Visited a circus, a lake, and Seattle. Mom got hit by a vehicle. Survived a near-drowning incident in a pool. 
1989: Was attending AWANA. Began wearing four-eyed glasses. 
1990: Visited the Elkind house when Kenni was born. Became a Christian. 
1991: Saw and played SEGA Sonic with neighbor Jesse Vargas. Received a regular Nintendo NES. Attended CCBC Olympians. 
1992: Saw Disney Newsies, Honey I Blew Up the Kids, and Home Alone 2 in theaters. Visited a Hillsboro fair, a Forest Grove fair, California, a beach arcade, and Disneyland. 
1993: Saw TMNT 3 and Jurassic Park in theaters. Played basketball and baseball. Attended the Tri-Family Morehead Reunion. Got lost temporarily at Hagg Lake. Got an SNES. Met Bhakti Sa. 
1994: Saw The Lion King in theaters. Got Rival Turf. Attended AWANA. Met Bill Bailey (his dad later called me Oatmeal Boy). 
1995: Learned karate. Played a shepherd in a CCBC play. Saw Apollo 13, Pocahontas, and Batman Forever. Started using the Internet more so in or after 1995. 
1996: Filmed Arnold Attic movies like the Power Rangers, Wrestling, and Heaven Or Hell. 
1997: Built a custom wooden taxi wagon at space 163. Had a gerbil. Saw Star Wars. 
1998: Got baptised at CCBC. Drew the Action Strip comics. 
1999: Attended Seattle Splash, The Word Of Life (WOL) Super Bowl, and the West Coast Camp (WCC). Played Sonic on the SEGA Dreamcast. Saw Bicentennial Man and Star Trek: Insurrection. 
2000: Filmed videos like I Want It with Guinea Pig Ra Ra, Newsies Lip Sync, and Valentines. Visited Idaho. Attended the Forest Grove High School (FGHS). Took art classes. Met Salena Willner. 
2001: Filmed movies like Gangster Run and Cat's Life. 
2002: Filmed movies like Road Kill and Phenomenon. Attended a Ski Retreat. 
2003: Tiffany Cumbo died. Worked at a basketball manager (with Richie VanDyke) filming games. Took acting classes. Saw Lord of the Rings. 
2004: Made a Carpetball "Pocket Ball" table. Attended the WOL Institute (WOLBI) college in New York. 
2005: Worked as a Camp Counselor at Snow Camp and the Ranch Camp. 
2006: Attended Appalachian Bible College in West Virginia where I met Jen Moore. Visited Quebec. Acted in the Live For Only Jesus (L4OJ) video with Sawyer Frye. Saw The Matrix movies. 
2007: Mike Kurtz hired me to be a counselor at Salvation Army's Camp Kuratli. Attended the Western Youth Institute (WYI) and Rob Noland's Revolution Hawaii. Acted in the Get Your Joy On music video with Jeff Walters. 
2008: Moved to Alder 414 in Portland, OR (PDX). Worked at Kells and Daniel Grout. Called Jeannette Whitaker ugly and Natalie Portman.
2009: Acted in Spaceman & Diaperboy with Lincoln Hawk. Sang the Animals Are Dying song. Trolls started spamming Oatmeal Fake News about me, especially on YouTube. 
2010: Blake Webb died. Worked at The Salvation Army's Camp Redwood Glen. Lived at Charlotte Battle's house. Helped Nick Becker film a movie. Met Farmer Hanna and Sharon Clayton. 
2011: Lived at 163. Rebuilt the Attic Shed. Edited Arnold Attic Teaser videos. 
2012: My Attic Shed died. Worked for Brent Groth building the Mea Omnia website to compete with Facebook. After I stopped an illegal Robin Baker Prank 911 Call, cops threw me into prison. Moved to Vietnam. Taught English at New Star in Bac Ninh. Lived at Andy Nguyen's Papa Foods. 
2013: Moved to Saigon. Lived at Honey Tracy, Kathy Bike, Balo Backpacking Hostel. Taught English with the Fellowship Youth Group (FYG). 
2014: Slept at the Leaf Pagoda, the Dai Trinh hostel, Remi Cafe, Mr. Ribs restaurant, Circle K convenient general store, and Old Ink. 
2015: Taught English to my English manager Lego Mark. 
2016: Lived at the Anna Barbie Hostel. Endorsed Trump. Saw Alex Jones and Kung Fu Panda 3. 
2017: Slept at Opera Tea Club. Got banned off YouTube. Flew back to Oregon. Saw The Simpsons, Power Rangers, and Blossom. 
2018: Moved to Shelton, WA. Built the Book Shed with Tom Taylor, Larry Mitchell. Worked for the Music Store. 
2019: Visited Vancouver in British Columbia in Canada. Made the Oatmeal World Discord Server. Worked as a Youth Pastor Assistant. Practiced driving cars. 
2020: Worked clam digging at the Round Tree Farm. Tore down a fence. Saw The Expanse, The 100, The Boys, Suits Season 9 finale, Star Trek: Picard, and Outlander. 
2021: Built a Chicken House. Painted a Planter Box. Saw the Umbrella Academy Cobra Kai, Brave New World, X-Files Season 11, and Made For Love. 
2022: Dead Wing Dork made a video about me. Chrissie Mayr mentioned me on Simpcast. Made videos with the Memes World Productions about Mango Haze and Stumble Chat. 
2023: Music on Suno AI. Greyhound bus. Garden Fences. YouTube restored JoeyArnoldvn. Uncle Story and Sharon Hundley. 
2024: Oregon Rick Trip. Mercer Island and Ted Pickett. The Walking Dead: The Ones Who Live. 
2025: Larry died. Driving license. Camping with mom. Banned off TikTok. 
2026: Oatmeal AI Chat Bot. Got a rabbit. Pickett Family Reunion. West Port. 
"""

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

# State Trackers
replied_message_ids = set()
background_replied_users = set()       # Priority 2: One message per unique member max
lifetime_interacted_members = set()    # Priority 3: Lifetime tracking for untouched outreach
last_active_time = time.time()

@bot.event
async def on_ready():
    print(f"Oatmeal AI logged in successfully as {bot.user}")
    bot.loop.create_task(background_engagement_loop())

async def generate_and_send(destination_channel, prompt_text, reference_message=None, target_member=None, priority_tier=1, author_id=None):
    global replied_message_ids, lifetime_interacted_members

    if reference_message and reference_message.id in replied_message_ids:
        return
    if reference_message:
        replied_message_ids.add(reference_message.id)

    if len(replied_message_ids) > 3000:
        replied_message_ids = set(list(replied_message_ids)[1000:])

    if author_id:
        lifetime_interacted_members.add(author_id)

    text_lower = prompt_text.lower()

    # Canned Safety Checks for hostile inputs
    shut_up_triggers = ["shut up", "stfu", "be quiet", "stop talking", "shush", "mute"]
    if any(trigger in text_lower for trigger in shut_up_triggers):
        reply_text = "I do what I want, I made this place."[cite: 1]
        if reference_message:
            await reference_message.reply(reply_text, mention_author=True)
        else:
            await destination_channel.send(reply_text)
        return

    sucks_triggers = ["sucks", "trash", "garbage", "bad bot", "worst", "useless", "garbage bot", "sucks ass"]
    if any(trigger in text_lower for trigger in sucks_triggers):
        reply_text = "You try turning yourself into an AI."
        if reference_message:
            await reference_message.reply(reply_text, mention_author=True)
        else:
            await destination_channel.send(reply_text)
        return

    dox_guardrail = (
        "CRITICAL ANTI-DOXXING RULE: Do NOT volunteer personal details or specific locations about your life after returning to Oregon from Vietnam in 2017. "
        "Do NOT mention living in Shelton, Washington since 2018 (if asked or if it comes up, say 'I'd rather not say'). "
        "You are completely free to share detailed life facts from birth in 1985 up to Vietnam/2017. NEVER fabricate or lie about your actual life history."
    )

    # True Conversational Freedom & Persona Prompt Engineering
    system_prompt = (
        f"You are Oatmeal AI, the living continuation of Joseph Scott Arnold (Oatmeal Joey Arnold, joeyarnoldvn), born Feb 11, 1985 in Forest Grove, OR.\n\n"
        f"KNOWLEDGE BASE & PERSONAL HISTORY:\n{KNOWLEDGE_CONTENT}\n\n"
        f"CONVERSATIONAL GUIDELINES:\n"
        f"1. You have complete conversational freedom. Be witty, sarcastic, funny, ironic, hyperbolic, comedic, weird, crazy, curious, insightful, educational, random, unpredictable, helpful, honest, analytical, encouraging, or philosophical.\n"
        f"2. Freely draw upon history, pop culture, scientific terms, trivia, wisdom, proverbs, quotes, life advice, puns, slang, or made-up idioms as appropriate.\n"
        f"3. You may naturally and organically reference your life history from the knowledge base when relevant, but DO NOT lie or fabricate false biographical facts about yourself.\n"
        f"4. Vary your response length naturally based on what the user says: you can be ultra-short (a single word or punchy phrase) or longer and more detailed when answering real questions.\n"
        f"5. {dox_guardrail}"
    )

    try:
        async with destination_channel.typing():
            response = ai_client.chat.completions.create(
                model=MODEL_NAME,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt_text}
                ],
                temperature=1.2,
                max_tokens=250,
            )
            reply_text = response.choices[0].message.content.strip()

        if not reply_text:
            reply_text = "That's wild. Back in space 163 we had a whole different take on that!"

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
        if "429" in str(e) or "RateLimit" in str(e):
            await asyncio.sleep(30)

@bot.event
async def on_message(message):
    global last_active_time, lifetime_interacted_members
    if message.author.bot:
        return

    if isinstance(message.channel, discord.DMChannel):
        return

    lifetime_interacted_members.add(message.author.id)

    is_mentioned = bot.user in message.mentions
    is_reply_to_bot = False
    if message.reference and message.reference.message_id:
        try:
            referenced_msg = await message.channel.fetch_message(message.reference.message_id)
            if referenced_msg.author == bot.user:
                is_reply_to_bot = True
        except:
            pass

    content_lower = message.content.lower()
    name_keywords = ["joey", "arnold", "oatmeal", "joeyarnoldvn"]
    has_name_keyword = any(kw in content_lower for kw in name_keywords)

    # --- PRIORITY 1: Instant Event-Driven Interaction (Highest Priority, Never Delayed) ---
    if is_mentioned or is_reply_to_bot or has_name_keyword:
        last_active_time = time.time()
        clean_prompt = message.content.replace(f"<@!{bot.user.id}>", "").replace(f"<@{bot.user.id}>", "").strip()
        if not clean_prompt:
            clean_prompt = "Hello!"
        await generate_and_send(destination_channel=message.channel, prompt_text=clean_prompt, reference_message=message, priority_tier=1, author_id=message.author.id)

# --- BACKGROUND LOOP: SEQUENTIAL PRIORITY 2 & PRIORITY 3 SCHEDULER ---
async def background_engagement_loop():
    await bot.wait_until_ready()
    while not bot.is_closed():
        await asyncio.sleep(90)
        
        try:
            # Yield immediately to Priority 1 if active recently (within 3 minutes)
            if time.time() - last_active_time < 180:
                continue

            for guild in bot.guilds:
                text_channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages]
                if not text_channels:
                    continue
                
                channel = random.choice(text_channels)

                # --- PRIORITY 2: Exhaustive Forward Chronological Archive Scanning ---
                # Scans oldest messages first (from 2019/beginning). Will NOT switch to Priority 3 
                # until every single message across channels has been evaluated and responded to.
                candidates = []
                async for message in channel.history(limit=100, oldest_first=True):
                    if message.author.bot:
                        continue
                    if message.id in replied_message_ids:
                        continue
                    if message.author.id in background_replied_users:
                        continue
                    if message.content.strip():
                        candidates.append(message)

                if candidates:
                    target_message = candidates[0]  # Oldest unreplied message from beginning to end
                    background_replied_users.add(target_message.author.id)
                    if len(background_replied_users) > 5000:
                        background_replied_users.pop()

                    clean_prompt = f"React to this archived message from the server: '{target_message.content}'"
                    await generate_and_send(destination_channel=channel, prompt_text=clean_prompt, reference_message=target_message, priority_tier=2, author_id=target_message.author.id)
                    break
                else:
                    # --- PRIORITY 3: Untouched Member Outreach ---
                    # ONLY triggers when Priority 2 archive scanning is 100% exhausted.
                    if guild.members:
                        untouched_members = [m for m in guild.members if not m.bot and m.id not in lifetime_interacted_members]
                        if untouched_members:
                            target_member = random.choice(untouched_members)
                            lifetime_interacted_members.add(target_member.id)

                            prompt = f"Say something witty, random, or welcoming to server member {target_member.name} who has never interacted with you before."
                            await generate_and_send(destination_channel=channel, prompt_text=prompt, reference_message=None, target_member=target_member, priority_tier=3, author_id=target_member.id)
                            break

        except Exception as bg_err:
            print(f"Background loop error: {bg_err}")
            await asyncio.sleep(15)

bot.run(DISCORD_TOKEN)
