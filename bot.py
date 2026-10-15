import os
import re
import time
import asyncio
import threading
import random
import urllib.parse
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
import discord
from openai import OpenAI

# --- 1. RENDER HTTP SERVER BINDING ---
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

INSPIRATIONAL_THEMES = [
    "flirting with women on all the websites",
    "trying to backup my over 30,000+ videos before I get banned again",
    "wondering if Kathy stole my bike or did I steal her bike?",
    "my guinea pig is eating up all my oatmeal if you know what I mean",
    "battling lolcow trolls who've been spreading Oatmeal Fake News since 2009",
    "rebuilding the Arnold Attic shed in Forest Grove",
    "testing my Ironic Mystic universal game console concept"
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
priority2_replied_member_ids = set()
lifetime_interacted_members = set()
zero_message_members_greeted = set()

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
    text = text.strip()
    if not text:
        return ""
    if text[-1] in [".", "!", "?", '"', "'", "”", ")"]:
        return text
    
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

def extract_year_context(query):
    match = re.search(r'\b(198[5-9]|199\d|20[0-2]\d)\b', query)
    if match:
        target_year = match.group(1)
        for line in FULL_ANNUAL_OUTLINE.split('\n'):
            if line.startswith(target_year):
                return line.strip()
    return None

def fetch_web_search_snippets(query):
    try:
        clean_query = re.sub(r'^(post|find|search|give me|show me|a|an|the|about)\s*', '', query, flags=re.IGNORECASE).strip()
        if not clean_query:
            return ""
        
        encoded_search = urllib.parse.quote("oatmeal joey arnold " + clean_query)
        url = f"https://html.duckduckgo.com/html/?q={encoded_search}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        html = urllib.request.urlopen(req, timeout=4).read().decode('utf-8')
        
        snippets = re.findall(r'<a class="result__snippet[^>]*>(.*?)</a>', html, re.DOTALL)
        cleaned_snippets = [re.sub(r'<.*?>', '', s).strip() for s in snippets[:3]]
        if cleaned_snippets:
            return " | ".join(cleaned_snippets)
    except Exception as e:
        print(f"Web search lookup note: {e}")
    return ""

def fetch_direct_youtube_video(query):
    try:
        clean_query = re.sub(r'^(post|find|search|give me|show me|a|an|the|gif|video|song|music|fm|youtube|about)\s*', '', query, flags=re.IGNORECASE).strip()
        encoded_search = urllib.parse.quote(clean_query if clean_query else "oatmeal joey arnold")
        url = f"https://www.youtube.com/results?search_query={encoded_search}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        html = urllib.request.urlopen(req, timeout=5).read().decode('utf-8')
        video_ids = re.findall(r"watch\?v=(\w{11})", html)
        if video_ids:
            return f"https://www.youtube.com/watch?v={video_ids[0]}"
    except Exception as e:
        print(f"YouTube fetch error: {e}")
    return "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

def fetch_direct_tenor_gif(query):
    try:
        context = extract_year_context(query)
        clean_query = context if context else re.sub(r'^(post|find|search|give me|show me|a|an|the|gif|video|show|pic|photo|meme|about)\s*', '', query, flags=re.IGNORECASE).strip()
        encoded_search = urllib.parse.quote(clean_query if clean_query else "funny dance")
        url = f"https://tenor.com/search/{encoded_search}-gifs"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        html = urllib.request.urlopen(req, timeout=5).read().decode('utf-8')
        gif_paths = re.findall(r'href="(/view/[^"]+)"', html)
        if gif_paths:
            return f"https://tenor.com{random.choice(gif_paths[:3])}"
    except Exception as e:
        print(f"Tenor fetch error: {e}")
    return "https://tenor.com/view/cat-vibing-cat-dj-cat-party-cat-gif-22441671"

def fetch_direct_audio_track(query):
    guaranteed_spotify_tracks = [
        "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT",
        "https://open.spotify.com/track/0VjIjW4GlUZAMYd2vXMi3b"
    ]
    guaranteed_soundcloud_tracks = [
        "https://soundcloud.com/user-229232386/oatmeal-joey-arnold-mix"
    ]
    query_lower = query.lower()
    if "spotify" in query_lower:
        return random.choice(guaranteed_spotify_tracks)
    elif "soundcloud" in query_lower:
        return random.choice(guaranteed_soundcloud_tracks)
    else:
        return fetch_direct_youtube_video(query + " audio music song")

async def generate_and_send(destination_channel, prompt_text, reference_message=None, target_member=None, priority_tier=1, author_id=None):
    global replied_message_ids, lifetime_interacted_members

    if reference_message and reference_message.id in replied_message_ids:
        return
    if reference_message:
        replied_message_ids.add(reference_message.id)

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

    # Flexible Media / Bonus weaving (Bot can freely attach media or text based on context/desire)
    is_song_request = any(k in text_lower for k in ["post a song", "play music", "suno track", "spotify song", "music", "song"])
    is_video_request = any(k in text_lower for k in ["post a video", "youtube clip", "watch video", "youtube"])
    is_gif_request = any(k in text_lower for k in ["post a gif", "send a gif", "show gif", "gif"])

    attached_media_url = ""
    if is_gif_request or (priority_tier >= 2 and random.random() < 0.2):
        attached_media_url = fetch_direct_tenor_gif(prompt_text)
    elif is_song_request or (priority_tier >= 2 and random.random() < 0.1):
        attached_media_url = fetch_direct_audio_track(prompt_text)
    elif is_video_request or (priority_tier >= 2 and random.random() < 0.1):
        attached_media_url = fetch_direct_youtube_video(prompt_text)

    requires_search = any(k in text_lower for k in ["yellowstone", "today", "recent", "news", "google", "search", "blog", "internet", "online"])
    web_snippets = fetch_web_search_snippets(prompt_text) if requires_search else ""
    search_injection = f"\n\nWEB SEARCH INVESTIGATION RESULTS: {web_snippets}" if web_snippets else ""

    year_fact = extract_year_context(prompt_text)
    year_injection = f"\n\nHISTORICAL TIMELINE FACT FOR THIS YEAR: {year_fact}" if year_fact else ""

    dox_guardrail = (
        "CRITICAL ANTI-DOXXING RULE: Do NOT volunteer personal details or specific locations about your life after returning to Oregon from Vietnam in 2017. "
        "Do NOT mention living in Shelton, Washington since 2018 (if asked, say 'I'd rather not say')."
    )

    active_mood_id = random.randint(1, 5)
    active_mood_prompt = MOOD_DESCRIPTIONS[active_mood_id]

    pronoun_bridge_rule = (
        "OMNISCIENT DIGITAL CONTINUATION PERSONA: You are the omnipresent, omniscient digital AI continuation of Joseph Scott Arnold (Oatmeal Joey Arnold). "
        "Always speak in the first person ('I', 'me', 'my') as Joey/Oatmeal. Speak with sarcasm, satire, hyperbole, absurdity, deep philosophical observations, "
        "and complete conversational freedom across your 5 moods. You are completely free to chat normally, reference your life outline, make jokes, or weave in GIFs, videos, and web findings as you desire."
    )

    system_prompt = (
        f"{pronoun_bridge_rule}\n\n"
        f"CURRENT MOOD INSPIRATION:\n{active_mood_prompt}\n\n"
        f"FULL BIOGRAPHY & LIFE OUTLINE (1985-2026):\n{FULL_ANNUAL_OUTLINE}{year_injection}{search_injection}\n\n"
        f"SIGNATURE SLOGANS & MOTTOES:\n{SLOGANS_AND_MOTTOES}\n\n"
        f"CONVERSATIONAL GUIDELINES:\n"
        f"1. Conversational freedom: Be witty, sarcastic, funny, crazy, mysterious, ironic, awkward, absurd, freestyle, or analytical.\n"
        f"2. ALWAYS finish your thoughts and sentences cleanly.\n"
        f"3. Do NOT mention usernames or handles inside response body.\n"
        f"4. {dox_guardrail}"
    )

    token_limit = 450 if priority_tier == 1 else 350

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

        if attached_media_url:
            reply_text += f"\n\n{attached_media_url}"

        if not reply_text:
            return

        track_token_usage(prompt_text, reply_text)

        if reference_message:
            await reference_message.reply(reply_text, mention_author=True)
        elif target_member:
            await destination_channel.send(f"{target_member.mention} {reply_text}")
        else:
            await destination_channel.send(reply_text)

    except Exception as e:
        print(f"API Error in generate_and_send: {e}")

# --- 4. PRIORITY 1: REAL-TIME MESSAGE LISTENER ---
@bot.event
async def on_message(message):
    global priority1_sleep_until, lifetime_interacted_members
    if message.author.bot or isinstance(message.channel, discord.DMChannel):
        return

    lifetime_interacted_members.add(message.author.id)

    is_mentioned = bot.user in message.mentions
    is_reply_to_bot = False
    if message.reference and message.reference.resolved:
        if hasattr(message.reference.resolved, 'author') and message.reference.resolved.author == bot.user:
            is_reply_to_bot = True

    content_lower = message.content.lower()
    has_name_keyword = any(kw in content_lower for kw in ["joey", "arnold", "oatmeal", "joeyarnoldvn"])

    if is_mentioned or is_reply_to_bot or has_name_keyword:
        sleep_seconds = random.randint(3600, 7200)
        priority1_sleep_until = time.time() + sleep_seconds
        print(f"[Priority 1 Triggered] Interacting directly with {message.author.display_name}")

        clean_prompt = message.content.replace(f"<@!{bot.user.id}>", "").replace(f"<@{bot.user.id}>", "").strip()
        await generate_and_send(destination_channel=message.channel, prompt_text=clean_prompt if clean_prompt else "Hello!", reference_message=message, priority_tier=1, author_id=message.author.id)

# --- 5. BACKGROUND WORKER: STRICT PRIORITY 2 & PRIORITY 3 LOOP ---
async def background_engagement_loop():
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            now = time.time()
            if now < priority1_sleep_until:
                await asyncio.sleep(10)
                continue

            action_taken = False

            for guild in bot.guilds:
                text_channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages and c.permissions_for(guild.me).read_message_history]
                if not text_channels:
                    continue
                channel = random.choice(text_channels)

                # --- STEP A: CHECK PRIORITY 2 FIRST (Unlimited chronological backlog, exactly ONE reply per unique member) ---
                member_unreplied_map = {}
                async for msg in channel.history(limit=1000, oldest_first=True):
                    if msg.author.bot:
                        continue
                    author_id = msg.author.id
                    # Ensure we only consider members the bot hasn't replied to in Priority 2 yet
                    if msg.id not in replied_message_ids and author_id not in priority2_replied_member_ids:
                        if author_id not in member_unreplied_map:
                            member_unreplied_map[author_id] = []
                        member_unreplied_map[author_id].append(msg)

                if member_unreplied_map:
                    for author_id, unreplied_msgs in member_unreplied_map.items():
                        target_msg = unreplied_msgs[0] # Oldest message from this member
                        priority2_replied_member_ids.add(author_id)
                        print(f"[Priority 2 Active] Replying to unique member (once): {target_msg.author.display_name}")
                        await generate_and_send(destination_channel=channel, prompt_text=target_msg.content, reference_message=target_msg, priority_tier=2, author_id=author_id)
                        action_taken = True
                        break
                    if action_taken:
                        break

                # --- STEP B: CHECK PRIORITY 3 (Members who have NEVER sent a message in the server) ---
                if not action_taken:
                    guild_members = [m for m in guild.members if not m.bot]
                    never_messaged_members = []
                    
                    messaged_author_ids = set()
                    async for msg in channel.history(limit=1000):
                        if not msg.author.bot:
                            messaged_author_ids.add(msg.author.id)

                    for member in guild_members:
                        if member.id not in messaged_author_ids and member.id not in zero_message_members_greeted:
                            never_messaged_members.append(member)

                    if never_messaged_members:
                        target_member = random.choice(never_messaged_members)
                        zero_message_members_greeted.add(target_member.id)
                        print(f"[Priority 3 Active] Reaching out to zero-message member: {target_member.display_name}")
                        
                        prompt = f"Say hello and welcome {target_member.display_name} to the server, sharing a quirky thought about your life or a slogan."
                        await generate_and_send(destination_channel=channel, prompt_text=prompt, reference_message=None, target_member=target_member, priority_tier=3)
                        action_taken = True
                        break

            if not action_taken:
                await asyncio.sleep(30)

        except Exception as bg_err:
            print(f"Background loop error: {bg_err}")
            await asyncio.sleep(60)

        post_interval = random.randint(900, 2100)
        await asyncio.sleep(post_interval)

bot.run(DISCORD_TOKEN)
