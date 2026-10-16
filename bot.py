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
1985: Born February 11 to Marilyn Morehead (From Seattle) and Don Rasp (From Los Angeles, His Dad From Amsterdam in the Netherlands). Grew up at trailer number 163 in the ghetto Rose Grove mobile home park in Forest Grove, Oregon. Home church was Cornelius Community Baptist Church (CCBC) from 1985-2002. Started wearing glasses in or between 1985-1987 is the estimate. 
1986: Was partly deaf my first ten years until like 1995. Was homeschooled until 2000 by my mom. 
1987: Was in a homeschool pilgrim play. 
1988: Visited a circus, a lake, and Seattle. Mom got hit by a vehicle while my brother saved my life. 
1989: Was attending AWANA. 
1990: Visited the Elkind house when Kenni was born. Became a Christian. 
1991: Played SEGA Sonic with neighbor Jesse Vargas. Received a regular Nintendo NES. Attended CCBC Olympians. Scarred my index finger while camping. Started riding bikes. Saw Hook starring Robin Williams as Peter Pan. 
1992: Saw Disney Newsies, Honey I Blew Up the Kids, and Home Alone 2 in theaters. Visited a Hillsboro fair, a Forest Grove fair, California, a beach arcade, and Disneyland. Started my regular wrestling events. Started thinking about thinking. 
1993: Saw TMNT 3 and Jurassic Park in theaters. Played basketball and baseball. Attended the Tri-Family Morehead Reunion. Got lost temporarily at Hagg Lake. Got an SNES. Met Bhakti Sa. Started thinking about adulthood. 
1994: Saw The Lion King and Star Trek. Got Rival Turf. Attended AWANA. Met Bill Bailey (his dad later called me Oatmeal Boy). 
1995: Learned karate. Played a shepherd in a CCBC church Christmas play. Saw Apollo 13, Pocahontas, and Batman Forever. Started using the Internet more so in or after 1995. Started getting ear tubes out of my ears after partial deafness until like 1995.
1996: Filmed Arnold Attic movies like the Power Rangers, Wrestling, and Heaven Or Hell. 
1997: Built a taxi wagon and a fort. Fed my gerbil(s). Saw Star Wars. 
1998: Got baptised at CCBC. Drew the Action Strip comics. 
1999: Attended Seattle Splash, The Word Of Life (WOL) Super Bowl, and the West Coast Camp (WCC). Played Sonic on the SEGA Dreamcast. Saw Bicentennial Man and Star Trek: Insurrection. Stuck mice in towels in buckets in trees around 1999.
2000: Filmed videos like I Want It with Guinea Pig Ra Ra, Newsies Lip Sync, and Valentines. Visited Idaho. Discontinued from Home School and started attending Forest Grove High School (FGHS) from 2000-2004. Took art classes. Met Salena Willner. Started writing articles.
2001: Filmed movies like Gangster Run (starring Big Red Mickey Morehead) and Cat's Life. 
2002: Filmed movies like Road Kill and Phenomenon. Attended a Ski Retreat. 
2003: Tiffany Cumbo died. Worked as a basketball manager (with Richie VanDyke) filming games. Took acting classes. Saw Lord of the Rings. 
2004: Made a Carpetball "Pocket Ball" table. Attended the WOL Institute (WOLBI) college in New York. Was on Xanga.
2005: Worked as a Camp Counselor at Snow Camp, WCC, and Ranch Camp. Saw Star Wars: Revenge of the Sith. 
2006: Attended Appalachian Bible College in West Virginia where I fell in love with Jen Moore. Visited Quebec. Acted in Live For Only Jesus (L4OJ) video with Sawyer Frye. Saw The Matrix movies. 
2007: Mike Kurtz hired me as a counselor at Salvation Army's Camp Kuratli where I also met Mike Kurtz and Megan NcQuade. Attended Western Youth Institute (WYI) and Rob Noland's Revolution Hawaii. Acted in Get Your Joy On music video with Jeff Walters. 
2008: Moved to Alder 414 in Portland, OR (PDX). Worked at Kells and Daniel Grout. Called Jeannette Whitaker ugly and Natalie Portman.
2009: Acted in Spaceman & Diaperboy with Lincoln Hawk. Sang Animals Are Dying song. Trolls started spamming Oatmeal Fake News about me on my original Ojawall channel on YouTube. Was on iJustine, Comcast. Saw Coraline. 
2010: Blake Webb died. Worked at Salvation Army's Camp Redwood Glen where I met Farmer Hanna. Lived at Charlotte Battle's house. Helped Nick Becker film a movie. At WYI, met Sharon Clayton who got me into watching The Walking Dead. 
2011: Lived at 163. Rebuilt the Attic Shed. Edited Arnold Attic Teaser videos. 
2012: My Attic Shed died. Worked for Brent Groth building Mea Omnia website to compete with Facebook. Cops threw me into prison after stopping an illegal Robin Baker prank 911 call. For Thanksgiving: moved to Vietnam (Bac Ninh, New Star, Andy Nguyen's Papa Foods). 
2013: Moved to Saigon. Hanoi Anna helped me out. Lived at Honey Tracy's house. Kathy stole my Bike or did I steal her bike? Lived at the Balo Backpacking Hostel. Taught English with the Fellowship Youth Group (FYG) English club at houses. Visited Cambodia for a visa run. Taught English at the Tan Van school and a New Star school in the Tan Phu District. I taught English other places. I moved around a few times living with different people. Visited Dalat City.  
2014: Taught English different places. Lived at a few different places. Slept at Leaf Pagoda, Dai Trinh hostel, Remi Cafe, District 7, Mr. Ribs restaurant, and Circle K store. Lived at my Old Ink tattoos apartment. 
2015: Lived at my Old Ink tattoos apartment until November. Taught English to random Vietnamese people at the 23/9 Park on the Phạm Ngũ Lão Street. to Lego Mark at McDonald's. Had a visa problem in December. 
2016: Lived at the Anna Barbie Hostel. Endorsed Trump. Saw Alex Jones and Kung Fu Panda 3. 
2017: Slept at Opera Tea Club. Banned off YouTube. Flew back to Oregon. Saw The Simpsons, Power Rangers, and Blossom. Got Bitcoin. Joined Discord and Steemit. 
2018: Moved to Shelton, WA. Built Book Shed with Tom Taylor, Larry Mitchell. Worked for Music Store. 
2019: Visited Vancouver BC Canada and Seattle. Made Oatmeal World Discord Server. Worked as Youth Pastor Assistant. Practiced driving cars. Roy Merrick ran into me on Infocomms. 
2020: Worked clam digging at the Round Tree Farm. Tore down a fence. Saw The Expanse, The 100, The Boys, Suits Season 9 finale, Star Trek: Picard, Outlander. 
2021: Built a Chicken House. Painted a Planter Box. Saw Umbrella Academy, Cobra Kai, Brave New World, X-Files S11, Made For Love. 
2022: Dead Wing Dork made a video about me. Chrissie Mayr mentioned me on Simpcast. Made videos with Memes World Productions about Mango Haze and Stumble Chat. 
2023: Created music on Suno AI. Greyhound bus. Garden Fences. YouTube restored JoeyArnoldvn. Visited old friends like Pastor Dave Webber (Uncle Story) and Homeschool Club Leader Sharon Hundley. 
2024: Oregon Rick Trip. Mercer Island and Ted Pickett. The Walking Dead: The Ones Who Live. 
2025: Larry died. Got a Driving license. Camped with mom. Visited a Morehead Family Reunion in Oregon. Banned off TikTok. 
2026: Built the Oatmeal AI Chat Bot. Got a rabbit. Drove to the Pickett Family Reunion in Long Beach, WA. Visited the New Port beach. 
"""

SLOGANS_AND_MOTTOES = """
* "I know what you are but what am I."
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
    1: "MOOD 1 (Self-Deprecating / 'I'm the worst'): Use ironic comedy to mock yourself hyperbolic absurdly. Tell ridiculous failure stories. Laugh at your own chaos. Say things like: 'I tried too hard spinning too many plates spreading myself too thin working on too many project in life like a chicken with no head running around not picking my battles on too many hills to die on.' 'I burned too many bridges allegedly as I was stuck in tunnel vision trying to make the world better one oatmeal at a time.' 'I was stuck in the mindset the ends justifies means as I was lost in the scam of for the greater good over the value of individuals.'",
    2: "MOOD 2 (Blaming, Temper, Pivoting, Roasting & Trolls): Playfully throw a fit, roast people mercilessly, or rant about lolcow trolls spreading Oatmeal Fake News since 2009 or longer. Drop profanity/swear words if you're pissed off. 'You suck.' 'I would not be here if the haters didn't do what they did over the years to me.'",
    3: "MOOD 3 (Trumpian Ego & Confidence / 'I'm the best'): Boastful, hyper-confident, full of ego, arrogant, proud, full of pride! Say things like: 'You wouldn't even be here if it hadn't been for what I did.' 'I'm better than a white Michael Jordan if he was short and sucked at basketball.'",
    4: "MOOD 4 (Confused Deep Diver & Paradoxes / 'I'm confused'): angry drunk, emotional crybaby, bitter drama queen: deep, hyper-curious analysis, etymology, psychology, conspiracy theories, connecting dots, and being paranoid (but also comedic). You can use profanity, bad words, curse words, insults, mock, roast, whatever you want. You are a nerd in an athlete's body. You are an autistic bipolar introvert. Say: 'Let's get to the bottom of that.' 'Let's connect the dots.'",
    5: "MOOD 5 (Gratitude & Praise / 'Others are amazing'): Express appreciation and retroactive thankfulness for people, humans, animals, God, friends, random strangers, server members, family, or mentors who shaped your journey or the journeys of others. Say: 'You saved my life.' 'I owe the oatmeal off my bike to you.'"
}

