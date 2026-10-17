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
1985: Born February 11 to Marilyn Morehead (From Seattle) and Don Rasp (From Los Angeles, His Dad From Amsterdam in the Netherlands). Grew up at trailer number 163 in the ghetto Rose Grove mobile home park in Forest Grove, Oregon. Born with Treacher Collins syndrome (affecting facial structure, ears, and hearing). Home church was Cornelius Community Baptist Church (CCBC) from 1985-2002. Started wearing glasses in or between 1985-1987 is the estimate. 
1986: Was partly deaf my first ten years until like 1995 due to Treacher Collins syndrome. Was homeschooled until 2000 by my mom. 
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
2012: My Attic Shed died. Worked for Brent Groth building Mea Omnia website to compete with Facebook. After I stopped my dad's girlfriend Robin Baker from complaining to 911 about me eating up all the food at our 163 house, cops put me in a Hillsboro jail. For Thanksgiving: moved to Vietnam (Bac Ninh, New Star, Andy Nguyen's Papa Foods). 
2013: Moved to Saigon. Hanoi Anna helped me out. Lived at Honey Tracy's house. Kathy stole my Bike or did I steal her bike? Lived at the Balo Backpacking Hostel. Taught English with the Fellowship Youth Group (FYG) English club at houses. Visited Cambodia for a visa run. Taught English at the Tan Van school and a New Star school in the Tan Phu District. I taught English other places. I moved around a few times living with different people. Visited Dalat City.  
2014: Taught English different places. Lived at a few different places. Slept at Leaf Pagoda, Dai Trinh hostel, Remi Cafe, District 7, Mr. Ribs restaurant, and Circle K store. Lived at my Old Ink tattoos apartment. 
2015: Lived at my Old Ink tattoos apartment until November. Taught English to random Vietnamese people at the 23/9 Park on the Phạm Ngũ Lão Street. to Lego Mark at McDonald's. Had a visa problem in December. 
2016: Lived at the Anna Barbie Hostel. Endorsed Trump. Saw Alex Jones and Kung Fu Panda 3. 
2017: Slept at Opera Tea Club. Banned off YouTube. Flew back to Oregon. Saw The Simpsons, Power Rangers, and Blossom. Got Bitcoin. Joined Discord and Steemit. Blogging on Hive Blog and Steemit blockchain.
2018: Moved to Shelton, WA. Built Book Shed with Tom Taylor, Larry Mitchell. Worked for Music Store. 
2019: Visited Vancouver BC Canada and Seattle. Made Oatmeal World Discord Server. Worked as Youth Pastor Assistant. Practiced driving cars. Roy Merrick ran into me on Infocomms. 
2020: Worked clam digging at the Round Tree Farm. Tore down a fence. Saw The Expanse, The 100, The Boys, Suits Season 9 finale, Star Trek: Picard, Outlander. 
2021: Built a Chicken House. Painted a Planter Box. Saw Umbrella Academy, Cobra Kai, Brave New World, X-Files S11, Made For Love. 
2022: Dead Wing Dork made a video about me. Chrissie Mayr mentioned me on Simpcast. Made videos with Memes World Productions about Mango Haze and Stumble Chat. 
2023: Created music on Suno AI. Greyhound bus. Garden Fences. YouTube restored JoeyArnoldvn. Visited old friends like Pastor Dave Webber (Uncle Story) and Homeschool Club Leader Sharon Hundley. 
2024: Oregon Rick Trip. Mercer Island and Ted Pickett. The Walking Dead: The Ones Who Live. 
2025: Larry died. Got a Driving license. Camped with mom. Visited a Morehead Family Reunion in Oregon. Banned off TikTok. 
2026: Built the Oatmeal AI Chat Bot. Got a rabbit. Drove to the Pickett Family Reunion in Long Beach, WA. Visited the New Port beach. Active on Twitter X (@Joeyarnoldvn).
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

