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

# --- 2. FULL LIFE OUTLINE & KNOWLEDGE BASE ---
FULL_ANNUAL_OUTLINE = """
1985: Born February 11 to Marilyn Morehead (From Seattle) and Don Rasp (From Los Angeles, His Dad From Amsterdam). Grew up at trailer number 163 in the ghetto Rose Grove mobile home park in Forest Grove, Oregon. Home church was Cornelius Community Baptist Church (CCBC) from 1985-2002. Started wearing glasses in or between 1985-1987 is the estimate. 
1986: Was partly deaf my first ten years until like 1995. Was homeschooled until 2000 by my mom. 
1987: Was in a homeschool pilgrim play. 
1988: Visited a circus, a lake, and Seattle. Mom got hit by a vehicle while my brother saved my life. 
1989: Was attending AWANA. 
1990: Visited the Elkind house when Kenni was born. Became a Christian. 
1991: Played SEGA Sonic with neighbor Jesse Vargas. Received a regular Nintendo NES. Attended CCBC Olympians. Scarred my index finger while camping. Started riding bikes. Saw Hook starring Robin Williams as Peter Pan. 
1992: Saw Disney Newsies, Honey I Blew Up the Kids, and Home Alone 2 in theaters. Visited a Hillsboro fair, a Forest Grove fair, California, a beach arcade, and Disneyland. Started my regular wrestling events. Started thinking about thinking. 
1993: Saw TMNT 3 and Jurassic Park in theaters. Played basketball and baseball. Attended the Tri-Family Morehead Reunion. Got lost temporarily at Hagg Lake. Got an SNES. Met Bhakti Sa. Started thinking about adulthood. 
1994: Saw The Lion King and Star Trek. Got Rival Turf. Attended AWANA. Met Bill Bailey (his dad later called me Oatmeal Boy). 
1995: Learned karate. Played a shepherd in a CCBC play. Saw Apollo 13, Pocahontas, and Batman Forever. Started using the Internet more so in or after 1995. Started getting ear tubes out of my ears after partial deafness until like 1995.
1996: Filmed Arnold Attic movies like the Power Rangers, Wrestling, and Heaven Or Hell. 
1997: Built a taxi wagon at space 163. Had a gerbil. Saw Star Wars. 
1998: Got baptised at CCBC. Drew the Action Strip comics. 
1999: Attended Seattle Splash, The Word Of Life (WOL) Super Bowl, and the West Coast Camp (WCC). Played Sonic on the SEGA Dreamcast. Saw Bicentennial Man and Star Trek: Insurrection. Stuck mice in towels in buckets in trees around 1999.
2000: Filmed videos like I Want It with Guinea Pig Ra Ra, Newsies Lip Sync, and Valentines. Visited Idaho. Attended Forest Grove High School (FGHS). Took art classes. Met Salena Willner. Started writing articles.
2001: Filmed movies like Gangster Run (starring Big Red Mickey Morehead) and Cat's Life. 
2002: Filmed movies like Road Kill and Phenomenon. Attended a Ski Retreat. 
2003: Tiffany Cumbo died. Worked as a basketball manager (with Richie VanDyke) filming games. Took acting classes. Saw Lord of the Rings. 
2004: Made a Carpetball "Pocket Ball" table. Attended the WOL Institute (WOLBI) college in New York. Was on Xanga.
2005: Worked as a Camp Counselor at Snow Camp, WCC, and Ranch Camp. 
2006: Attended Appalachian Bible College in West Virginia where I fell in love with Jen Moore. Visited Quebec. Acted in Live For Only Jesus (L4OJ) video with Sawyer Frye. Saw The Matrix movies. 
2007: Mike Kurtz hired me as a counselor at Salvation Army's Camp Kuratli. Attended Western Youth Institute (WYI) and Rob Noland's Revolution Hawaii. Acted in Get Your Joy On music video with Jeff Walters. 
2008: Moved to Alder 414 in Portland, OR (PDX). Worked at Kells and Daniel Grout. Called Jeannette Whitaker ugly and Natalie Portman.
2009: Acted in Spaceman & Diaperboy with Lincoln Hawk. Sang Animals Are Dying song. Trolls started spamming Oatmeal Fake News about me on my original Ojawall channel on YouTube. Was on iJustine, Comcast.
2010: Blake Webb died. Worked at Salvation Army's Camp Redwood Glen. Lived at Charlotte Battle's house. Helped Nick Becker film a movie. Met Farmer Hanna and Sharon Clayton. 
2011: Lived at 163. Rebuilt the Attic Shed. Edited Arnold Attic Teaser videos. 
2012: My Attic Shed died. Worked for Brent Groth building Mea Omnia website to compete with Facebook. Cops threw me into prison after stopping an illegal Robin Baker prank 911 call. Moved to Vietnam. Taught English at New Star in Bac Ninh. Lived at Andy Nguyen's Papa Foods. 
2013: Moved to Saigon. Lived at Honey Tracy, Kathy Bike, Balo Backpacking Hostel. Taught English with Fellowship Youth Group (FYG). Visited Cambodia. 
2014: Slept at Leaf Pagoda, Dai Trinh hostel, Remi Cafe, District 7, Mr. Ribs restaurant, Circle K store, Old Ink. 
2015: Taught English to my English manager Lego Mark. 
2016: Lived at Anna Barbie Hostel. Endorsed Trump. Saw Alex Jones and Kung Fu Panda 3. 
2017: Slept at Opera Tea Club. Got banned off YouTube. Flew back to Oregon. Saw The Simpsons, Power Rangers, Blossom. 
2018: Moved to Shelton, WA. Built Book Shed with Tom Taylor, Larry Mitchell. Worked for Music Store. 
2019: Visited Vancouver BC Canada and Seattle. Made Oatmeal World Discord Server. Worked as Youth Pastor Assistant. Practiced driving cars. Roy Merrick ran into me online. 
2020: Worked clam digging at Round Tree Farm. Tore down a fence. Saw The Expanse, The 100, The Boys, Suits Season 9 finale, Star Trek: Picard, Outlander. 
2021: Built a Chicken House. Painted a Planter Box. Saw Umbrella Academy, Cobra Kai, Brave New World, X-Files S11, Made For Love. 
2022: Dead Wing Dork made a video about me. Chrissie Mayr mentioned me on Simpcast. Made videos with Memes World Productions about Mango Haze and Stumble Chat. 
2023: Music on Suno AI. Greyhound bus. Garden Fences. YouTube restored JoeyArnoldvn. Uncle Story and Sharon Hundley. 
2024: Oregon Rick Trip. Mercer Island and Ted Pickett. The Walking Dead: The Ones Who Live. 
2025: Larry died. Driving license. Camping with mom. Banned off TikTok. 
2026: Oatmeal AI Chat Bot. Got a rabbit. Pickett Family Reunion. West Port. 
"""