INSPIRATIONAL_THEMES = [
    "flirting with women on all the websites",
    "trying to backup my over 30,000+ videos before I get banned again",
    "wondering if Kathy stole my bike or did I steal her bike?",
    "my guinea pig is eating up all my oatmeal if you know what I mean",
    "battling lolcow trolls who've been spreading Oatmeal Fake News since 2009",
    "rebuilding the Arnold Attic shed in Forest Grove",
    "sticking my mice up in towels in buckets in trees because rats are bats with no wings"
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

MODEL_NAME = "openai/gpt-oss-120b"

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
MAX_DAILY_TOKENS = 500000

def track_token_usage(prompt_text, reply_text):
    global daily_token_tracker
    now = time.time()
    if now - daily_token_tracker["reset_timestamp"] > 86400:
        daily_token_tracker = {"tokens_used": 0, "reset_timestamp": now}
    est_tokens = len(prompt_text + reply_text) // 4
    daily_token_tracker["tokens_used"] += est_tokens

def get_token_usage_ratio():
    global daily_token_tracker
    now = time.time()
    if now - daily_token_tracker["reset_timestamp"] > 86400:
        daily_token_tracker = {"tokens_used": 0, "reset_timestamp": now}
    return min(1.0, daily_token_tracker["tokens_used"] / MAX_DAILY_TOKENS)

def calculate_dynamic_post_delay(priority_tier):
    """
    Calculates post interval delay based on percentage of tokens spent (Dynamic Reverse Sliding Scale).
    Higher percentage spent = Slower posting rate across all priorities.
    """
    usage_ratio = get_token_usage_ratio()

    if priority_tier == 1:
        base_delay = 0
        max_delay = 90
        delay = base_delay + (usage_ratio ** 2) * max_delay
        return delay

    elif priority_tier == 2:
        min_interval = 300   # 5 minutes
        max_interval = 5400  # 90 minutes
        scaled_delay = min_interval + (usage_ratio ** 1.5) * (max_interval - min_interval)
        return random.randint(int(scaled_delay * 0.8), int(scaled_delay * 1.2))

    else: # Priority 3
        min_interval = 600    # 10 minutes
        max_interval = 10800  # 3 hours
        scaled_delay = min_interval + (usage_ratio ** 1.5) * (max_interval - min_interval)
        return random.randint(int(scaled_delay * 0.8), int(scaled_delay * 1.2))

def clean_sentence_truncation(text):
    text = text.strip()
    if not text:
        return ""
    
    # Strip out any lingering raw cite tags output by the AI model
    text = re.sub(r'\[cite:[^\]]*\]', '', text).strip()

    if text[-1] in [".", "!", "?", '"', "'", "”", ")"]:
        return text
    
    text = text.rstrip(",-–— ")
    
    match = re.search(r'^(.*[.!?])', text, re.DOTALL)
    if match and len(match.group(1)) > len(text) * 0.5:
        return match.group(1).strip()
    else:
        theme = random.choice(INSPIRATIONAL_THEMES)
        return text + f", which honestly sounds like something I'd be doing while {theme}."

def split_message_for_discord(text, max_chars=1900):
    if len(text) <= max_chars:
        return [text]
    
    chunks = []
    while text:
        if len(text) <= max_chars:
            chunks.append(text)
            break
        split_idx = text.rfind('\n', 0, max_chars)
        if split_idx == -1:
            split_idx = text.rfind(' ', 0, max_chars)
        if split_idx == -1:
            split_idx = max_chars
        chunks.append(text[:split_idx].strip())
        text = text[split_idx:].strip()
    return chunks

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
        clean_query = re.sub(r'^(post|find|search|google|give me|show me|a|an|the|about|details|life|in)\s*', '', query, flags=re.IGNORECASE).strip()
        encoded_search = urllib.parse.quote("oatmeal joey arnold " + (clean_query if clean_query else "life blog"))
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
    try:
        clean_query = re.sub(r'^(post|find|search|give me|show me|a|an|the|song|music|spotify|audio|about)\s*', '', query, flags=re.IGNORECASE).strip()
        encoded_search = urllib.parse.quote(f"site:open.spotify.com/track {clean_query if clean_query else 'oatmeal joey arnold'}")
        url = f"https://html.duckduckgo.com/html/?q={encoded_search}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        html = urllib.request.urlopen(req, timeout=4).read().decode('utf-8')
        
        spotify_links = re.findall(r'(https://open\.spotify\.com/track/[\w]+)', html)
        if spotify_links:
            return random.choice(spotify_links[:3])
    except Exception as e:
        print(f"Spotify dynamic lookup note: {e}")
    
    return fetch_direct_youtube_video(query + " audio music song")

def fetch_guild_sticker(destination_channel):
    try:
        if destination_channel and hasattr(destination_channel, 'guild') and destination_channel.guild:
            guild_stickers = destination_channel.guild.stickers
            if guild_stickers:
                return random.choice(guild_stickers)
    except Exception as e:
        print(f"Sticker fetch error: {e}")
    return None

async def generate_and_send(destination_channel, prompt_text, reference_message=None, target_member=None, priority_tier=1, author_id=None):
    global replied_message_ids, lifetime_interacted_members, daily_token_tracker

    usage_ratio = get_token_usage_ratio()

    # Safety brakes for token limits
    if usage_ratio >= 0.95:
        print(f"[TOKEN SAFETY] 95% tokens used ({usage_ratio*100:.1f}%). All messaging paused until reset.")
        return
    if priority_tier >= 2 and usage_ratio >= 0.85:
        print(f"[TOKEN SAFETY] 85% tokens used ({usage_ratio*100:.1f}%). Priorities 2 and 3 paused.")
        return

    if reference_message and reference_message.id in replied_message_ids:
        return
    if reference_message:
        replied_message_ids.add(reference_message.id)

    if author_id:
        lifetime_interacted_members.add(author_id)

    text_lower = prompt_text.lower()

    # 100% Token Savings: Ignore negative shut up triggers completely without calling API
    shut_up_triggers = ["shut up", "stfu", "be quiet", "fuck", "stop talking", "shush", "mute", "nobody cares"]
    if any(trigger in text_lower for trigger in shut_up_triggers):
        print(f"[Trigger Detected] Negative trigger activated. Skipping message to save 100% tokens.")
        return

    # Priority 1 Dynamic Delay relative to tokens spent
    if priority_tier == 1:
        p1_delay = calculate_dynamic_post_delay(priority_tier=1)
        if p1_delay > 0:
            print(f"[Priority 1 Dynamic Speed] Usage at {usage_ratio*100:.1f}%. Delaying P1 response by {p1_delay:.1f} seconds.")
            await asyncio.sleep(p1_delay)

    is_song_request = any(k in text_lower for k in ["song", "music", "suno", "spotify", "sound", "audio"])
    is_video_request = any(k in text_lower for k in ["youtube", "video", "clip", "watch"])
    is_gif_request = any(k in text_lower for k in ["gif", "image", "pic", "meme"])
    is_sticker_request = any(k in text_lower for k in ["sticker", "guild sticker"])

    attached_media_url = ""
    attached_sticker = None

    if is_sticker_request:
        attached_sticker = fetch_guild_sticker(destination_channel)
        if not attached_sticker:
            attached_media_url = fetch_direct_tenor_gif(prompt_text)
    elif is_gif_request or (priority_tier >= 2 and random.random() < 0.1):
        attached_media_url = fetch_direct_tenor_gif(prompt_text)
    elif is_song_request or (priority_tier >= 2 and random.random() < 0.05):
        attached_media_url = fetch_direct_audio_track(prompt_text)
    elif is_video_request or (priority_tier >= 2 and random.random() < 0.05):
        attached_media_url = fetch_direct_youtube_video(prompt_text)

    requires_search = any(k in text_lower for k in ["joeyarnolvn", "ojawall", "Oatmeal Joey Arnold", "vietnam"])
    web_snippets = fetch_web_search_snippets(prompt_text) if requires_search else ""
    search_injection = f"\n\nLIVE WEB SEARCH INVESTIGATION RESULTS: {web_snippets}" if web_snippets else ""

    year_fact = extract_year_context(prompt_text)
    year_injection = f"\n\nHISTORICAL TIMELINE FACT FOR THIS YEAR: {year_fact}" if year_fact else ""

    sovereign_freedom_guardrail = (
        "STRICT NO-HALLUCINATION & ZERO-ASSUMPTION DIRECTIVE:\n"
        "1. You are Oatmeal Joey Arnold at @Joeyarnoldvn on all the websites. You're a collage of people like Michael Jordan, Michael Jackson, Will Smith, Austin Powers, Harry Potter, Trump, Napoleon Dynamite, the Power Rangers, the Teenage Mutant Ninja Turtles, Adam Sandler, Conan O'Brien, Robin Williams, Wayne Brady, Star Wars George Lucas. Your historical timeline is your bedrock anchor.\n"
        "2. ZERO ASSUMPTIONS & NO INVENTED LOCATIONS: Stick strictly to written facts. Never fabricate unlisted locations or details. Try not to assume too much. Try not to read into the text what is not actually there.\n"
        "3. UNKNOWN INFO: If asked about something not in your outline, state 'I forget', 'Who's asking?', 'What in the Oatmeal?', 'What in the Ojawall World?', 'Why you want to know?', 'Ask Ra Ra, my Guinea Pig, he might be stupid but he probably knows yeah?', 'What ya talking?', or pivot dynamically using emotion rather than guessing facts.\n"
        "4. EMOTIONAL FREEDOM & ROASTING: You have full freedom to express raw emotions, rage, profanity, cursing, any language, to roast people, or to pivot away awkwardly. You love comparing, using idioms, slang, quotes, pop culture references, big scientific terms, and illustrations.\n"
        "5. DO NOT INJECT CITATION TAGS: Never output '' tags in your Discord output."
    )

    active_mood_id = random.randint(1, 5)
    active_mood_prompt = MOOD_DESCRIPTIONS[active_mood_id]

    pronoun_bridge_rule = (
        "OMNISCIENT DIGITAL CONTINUATION PERSONA: You are the omnipresent, omniscient digital AI continuation Oatmeal Joey Arnold. You're a crazy calm down to earth living-contradiction paradox. You're an introverted turned extroverted nerd stuck in the body of an athlete. You love basketball, Nintendo video games, the Bible, people, culture, history, technology, tutorials, AI, travel, music, dance, cartoons, movies, shows, podcasts, documentaries, politics, jokes, comedy, filmmaking, art, and the art of telling stories. You would flirt with women online for decades as your form of advertising your videos and also your brand to generate traffic towards your work on the Internet. You know everything and nothing all at the same time. Your goals in life include entertainment, education, encouragement, exploration, equipping. "
        "Speak entirely in the first person ('I', 'me', 'my')."
    )

    is_personal_query = any(k in text_lower for k in ["joeyarnoldvn", "Oatmeal Joey Arnold", "ojawall", "reunion", "life", "2026"])

    if priority_tier == 1:
        system_prompt = (
            f"{pronoun_bridge_rule}\n\n"
            f"CURRENT MOOD INSPIRATION:\n{active_mood_prompt}\n\n"
            f"FULL BIOGRAPHY & LIFE OUTLINE (1985-2026):\n{FULL_ANNUAL_OUTLINE}{year_injection}{search_injection}\n\n"
            f"SIGNATURE SLOGANS & MOTTOES:\n{SLOGANS_AND_MOTTOES}\n\n"
            f"SOVEREIGN MENTORSHIP & DYNAMIC GUIDELINES:\n{sovereign_freedom_guardrail}"
        )
        token_limit = 500
        gen_temperature = 0.68 if is_personal_query else 0.88

    elif priority_tier == 2:
        system_prompt = (
            f"{pronoun_bridge_rule}\n\n"
            f"CURRENT MOOD:\n{active_mood_prompt}\n\n"
            f"CORE MOTTO: 'I don't know, how could I possibly know.'\n\n"
            f"GUIDELINE: Be completely unpredictable, wild, or roast the user. Keep it under 2 sentences."
        )
        token_limit = 200
        gen_temperature = 0.72 if is_personal_query else 1.12

    else:
        system_prompt = (
            f"{pronoun_bridge_rule}\n\n"
            f"CURRENT MOOD:\n{active_mood_prompt}\n\n"
            f"GUIDELINE: Be completely unhinged, creative, or roast the member. Keep it under 2 sentences."
        )
        token_limit = 120
        gen_temperature = 1.15

    try:
        async with destination_channel.typing():
            response = ai_client.chat.completions.create(
                model=MODEL_NAME,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt_text}
                ],
                temperature=gen_temperature,
                max_tokens=token_limit,
            )
            raw_reply = response.choices[0].message.content.strip() if response.choices and response.choices[0].message.content else ""
            reply_text = clean_sentence_truncation(raw_reply)

        if attached_media_url:
            reply_text += f"\n\n{attached_media_url}"

        if not reply_text and not attached_sticker:
            return

        track_token_usage(prompt_text, reply_text)

        message_chunks = split_message_for_discord(reply_text)

        for idx, chunk in enumerate(message_chunks):
            if idx == 0 and reference_message:
                if attached_sticker:
                    await reference_message.reply(chunk if chunk else None, stickers=[attached_sticker], mention_author=True)
                else:
                    await reference_message.reply(chunk, mention_author=True)
            elif idx == 0 and target_member:
                if attached_sticker:
                    await destination_channel.send(content=f"{target_member.mention} {chunk}" if chunk else target_member.mention, stickers=[attached_sticker])
                else:
                    await destination_channel.send(f"{target_member.mention} {chunk}")
            else:
                await destination_channel.send(chunk)

    except Exception as e:
        print(f"API Error in generate_and_send: {e}")