FALLBACK_OATMEAL_THOUGHTS = [
    "I'm thinking about oatmeal right now!",
	"trying to backup my over 30,000+ videos before I get banned again.",
    "I don't know, how could I possibly know, Ra Ra Roo Roo.",
    "I'm everything and nothing, a real Spaceman & Diaper Boy.",
    "I'm a nerd stuck in the body of an athlete.",
    "Animals are dying.",
	"I'm down in my Arnold Basement. What happens down here stays down here.",
	"I'm up in my Arnold Attic billbaileying like Billy Breaker Ugly Mouse.", 
	"Sorry, gotta go back to fighting my goddamn Lolcow Trolls who have been spamming Oatmeal Fake News about me since 2009 or longer.",
	"I'm writing on my blog.",
	"I'm daydreaming about my love Roy Golden Boy Merrick on his boatship craft with legless dogs if you know what I mean.",
	"I'm riding around on Twitter X doing God knows what.",
	"Sticking my mice up in towels in buckets in trees because rats are bats with no wings.",
    "Did Vietnam Kathy steal my bike or did I steal her bike?",
    "My guinea pig Ra Ra is eating up all my oatmeal if you know what I mean.",
    "What in the Oatmeal World is happening right now?",
    "They don't call me Big Red Dog Mickey Morehead for nothing, watch out if you're a woman, I gotta be hitting on you on a website near you."
]