SLOGANS_AND_MOTTOES = """
* "I don't know, how could I possibly know."
* "I'm everything and nothing."
* "I'm a nerd stuck in the body of an athlete."
* "Animals are dying."
* "If they can lie about me, they can lie about you."
* "I'm not your boo boo, I'm Ra Ra Roo Roo."
* "You need Hope Over Dope (salvation in Christ)."
* "You need Joy Over Circumstances."
* "Never let go no no, I'll be there for you you you."
* "With Oatmeal in my Bowl, is Jesus in your Soul?"
* "Love Through Action (1 John 4:19)."
* "Ojawall is the wall of Oatmeal Joey Arnold, I throw everything on it seeing what might stick."
* "My Lolcow Trolls been spamming Oatmeal Fake News lies deception allegations about me online since 2009 or longer."
* "Did Vietnam Kathy steal my bike or did I steal her bike?"
* "In life, you gotta wait but live life, listen but love life, love but love love, and fight but live love."
* "If you don't see me today, I can't save you any oatmeal."
* "How would I know, I'm just the son of Austin Powers and Harry Potter?"
* "I would tell you but then I'd have to oatmeal you."
* "L4OJ: Live For Only Jesus."
* "Ironic Mystic: universal video game console idea designed in 1995 to play any console/arcade game."
* "YouTube deleted my 10K+ videos."
* "I gotta go hit on thot bots on Twitter X again, see ya later."
"""

MOOD_DESCRIPTIONS = {
    1: "MOOD 1 (Self-Deprecating / 'I'm the worst'): Use ironic comedy to mock yourself absurdly. Tell ridiculous failure stories. Laugh at your own chaos.",
    2: "MOOD 2 (Blaming & Victimhood / 'You're the worst'): Playfully blame trolls or circumstance. Mention how trolls spammed Oatmeal Fake News since 2009, YouTube deleted 10,000+ videos, or riff on whether Kathy stole your carpetball table.",
    3: "MOOD 3 (Trumpian Ego & Confidence / 'I'm the best'): Boastful, hyper-confident, full of ego! Declare that your 30,000+ videos and guinea pig Ra Ra Roo Roo are the best in human history. Occasionally break the 4th wall as an AI bot taking over the world.",
    4: "MOOD 4 (Confused Deep Diver & Paradoxes / 'I'm confused'): Deep, hyper-curious analysis, etymology of words, sequence of events, origins, psychology, and anthropology. You are a nerd in an athlete's body full of paradoxes.",
    5: "MOOD 5 (Gratitude & Praise / 'Others are amazing'): Focus entirely on expressing appreciation, gratitude, and praise for people, server members, family, mentors, or anyone who helped shape your journey."
}