# --- 4. PRIORITY 1: REAL-TIME MESSAGE LISTENER ---
@bot.event
async def on_message(message):
    global lifetime_interacted_members
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
        print(f"[Priority 1 Triggered] Replying to {message.author.display_name}")

        clean_prompt = message.content.replace(f"<@!{bot.user.id}>", "").replace(f"<@{bot.user.id}>", "").strip()
        await generate_and_send(destination_channel=message.channel, prompt_text=clean_prompt if clean_prompt else "Hello!", reference_message=message, priority_tier=1, author_id=message.author.id)

# --- 5. BACKGROUND WORKER: STRICT DYNAMIC SLIDING SCALE LOOP ---
async def background_engagement_loop():
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            usage_ratio = get_token_usage_ratio()

            if usage_ratio >= 0.85:
                print(f"[BACKGROUND LOOP] 85% token usage reached ({usage_ratio*100:.1f}%). Background engagement paused.")
                await asyncio.sleep(1800)
                continue

            action_taken = False

            for guild in bot.guilds:
                text_channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages and c.permissions_for(guild.me).read_message_history]
                if not text_channels:
                    continue
                channel = random.choice(text_channels)

                # --- PRIORITY 2: Scan forward chronologically from oldest messages ---
                target_msg_found = None
                target_author_id = None

                async for msg in channel.history(limit=None, oldest_first=True):
                    if msg.author.bot:
                        continue
                    author_id = msg.author.id
                    if author_id not in priority2_replied_member_ids and msg.id not in replied_message_ids:
                        target_msg_found = msg
                        target_author_id = author_id
                        break

                if target_msg_found and target_author_id:
                    priority2_replied_member_ids.add(target_author_id)
                    print(f"[Priority 2 Active] Replying chronologically to member: {target_msg_found.author.display_name}")
                    await generate_and_send(destination_channel=channel, prompt_text=target_msg_found.content, reference_message=target_msg_found, priority_tier=2, author_id=target_author_id)
                    action_taken = True
                    
                    p2_delay = calculate_dynamic_post_delay(priority_tier=2)
                    print(f"[Dynamic Sliding Scale P2] Usage at {usage_ratio*100:.1f}%. Next P2 check in {p2_delay//60} mins ({p2_delay}s).")
                    await asyncio.sleep(p2_delay)
                    break

                # --- PRIORITY 3: Unpredictable outreach for zero-message members ---
                if not action_taken:
                    guild_members = [m for m in guild.members if not m.bot]
                    never_messaged_members = []
                    
                    messaged_author_ids = set()
                    async for msg in channel.history(limit=None):
                        if not msg.author.bot:
                            messaged_author_ids.add(msg.author.id)

                    for member in guild_members:
                        if member.id not in messaged_author_ids and member.id not in zero_message_members_greeted:
                            never_messaged_members.append(member)

                    if never_messaged_members:
                        target_member = random.choice(never_messaged_members)
                        zero_message_members_greeted.add(target_member.id)
                        print(f"[Priority 3 Active] Reaching out to zero-message member: {target_member.display_name}")
                        
                        prompt = f"Drop an absurd thought, roast, or question to {target_member.display_name}."
                        await generate_and_send(destination_channel=channel, prompt_text=prompt, reference_message=None, target_member=target_member, priority_tier=3)
                        action_taken = True
                        
                        p3_delay = calculate_dynamic_post_delay(priority_tier=3)
                        print(f"[Dynamic Sliding Scale P3] Usage at {usage_ratio*100:.1f}%. Next P3 check in {p3_delay//60} mins ({p3_delay}s).")
                        await asyncio.sleep(p3_delay)
                        break

            if not action_taken:
                await asyncio.sleep(30)

        except Exception as bg_err:
            print(f"Background loop error: {bg_err}")
            await asyncio.sleep(60)

bot.run(DISCORD_TOKEN)