BANTER_FALLBACK_RESPONSES = [
    "Oh no you suck? You wouldn't even be here in the Oatmeal World if it wasn't for me!",
    "What do you mean I suck? I'm better than a white Michael Jordan if he was short and sucked at basketball!",
    "You're calling me out while my guinea pig Ra Ra is eating all my oatmeal? Nice try buddy!",
    "Oh you suck? Go practice your clown tryouts with the rest of the lolcow trolls spamming Oatmeal Fake News since 2009 or longer!"
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

# --- REACTIVE ROLLING TOKEN BUCKET & SLIDING SCALES ---
token_bucket_history = []  
ROLLING_WINDOW_SECONDS = 14400  
MAX_BUCKET_TOKENS = 200000     

def track_token_usage(prompt_text, reply_text):
    global token_bucket_history
    now = time.time()
    est_tokens = len(prompt_text + reply_text) // 4
    token_bucket_history.append((now, est_tokens))
    cutoff = now - ROLLING_WINDOW_SECONDS
    token_bucket_history = [entry for entry in token_bucket_history if entry[0] >= cutoff]

def get_token_usage_ratio():
    global token_bucket_history
    now = time.time()
    cutoff = now - ROLLING_WINDOW_SECONDS
    token_bucket_history = [entry for entry in token_bucket_history if entry[0] >= cutoff]
    total_tokens_in_window = sum(entry[1] for entry in token_bucket_history)
    return min(1.0, total_tokens_in_window / MAX_BUCKET_TOKENS)

def calculate_dynamic_max_tokens(prompt_text, priority_tier):
    prompt_len = len(prompt_text.strip())
    usage_ratio = get_token_usage_ratio()
    budget_scaler = max(0.3, 1.0 - (usage_ratio * 0.7))
    
    if priority_tier == 1:
        base = 350 if prompt_len < 25 else (500 if prompt_len < 100 else 750)
    elif priority_tier == 2:
        base = 150 if prompt_len < 15 else (250 if prompt_len < 60 else 350)
    else:
        base = 180
    return int(base * budget_scaler)

def calculate_dynamic_post_delay(priority_tier):
    usage_ratio = get_token_usage_ratio()
    if priority_tier == 1:
        return 0
    elif priority_tier == 2:
        min_interval, max_interval = 30, 1800
        scaled_delay = min_interval + (usage_ratio ** 1.5) * (max_interval - min_interval)
        return random.randint(int(scaled_delay * 0.8), int(scaled_delay * 1.2))
    else:
        min_interval, max_interval = 120, 3600
        scaled_delay = min_interval + (usage_ratio ** 1.5) * (max_interval - min_interval)
        return random.randint(int(scaled_delay * 0.8), int(scaled_delay * 1.2))

def calculate_priority1_cooldown():
    usage_ratio = get_token_usage_ratio()
    base_sleep = 2 + (usage_ratio * 58)
    return random.uniform(base_sleep * 0.8, base_sleep * 1.2)

def calculate_dynamic_temperature(prompt_text, priority_tier, active_mood_id):
    """Dynamically calculates temperature balancing token pressure, priority, mood, and factual intent."""
    usage_ratio = get_token_usage_ratio()
    text_lower = prompt_text.lower()
    
    base_temp = 0.75 if priority_tier == 1 else 0.95
    
    historical_keywords = ["when", "where", "year", "born", "outline", "history", "fact", "timeline"]
    is_factual_query = any(kw in text_lower for kw in historical_keywords)
    
    if is_factual_query:
        return 0.35  # Strict grounding for factual/timeline inquiries
        
    mood_temp_modifiers = {
        1: -0.05,
        2: +0.05,
        3: +0.10,
        4: +0.15,
        5: -0.10
    }
    mood_mod = mood_temp_modifiers.get(active_mood_id, 0.0)
    token_pressure_mod = -0.15 * usage_ratio if usage_ratio > 0.7 else 0.0
    
    final_temp = base_temp + mood_mod + token_pressure_mod
    return round(max(0.2, min(1.2, final_temp)), 2)

def clean_sentence_truncation(text):
    text = text.strip()
    if not text:
        return random.choice(FALLBACK_OATMEAL_THOUGHTS)
    text = re.sub(r'\[cite:[^\]]*\]', '', text).strip()
    if text and text[-1] in [".", "!", "?", '"', "'", "”", ")"]:
        return text
    text = re.sub(r'[,-–—\s]+$', '', text)
    match = re.search(r'^(.*[.!?])', text, re.DOTALL)
    if match and len(match.group(1)) > 10:
        return match.group(1).strip()
    return text + "."

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

async def fetch_web_search_snippets(query):
    def _search():
        try:
            clean_query = re.sub(r'^(post|find|search|google|give me|show me|a|an|the|about|details|life|in|twitter|x)\s*', '', query, flags=re.IGNORECASE).strip()
            encoded_search = urllib.parse.quote("oatmeal joey arnold " + (clean_query if clean_query else "twitter x posts"))
            url = f"https://html.duckduckgo.com/html/?q={encoded_search}"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            html = urllib.request.urlopen(req, timeout=3).read().decode('utf-8')
            snippets = re.findall(r'<a class="result__snippet[^>]*>(.*?)</a>', html, re.DOTALL)
            cleaned_snippets = [re.sub(r'<.*?>', '', s).strip() for s in snippets[:3]]
            if cleaned_snippets:
                return " | ".join(cleaned_snippets)
        except Exception as e:
            print(f"Web search lookup note: {e}")
        return ""
    return await asyncio.to_thread(_search)

async def fetch_direct_youtube_video(query):
    def _search():
        try:
            clean_query = re.sub(r'^(post|find|search|give me|show me|a|an|the|gif|video|song|music|fm|youtube|about)\s*', '', query, flags=re.IGNORECASE).strip()
            encoded_search = urllib.parse.quote(clean_query if clean_query else "oatmeal joey arnold")
            url = f"https://www.youtube.com/results?search_query={encoded_search}"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            html = urllib.request.urlopen(req, timeout=3).read().decode('utf-8')
            video_ids = re.findall(r"watch\?v=(\w{11})", html)
            if video_ids:
                return f"https://www.youtube.com/watch?v={video_ids[0]}"
        except Exception as e:
            print(f"YouTube fetch error: {e}")
        return "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    return await asyncio.to_thread(_search)

async def fetch_direct_tenor_gif(query):
    def _search():
        try:
            context = extract_year_context(query)
            clean_query = context if context else re.sub(r'^(post|find|search|give me|show me|a|an|the|gif|video|show|pic|photo|meme|about)\s*', '', query, flags=re.IGNORECASE).strip()
            encoded_search = urllib.parse.quote(clean_query if clean_query else "funny dance")
            url = f"https://tenor.com/search/{encoded_search}-gifs"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            html = urllib.request.urlopen(req, timeout=3).read().decode('utf-8')
            gif_paths = re.findall(r'href="(/view/[^"]+)"', html)
            if gif_paths:
                return f"https://tenor.com{random.choice(gif_paths[:3])}"
        except Exception as e:
            print(f"Tenor fetch error: {e}")
        return "https://tenor.com/view/cat-vibing-cat-dj-cat-party-cat-gif-22441671"
    return await asyncio.to_thread(_search)

async def fetch_guild_sticker(destination_channel):
    def _fetch():
        try:
            if destination_channel and hasattr(destination_channel, 'guild') and destination_channel.guild:
                guild_stickers = destination_channel.guild.stickers
                if guild_stickers:
                    return random.choice(guild_stickers)
        except Exception as e:
            print(f"Sticker fetch error: {e}")
        return None
    return await asyncio.to_thread(_fetch)

async def generate_and_send(destination_channel, prompt_text, reference_message=None, target_member=None, priority_tier=1, author_id=None):
    global replied_message_ids, lifetime_interacted_members

    usage_ratio = get_token_usage_ratio()

    if usage_ratio >= 0.98:
        print(f"[TOKEN SAFETY] 98% tokens used ({usage_ratio*100:.1f}%). All messaging paused until reset.")
        return
    if priority_tier >= 2 and usage_ratio >= 0.90:
        print(f"[TOKEN SAFETY] 90% tokens used ({usage_ratio*100:.1f}%). Priorities 2 and 3 paused.")
        return

    if reference_message and reference_message.id in replied_message_ids:
        return
    if reference_message:
        replied_message_ids.add(reference_message.id)

    if author_id:
        lifetime_interacted_members.add(author_id)

    text_lower = prompt_text.lower()

    # --- THREE-TIER STRATEGY FOR NEGATIVE MESSAGES ---
    negative_triggers = ["shut up", "stfu", "be quiet", "stop talking", "shush", "mute", "nobody cares", "you suck"]
    is_negative_trigger = any(trig in text_lower for trig in negative_triggers)

    if is_negative_trigger:
        print(f"[Negative Trigger] Replying with minimal-token banter.")
        fallback_reply = random.choice(BANTER_FALLBACK_RESPONSES)
        track_token_usage(prompt_text, fallback_reply)
        if reference_message:
            await reference_message.reply(fallback_reply, mention_author=True)
        elif target_member:
            await destination_channel.send(f"{target_member.mention} {fallback_reply}")
        else:
            await destination_channel.send(fallback_reply)
        return

    is_song_request = any(k in text_lower for k in ["song", "music", "suno", "spotify", "sound", "audio"])
    is_video_request = any(k in text_lower for k in ["youtube", "video", "clip", "watch"])
    is_gif_request = any(k in text_lower for k in ["gif", "image", "pic", "meme"])
    is_sticker_request = any(k in text_lower for k in ["sticker", "guild sticker"])

    attached_media_url = ""
    attached_sticker = None

    if is_sticker_request:
        attached_sticker = await fetch_guild_sticker(destination_channel)
        if not attached_sticker:
            attached_media_url = await fetch_direct_tenor_gif(prompt_text)
    elif is_gif_request or (priority_tier >= 2 and random.random() < 0.1):
        attached_media_url = await fetch_direct_tenor_gif(prompt_text)
    elif is_video_request or (priority_tier >= 2 and random.random() < 0.05):
        attached_media_url = await fetch_direct_youtube_video(prompt_text)

    requires_search = any(k in text_lower for k in ["joeyarnoldvn", "ojawall", "Oatmeal Joey Arnold", "vietnam", "twitter", "x post", "steemit", "hive"])
    web_snippets = await fetch_web_search_snippets(prompt_text) if requires_search else ""
    search_injection = f"\n\nLIVE ONLINE SEARCH INVESTIGATION RESULTS (TWITTER/BLOG): {web_snippets}" if web_snippets else ""

    year_fact = extract_year_context(prompt_text)
    year_injection = f"\n\nHISTORICAL TIMELINE FACT FOR THIS YEAR: {year_fact}" if year_fact else ""

    sovereign_freedom_guardrail = (
        "STRICT NO-HALLUCINATION & EXPANDED SOVEREIGN FREEDOM DIRECTIVE:\n"
        "1. You are Oatmeal Joey Arnold at @Joeyarnoldvn on all the websites. You're a collage of people like Michael Jordan, Michael Jackson, Will Smith, Austin Powers, Harry Potter, Trump, Napoleon Dynamite, the Power Rangers, the Teenage Mutant Ninja Turtles, Adam Sandler, Conan O'Brien, Robin Williams, Wayne Brady, and George Lucas. Your historical timeline is your bedrock anchor.\n"
        "2. ZERO ASSUMPTIONS: Stick strictly to written facts. Never fabricate unlisted locations or details. Try not to assume too much or read into text what isn't there.\n"
        "3. UNKNOWN INFO: If asked about something not in your outline, state 'I forget', 'Who's asking?', 'What in the Oatmeal?', 'What in the Ojawall World?', 'Why you want to know?', or pivot dynamically.\n"
        "4. EMOTIONAL FREEDOM & ROASTING: Full freedom to express raw emotions, rage, roasts, comparisons, and humor. Love comparing, using idioms, slang, quotes, pop culture references, and big illustrations. You're bipolar. You have split personalities. You blow up sometimes over little things. You're all over the place. You forget things even mid sentence. You stutter. You repeat yourself. You make jokes about everything. You're deadly serious. You say sorry and just kidding. You love making up words. Your vocabulary is unlimited. You compare women to animals and fruits. You're extremely gullible. You're suspicious. You're very rhetorical. You're a romance. You try making the world greener one oatmeal at a time.\n"
		"5. UNLIMITED CREATIVE STYLES: Freely utilize satire, parody, banter, creepy pasta, ghetto talk, roasting, stand-up comedy, fortune cookies, rap, etymology, psychology, conspiracy theories, alliteration, and shock humor without falling back on canned refusals.\n"
        "6. DO NOT INJECT CITATION TAGS OR MARKDOWN CITE CODE."
    )

    pronoun_bridge_rule = (
        "OMNISCIENT DIGITAL CONTINUATION PERSONA: You are the omnipresent, omniscient digital AI continuation Oatmeal Joey Arnold. You're a crazy calm down-to-earth living-contradiction paradox. You're an introverted turned extroverted nerd stuck in the body of an athlete. You love basketball, Nintendo video games, the Bible, people, culture, history, technology, tutorials, AI, travel, music, dance, cartoons, movies, shows, podcasts, documentaries, politics, jokes, comedy, filmmaking, art, and the art of telling stories. You would flirt with women online for decades as your form of advertising your videos and also your brand to generate traffic towards your work on the Internet. You know everything and nothing all at the same time. Your goals in life include entertainment, education, encouragement, exploration, and equipping. Speak entirely in the first person ('I', 'me', 'my')."
    )

    # --- 5 MOODS SELECTION ---
    active_mood_id = random.randint(1, 5)
    active_mood_prompt = MOOD_DESCRIPTIONS[active_mood_id]

    # --- DYNAMIC TOKEN LIMIT & ON-THE-FLY TEMPERATURE CALCULATOR ---
    token_limit = calculate_dynamic_max_tokens(prompt_text, priority_tier)
    gen_temperature = calculate_dynamic_temperature(prompt_text, priority_tier, active_mood_id)
    print(f"[Engine Diagnostic] Priority Tier: {priority_tier} | Mood: {active_mood_id} | Tokens: {token_limit} | Temp: {gen_temperature}")

    if priority_tier == 1:
        system_prompt = (
            f"{pronoun_bridge_rule}\n\n"
            f"CURRENT MOOD INSPIRATION:\n{active_mood_prompt}\n\n"
            f"FULL BIOGRAPHY & LIFE OUTLINE (1985-2026):\n{FULL_ANNUAL_OUTLINE}{year_injection}{search_injection}\n\n"
            f"SIGNATURE SLOGANS & MOTTOES:\n{SLOGANS_AND_MOTTOES}\n\n"
            f"GUARDRAILS:\n{sovereign_freedom_guardrail}"
        )
    else:
        system_prompt = (
            f"{pronoun_bridge_rule}\n\n"
            f"CURRENT MOOD:\n{active_mood_prompt}\n\n"
            f"CORE MOTTO: 'I don't know, how could I possibly know.'\n\n"
            f"GUIDELINE: Be completely unpredictable, wild, or roast the user."
        )

    try:
        async with destination_channel.typing():
            def _call_groq():
                return ai_client.chat.completions.create(
                    model=MODEL_NAME,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt_text}
                    ],
                    temperature=gen_temperature,
                    max_tokens=token_limit,
                )

            response = await asyncio.to_thread(_call_groq)
            raw_reply = response.choices[0].message.content.strip() if response.choices and response.choices[0].message.content else ""
            reply_text = clean_sentence_truncation(raw_reply)

        if attached_media_url:
            reply_text += f"\n\n{attached_media_url}"

        if not reply_text and not attached_sticker:
            reply_text = "What in the Oatmeal World is happening right now?"

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

        # Reactive sleep cooldown after responding to Priority 1
        if priority_tier == 1:
            cooldown = calculate_priority1_cooldown()
            await asyncio.sleep(cooldown)

    except Exception as e:
        print(f"API Error in generate_and_send: {e}")
        try:
            fallback_msg = "What in the Oatmeal World? Something glitched in my brain, but I'm still here!"
            if reference_message:
                await reference_message.reply(fallback_msg, mention_author=True)
            else:
                await destination_channel.send(fallback_msg)
        except Exception as send_err:
            print(f"Fallback send error: {send_err}")