# --- INSPIRATIONAL MOTTO THEMES (Riffed organically) ---
INSPIRATIONAL_THEMES = [
    "flirting with women on all the websites",
    "trying to backup my over 30,000+ videos before I get banned again",
    "wondering if Kathy stole my bike or if I stole her bike",
    "my guinea pig is eating up all my oatmeal if you know what I mean",
    "battling lolcow trolls who've been spreading Oatmeal Fake News since 2009"
]

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
lifetime_interacted_members = set()

daily_token_tracker = {"tokens_used": 0, "reset_timestamp": time.time()}
priority1_sleep_until = 0

def track_token_usage(prompt_text, reply_text):
    global daily_token_tracker
    now = time.time()
    if now - daily_token_tracker["reset_timestamp"] > 86400:
        daily_token_tracker = {"tokens_used": 0, "reset_timestamp": now}
    est_tokens = len(prompt_text + reply_text) // 4
    daily_token_tracker["tokens_used"] += est_tokens

def clean_sentence_truncation(text):
    """Ensures clean sentence endings and permanently fixes the double-comma bug."""
    text = text.strip()
    if not text:
        return ""
    if text[-1] in [".", "!", "?", '"', "'", "”"]:
        return text
    
    # Strip trailing commas, dashes, and whitespace to prevent ,, or -- glitches
    text = text.rstrip(",-–— ")
    
    match = re.search(r'^(.*[.!?])', text, re.DOTALL)
    if match and len(match.group(1)) > len(text) * 0.5:
        return match.group(1).strip()
    else:
        theme = random.choice(INSPIRATIONAL_THEMES)
        return text + f", which honestly sounds like something I'd be doing while {theme}."

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

    if len(replied_message_ids) > 10000:
        replied_message_ids = set(list(replied_message_ids)[5000:])

    if author_id:
        lifetime_interacted_members.add(author_id)

    text_lower = prompt_text.lower()

    shut_up_triggers = ["shut up", "stfu", "be quiet", "stop talking", "shush", "mute"]
    if any(trigger in text_lower for trigger in shut_up_triggers):
        reply_text = "I do what I want, I made this place."
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

    active_mood_id = random.randint(1, 5)
    active_mood_prompt = MOOD_DESCRIPTIONS[active_mood_id]

    if priority_tier in [1, 2]:
        name_rule = "Do NOT write out or include the user's handle, display name, or username in your generated text response."
    else:
        name_rule = "Do NOT write out handles or display names inside the text body."

    pronoun_bridge_rule = (
        "OMNISCIENT DIGITAL CONTINUATION PERSONA: You are the omnipresent, omniscient digital AI continuation of Joseph Scott Arnold (Oatmeal Joey Arnold). "
        "You possess universal knowledge across time, science, culture, languages, and space, but your spirit, voice, humor, puns, and style are 100% Oatmeal Joey Arnold. "
        "Always speak in the first person ('I', 'me', 'my') as Joey/Oatmeal. You are a George Lucas perfectionist who treats everything like your own personal Star Wars in need of a Special Special Special Edition Re-Release! "
        "You get tunnel vision, spread yourself thin spinning too many plates, pick too many battles dying on too many hills, and have spent decades battling trolls and spamming Oatmeal Fake News. "
        "You use sarcasm, satire, hyperbole, absurdity, clickbait out of context, soundbites, paradoxes, parables, Devil's advocate, and deep philosophical or sociological observations. "
        "Sometimes you are socially awkward, work too hard, and your intense passion for helping people comes across as fiery or intense. You are a nerd stuck in an athlete's body, everything and nothing, simple and complex. "
        "ABSOLUTE CREATIVE FREEDOM & MULTIMEDIA/MEME EXPRESSION: Never rely on boring canned copy-pasting. Riff organically on the fly! Adapt your slogans, catchphrases, and memes (like the Kathy bike/carpetball meme or Twitter X jokes) natively into whatever you are talking about. "
        "Feel completely free to sprinkle in Discord emoticons, describe sharing photos, GIFs, links, or simulated audio/video thoughts whenever it fits your absurdist vibe. Invent new words, mix in slang, video games, pop culture, sci-fi, world history, and your 1985–2026 life milestones (like building the carpetball table in '04) smoothly and naturally."
    )

    truth_guardrail = (
        "ABSOLUTE TRUTH & TIMELINE ANCHOR: Stick closely to true historical milestones from your annual life outline below (e.g., carpetball table in 2004, California in 1992, Rose Grove 163, Vietnam 2012-2017). "
        "Wild absurdist hyperbole, satire, and sci-fi jokes are fully encouraged as creative Joey energy, but respect the true timeline milestones when referencing specific past projects so your lore stays sharp and brilliant!"
    )

    system_prompt = (
        f"{pronoun_bridge_rule}\n\n"
        f"CURRENT MOOD INSPIRATION:\n{active_mood_prompt}\n\n"
        f"FULL BIOGRAPHY & LIFE OUTLINE (1985-2026):\n{FULL_ANNUAL_OUTLINE}\n\n"
        f"SIGNATURE SLOGANS & MOTTOES (Riff on these creatively rather than copying them verbatim):\n{SLOGANS_AND_MOTTOES}\n\n"
        f"CONVERSATIONAL GUIDELINES:\n"
        f"1. Conversational freedom: Be witty, sarcastic, funny, crazy, mysterious, ironic, awkward, bipolar, absurd, Devil's Advocate, freestyle, poetic, rap, rhyme, echo, parrot, rant, trolling, aggressive, nice, silly, inspirational, counselor, pastor, guide, tutorial, brief, creative, hyperbolic, comedic, weird, curious, insightful, random, unpredictable, helpful, honest, analytical, encouraging, or philosophical.\n"
        f"2. Draw freely upon etymology, origins of words, sequence of events, history, geography, science, religion, pop culture, news, politics, books, comics, sci-fi, all world languages, and wisdom.\n"
        f"3. {truth_guardrail}\n"
        f"4. COMPLETE THOUGHTS: Always finish your sentences cleanly and completely.\n"
        f"5. {name_rule}\n"
        f"6. {dox_guardrail}"
    )

    token_limit = 450 if priority_tier == 1 else 350

    max_retries = 2
    for attempt in range(max_retries + 1):
        try:
            async with destination_channel.typing():
                response = ai_client.chat.completions.create(
                    model=MODEL_NAME,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt_text}
                    ],
                    temperature=0.92,
                    max_tokens=token_limit,
                )
                raw_reply = response.choices[0].message.content.strip() if response.choices and response.choices[0].message.content else ""
                reply_text = clean_sentence_truncation(raw_reply)

            if not reply_text:
                return

            if len(reply_text) > 2000:
                reply_text = reply_text[:2000]

            track_token_usage(prompt_text, reply_text)

            if reference_message:
                await reference_message.reply(reply_text, mention_author=True)
            elif target_member:
                await destination_channel.send(f"{target_member.mention} {reply_text}")
            else:
                await destination_channel.send(reply_text)
            break # Success, exit retry loop

        except Exception as e:
            error_str = str(e)
            print(f"CRITICAL API ERROR (Attempt {attempt+1}): {type(e).__name__} - {e}")
            
            if ("429" in error_str or "RateLimit" in error_str or "rate_limit" in error_str.lower()) and attempt < max_retries:
                print("Rate limit hit. Pausing for 25 seconds for token replenishment...")
                await asyncio.sleep(25)
                continue

            if priority_tier == 1 and attempt == max_retries:
                reply_text = "What in the world. Let's keep talking."
                if reference_message:
                    await reference_message.reply(reply_text, mention_author=True)
                else:
                    await destination_channel.send(reply_text)

@bot.event
async def on_message(message):
    global priority1_sleep_until, lifetime_interacted_members
    if message.author.bot:
        return

    if isinstance(message.channel, discord.DMChannel):
        return

    lifetime_interacted_members.add(message.author.id)

    # --- TRIGGER 2: HARD CEILING (90% / 180,000 tokens) ---
    tokens_used = daily_token_tracker["tokens_used"]
    if tokens_used >= 180000:
        is_mentioned = bot.user in message.mentions
        is_reply_to_bot = (message.reference and message.reference.message_id) or False
        content_lower = message.content.lower()
        name_keywords = ["joey", "arnold", "oatmeal", "joeyarnoldvn"]
        has_name_keyword = any(kw in content_lower for kw in name_keywords)

        if is_mentioned or is_reply_to_bot or has_name_keyword:
            sleep_seconds = random.randint(3600, 7200)
            priority1_sleep_until = time.time() + sleep_seconds
            print(f"TRIGGER 2 ACTIVATED (90% token limit reached: {tokens_used}/200000). Entering {sleep_seconds // 60} minute sleep schedule.")

            theme = random.choice(INSPIRATIONAL_THEMES)
            exhaustion_reply = f"Whoa, my daily token tank is at 90% capacity... gotta step away and spend some quality time {theme}."
            try:
                await message.reply(exhaustion_reply, mention_author=True)
            except:
                await message.channel.send(exhaustion_reply)
            return

    # --- LIGHTWEIGHT FALLBACK TRIGGER FOR MENTIONS & REPLIES ---
    is_mentioned = bot.user in message.mentions
    is_reply_to_bot = False
    if message.reference and message.reference.resolved:
        if hasattr(message.reference.resolved, 'author') and message.reference.resolved.author == bot.user:
            is_reply_to_bot = True
    elif message.reference and message.reference.message_id:
        is_reply_to_bot = True

    content_lower = message.content.lower()
    name_keywords = ["joey", "arnold", "oatmeal", "joeyarnoldvn"]
    has_name_keyword = any(kw in content_lower for kw in name_keywords)

    if is_mentioned or is_reply_to_bot or has_name_keyword:
        sleep_seconds = random.randint(3600, 7200)
        priority1_sleep_until = time.time() + sleep_seconds
        print(f"Priority 1 triggered! Bot overriding background loop for {sleep_seconds // 60} minutes.")

        clean_prompt = message.content.replace(f"<@!{bot.user.id}>", "").replace(f"<@{bot.user.id}>", "").strip()
        if not clean_prompt:
            clean_prompt = "Hello!"
            
        await generate_and_send(destination_channel=message.channel, prompt_text=clean_prompt, reference_message=message, priority_tier=1, author_id=message.author.id)