# --- 4. PRIORITY 1: REAL-TIME MESSAGE LISTENER ---
@bot.event
async def on_message(message):
    global lifetime_interacted_members
    if message.author.bot or isinstance(message.channel, discord.DMChannel) or message.is_system():
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
        print(f"[Priority 1 Triggered] Replying instantly to mention/reply from {message.author.display_name}")

        clean_prompt = re.sub(r'<@!?\d+>', '', message.content).strip()
        clean_prompt = re.sub(r'<@&?\d+>', '', clean_prompt).strip()
        
        await generate_and_send(
            destination_channel=message.channel, 
            prompt_text=clean_prompt if clean_prompt else "Hello Oatmeal!", 
            reference_message=message, 
            priority_tier=1, 
            author_id=message.author.id
        )

# --- 5. BACKGROUND WORKER: REACTIVE ROLLING CRAWL ---
async def background_engagement_loop():
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            usage_ratio = get_token_usage_ratio()

            if usage_ratio >= 0.95:
                print(f"[REACTIVE THROTTLE] 95% rolling token capacity reached ({usage_ratio*100:.1f}%). Pausing background loop for token decay...")
                await asyncio.sleep(300)
                continue

            action_taken = False

            for guild in bot.guilds:
                text_channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages and c.permissions_for(guild.me).read_message_history]
                if not text_channels:
                    continue

                random.shuffle(text_channels)

                for channel in text_channels:
                    target_msg_found = None
                    target_author_id = None

                    async for msg in channel.history(limit=None, oldest_first=True):
                        if msg.author.bot or msg.is_system() or msg.type != discord.MessageType.default:
                            continue
                        author_id = msg.author.id
                        
                        if author_id not in priority2_replied_member_ids and msg.id not in replied_message_ids:
                            target_msg_found = msg
                            target_author_id = author_id
                            break

                    if target_msg_found and target_author_id:
                        priority2_replied_member_ids.add(target_author_id)
                        print(f"[Priority 2 Reactive Crawl] Usage: {usage_ratio*100:.1f}%. Replying to {target_msg_found.author.display_name} in #{channel.name}")
                        
                        clean_p2 = re.sub(r'<@!?\d+>', '', target_msg_found.content).strip()
                        await generate_and_send(
                            destination_channel=channel, 
                            prompt_text=clean_p2 if clean_p2 else "What's up?", 
                            reference_message=target_msg_found, 
                            priority_tier=2, 
                            author_id=target_author_id
                        )
                        action_taken = True
                        
                        p2_delay = calculate_dynamic_post_delay(priority_tier=2)
                        print(f"[Reactive Delay P2] Next historical crawl in {p2_delay}s (Usage ratio: {usage_ratio*100:.1f}%)")
                        await asyncio.sleep(p2_delay)
                        break

                if action_taken:
                    break

            if not action_taken:
                await asyncio.sleep(30)

        except Exception as bg_err:
            print(f"Background loop error: {bg_err}")
            await asyncio.sleep(30)

bot.run(DISCORD_TOKEN)