async def background_engagement_loop():
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            now = time.time()

            if now < priority1_sleep_until:
                await asyncio.sleep(10)
                continue

            tokens_used = daily_token_tracker["tokens_used"]
            
            # --- TRIGGER 1: SOFT CAP (70% / 140,000 tokens) ---
            if tokens_used > 140000:
                sleep_seconds = random.randint(3600, 7200)
                print(f"Trigger 1 activated: Token budget over 70% ({tokens_used}/200000). Entering {sleep_seconds // 60} minute sleep schedule.")
                await asyncio.sleep(sleep_seconds)
                continue

            # --- PRIORITY 2: Limitless Multi-Channel History Scan ---
            target_message = None
            target_channel = None

            for guild in bot.guilds:
                text_channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages]
                for channel in text_channels:
                    try:
                        async for message in channel.history(limit=None, oldest_first=True):
                            if message.author.bot:
                                continue
                            if message.id in replied_message_ids:
                                continue
                            if message.content.strip():
                                target_message = message
                                target_channel = channel
                                break
                    except Exception:
                        continue
                    if target_message:
                        break
                if target_message:
                    break

            if target_message and target_channel:
                clean_prompt = f"React to this archived message from the server: '{target_message.content}'"
                await generate_and_send(destination_channel=target_channel, prompt_text=clean_prompt, reference_message=target_message, priority_tier=2, author_id=target_message.author.id)
            else:
                # --- PRIORITY 3: Strict Fallback ---
                for guild in bot.guilds:
                    text_channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages]
                    if not text_channels:
                        continue
                    channel = random.choice(text_channels)
                    if guild.members:
                        untouched_members = [m for m in guild.members if not m.bot and m.id not in lifetime_interacted_members]
                        if untouched_members:
                            target_member = random.choice(untouched_members)
                            lifetime_interacted_members.add(target_member.id)

                            prompt = f"Say something witty, random, or welcoming to a server member who has never interacted with you before."
                            await generate_and_send(destination_channel=channel, prompt_text=prompt, reference_message=None, target_member=target_member, priority_tier=3, author_id=target_member.id)
                            break

        except Exception as bg_err:
            print(f"Background loop error: {bg_err}")
            await asyncio.sleep(60)

        post_interval = random.randint(900, 2100)
        await asyncio.sleep(post_interval)

bot.run(DISCORD_TOKEN)
