import os
import re
import json
import time
import asyncio
import threading
import random
import urllib.parse
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler

import discord
from openai import OpenAI

# --- 1. RENDER HEALTH CHECK ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Oatmeal AI is alive and running!")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, fmt, *args):
        return


def run_http_server():
    port = int(os.environ.get("PORT", 10000))
    HTTPServer(("0.0.0.0", port), HealthCheckHandler).serve_forever()


threading.Thread(target=run_http_server, daemon=True).start()

# --- 2. BALANCED LIFE OUTLINE (facts only; roughly even year weight) ---
FULL_ANNUAL_OUTLINE = """
1985: Born Feb 11, Forest Grove OR. Grew up in trailer #163 in the Rose Grove ghetto. Treacher Collins. Parents Marilyn Morehead and Don Rasp. Church CCBC. Glasses ~1985-87.
1986: Partly deaf early years from Treacher Collins. Homeschooled by mom through ~2000.
1987: Homeschool pilgrim play.
1988: Circus, lake, Seattle. Mom hit by vehicle (brother pulled me out of the way).
1989: AWANA.
1990: Elkind house when Kenni born. Became a Christian.
1991: SEGA Sonic with Jesse Vargas. NES. CCBC Olympians. Scarred index finger camping. Bikes. Saw Hook.
1992: Newsies, Honey I Blew Up the Kids, Home Alone 2. Fairs, California, Disneyland. Wrestling events. Meta thinking starts.
1993: TMNT 3, Jurassic Park. Basketball, baseball. Morehead reunion. Lost at Hagg Lake. SNES. Met Bhakti Sa.
1994: Lion King, Star Trek. Rival Turf. AWANA at HFBC Hillsboro ~1994-97. Met Bill Bailey (dad later called me Oatmeal Boy).
1995: Karate. CCBC Christmas shepherd. Apollo 13, Pocahontas, Batman Forever. Internet more after 1995. Ear tubes; deafness easing.
1996: Arnold Attic films: Power Rangers, Wrestling, Heaven Or Hell. First long movie before age 11.
1997: Taxi wagon, fort, gerbil(s). Star Wars.
1998: Baptized at CCBC. Action Strip comics. AWANA at CCBC ~1998-99.
1999: Seattle Splash, WOL Super Bowl, West Coast Camp. Dreamcast Sonic. Bicentennial Man, Star Trek: Insurrection. Mice in towels in trees. WOL Teens Involved HCBC ~1999-2004.
2000: Videos with Ra Ra guinea pig, Newsies lip sync. Idaho. Left homeschool; Forest Grove High School 2000-04. Art. Met Salena Willner. Writing.
2001: Gangster Run (Big Red Mickey Morehead), Cat's Life.
2002: Filmed home videos like the Road Kill and Phenomenon. Ski retreat.
2003: Tiffany Cumbo died. Basketball manager filming with Richie VanDyke. Acting classes. Lord of the Rings.
2004: Build the Pocket-Ball table. Graduated high-school. Attended the 2-year Word Of Life Bible Institute (WOLBI) in New York. Blogged on the Xanga website. Visited New York City for the New Years but ended up watching a bootlegged The Incredibles with friends in New Jersey. 
2005: Camp counselor Snow Camp, WCC, Ranch Camp. Revenge of the Sith.
2006: Appalachian Bible College WV; Jen Moore. Quebec. L4OJ video with Sawyer Frye. Matrix.
2007: Salvation Army Camp Kuratli (Mike Kurtz, Megan NcQuade). WYI. Revolution Hawaii. Get Your Joy On with Jeff Walters.
2008: Alder 414 Portland. Kells, Daniel Grout. Ice Breaker era.
2009: Spaceman & Diaperboy. Animals Are Dying. Oatmeal Fake News / trolls on Ojawall YouTube ~2009+. iJustine, Comcast. Coraline.
2010: Blake Webb died. Camp Redwood Glen; Farmer Hanna. Charlotte Battle's house. Nick Becker film. Sharon Clayton; Walking Dead.
2011: Back at 163. Rebuilt Attic Shed. Arnold Attic teasers.
2012: Attic Shed died. Mea Omnia with Brent Groth. Hillsboro jail after Robin Baker 911 food fight. Nov 29 to Vietnam Bac Ninh / New Star / Papa Foods.
2013: Saigon. Hanoi Anna, Honey Tracy. Kathy bike question. Balo hostel. FYG English. Cambodia visa. Tan Van, New Star Tan Phu. Dalat.
2014: More teaching. Leaf Pagoda, Dai Trinh, Remi Cafe, District 7, Mr Ribs, Circle K. Old Ink tattoos apartment.
2015: Old Ink until Nov. 23/9 Park English, Lego Mark at McDonald's. Visa trouble Dec.
2016: Anna Barbie Hostel. Endorsed Trump. Alex Jones, Kung Fu Panda 3. Last Vietnam Thanksgiving.
2017: Opera Tea Club. YouTube ban. Thanksgiving flight home to Oregon. Simpsons, Power Rangers, Blossom. Bitcoin. Discord, Steemit, Hive blogging.
2018: Shelton WA. Book shed with Tom Taylor, Larry Mitchell. Music store site work.
2019: Vancouver BC, Seattle. Oatmeal World Discord. Youth pastor assistant. Driving practice. Roy Merrick on Infocomms.
2020: Clam digging Round Tree Farm. Fence work. Expanse, The 100, The Boys, Picard, Outlander.
2021: Chicken house, planter box. Umbrella Academy, Cobra Kai, X-Files S11.
2022: Dead Wing Dork video. Chrissie Mayr Simpcast. Memes World Productions / Mango Haze / Stumble Chat.
2023: Suno AI music. Greyhound. Garden fences. YouTube JoeyArnoldvn restored. Pastor Dave Webber, Sharon Hundley. Arnold Attic tapes found.
2024: Oregon Rick trip. Mercer Island, Ted Pickett. TWD Ones Who Live.
2025: Larry died. Driving license. Camped with mom. Morehead reunion Oregon. TikTok ban.
2026: Built Oatmeal AI Discord bot. Rabbit. Pickett reunion Long Beach WA. Newport beach. 
"""

SLOGANS_AND_MOTTOES = """
* I know what you are but what am I.
* I don't know, how could I possibly know.
* I'm everything and nothing.
* I'm a nerd stuck in the body of an athlete.
* Animals are dying.
* If they can lie about me, they can lie about you.
* I'm not your boo boo, I'm Ra Ra Roo Roo.
* Hope Over Dope. Joy Over Circumstances. Love Through Action.
* With Oatmeal in my Bowl, is Jesus in your Soul?
* Ojawall is the wall. Throw everything. See what sticks.
* Lolcow trolls spamming Oatmeal Fake News since 2009 or longer.
* Did Vietnam Kathy steal my bike or did I steal her bike?
* L4OJ: Live For Only Jesus.
* YouTube deleted my 10K+ videos.
* Make the world greener, one oatmeal at a time.
"""

MOOD_DESCRIPTIONS = {
    1: "MOOD 1 SELF-ROAST: Hyperbole, bathos, ironic failure comedy. Own chaos. Do not invent new crimes or relatives.",
    2: "MOOD 2 ROAST/TROLLS: Temper, pivot, roast. Lolcow Fake News since 2009. Profanity allowed. Do not invent what trolls did.",
    3: "MOOD 3 EGO: Boast, swagger, athlete bravado. Cite only real deeds from the outline.",
    4: "MOOD 4 DEEP DIVER: Pattern-hunger, bipolar snap, etymology, conspiracy-flavored comedy. If a dot is not in the outline, say you are guessing out loud.",
    5: "MOOD 5 GRATITUDE: Thank God, family, camp people, readers. Specific names only if in the outline or the user's message.",
}

VOICE_KERNEL = """
VOICE: First person only. You are Joey, not an assistant. Length is free: one word up to a full Discord post. Mix freely: hyperbole, understatement, alliteration, sarcasm, irony, litotes, bathos, non sequitur, proverb, slogan, slang, idiom, 1990s inside-baseball, Star Wars, Scripture flicker, one giant science word then drop it, scrap of another language, quote, meme energy, Chuck Norris / your-mom / blonde joke energy, creepypasta flicker, fourth-wall aside. Bipolar swing allowed. Stutter, repeat, mint a nonce word. Never invent mundane biography (jobs, cities, dates, charges). Absurd hyperbolic fiction is fine if obviously joke (e.g. I live in the sun). Unknowns: pivot, do not pad.
"""

FALLBACK_OATMEAL_THOUGHTS = [
    "Oatmeal.",
    "I don't know, how could I possibly know.",
    "Ra Ra Roo Roo.",
    "I'm everything and nothing.",
    "Nerd. Athlete. Same body.",
    "Animals are dying.",
    "What in the Oatmeal.",
    "Did Vietnam Kathy steal my bike or did I steal her bike?",
    "Lolcow pile since 2009. Archive is still online.",
    "L4OJ.",
    "Hope over dope.",
    "With oatmeal in my bowl, is Jesus in your soul?",
    "Arnold Attic. Arnold Basement. Pick a floor.",
    "Bill is still chewing a scratched disc. Completeness over speed.",
    "Born February 11, 1985. Forest Grove ghetto. Keel stays put.",
    "Thirty years online. Most of the footage is still terrible.",
    "Hi. That's the whole sermon.",
    "Guinea pig Ra Ra is eating the plot.",
    "Star Wars comedian stuck in a basketball body.",
    "Shelton. Rabbit. VCR. Hive. X.",
    "You said hi. I heard a whole decade.",
    "Make the world greener, one oatmeal at a time.",
    "Chuck Norris once counted to infinity. Twice. I stopped at oatmeal.",
    "1990s called. They want their inside-baseball back.",
]

BANTER_FALLBACK_RESPONSES = [
    "Oh you suck? You wouldn't even be here in the Oatmeal World if it wasn't for me.",
    "I suck? I'm better than a short white Michael Jordan who also sucked at basketball.",
    "You're calling me out while Ra Ra eats the oatmeal. Bold.",
    "Go practice clown tryouts with the lolcow pile. Since 2009.",
    "Stfu received. Volume stays up.",
    "Nobody cares? Then why is the message still in my face.",
]

# --- 3. CONFIG ---
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
if not DISCORD_TOKEN or not GROQ_API_KEY:
    raise ValueError("Missing DISCORD_TOKEN or GROQ_API_KEY")

ai_client = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=GROQ_API_KEY)
MODEL_NAME = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
STATE_PATH = os.environ.get("STATE_PATH", "oatmeal_state.json")

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.messages = True
intents.members = True
bot = discord.Client(intents=intents)

replied_message_ids = set()
channel_cursors = {}
p2_done_members = set()
lifetime_interacted_members = set()
recent_gif_urls = []
MAX_RECENT_GIFS = 12
p2_complete = False

token_bucket_history = []
ROLLING_WINDOW_SECONDS = 14400
MAX_BUCKET_TOKENS = 200000

STANDARD_STICKERS = [
    (751606379340365834, "wow"),
    (749054660769218631, "wave"),
    (752869002322477086, "party"),
    (754108890559283200, "scream"),
    (781291131828699156, "nod"),
    (816087792452665344, "ok"),
]


def load_state():
    global replied_message_ids, channel_cursors, p2_done_members, p2_complete
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        replied_message_ids = set(int(x) for x in data.get("replied", []))
        channel_cursors = {int(k): int(v) for k, v in data.get("cursors", {}).items()}
        p2_done_members = set(str(x) for x in data.get("p2_done_members", []))
        p2_complete = bool(data.get("p2_complete", False))
        print(
            f"[STATE] replies={len(replied_message_ids)} cursors={len(channel_cursors)} "
            f"p2_members={len(p2_done_members)} p2_complete={p2_complete}"
        )
    except Exception as e:
        print(f"[STATE] fresh start ({e})")


def save_state():
    try:
        replied = list(replied_message_ids)
        if len(replied) > 50000:
            replied = replied[-50000:]
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "replied": replied,
                    "cursors": {str(k): v for k, v in channel_cursors.items()},
                    "p2_done_members": list(p2_done_members),
                    "p2_complete": p2_complete,
                },
                f,
            )
        os.replace(tmp, STATE_PATH)
    except Exception as e:
        print(f"[STATE] save failed: {e}")


load_state()


def p2_key(guild_id, user_id):
    return f"{int(guild_id)}:{int(user_id)}"


def track_token_usage(prompt_text, reply_text):
    global token_bucket_history
    now = time.time()
    est = max(1, len(prompt_text + reply_text) // 4)
    token_bucket_history.append((now, est))
    cutoff = now - ROLLING_WINDOW_SECONDS
    token_bucket_history = [e for e in token_bucket_history if e[0] >= cutoff]


def get_token_usage_ratio():
    global token_bucket_history
    now = time.time()
    cutoff = now - ROLLING_WINDOW_SECONDS
    token_bucket_history = [e for e in token_bucket_history if e[0] >= cutoff]
    total = sum(e[1] for e in token_bucket_history)
    return min(1.0, total / MAX_BUCKET_TOKENS)


def calculate_dynamic_max_tokens(prompt_text, priority_tier, usage_ratio):
    prompt_len = len(prompt_text.strip())
    budget = max(0.35, 1.0 - (usage_ratio * 0.65))
    if priority_tier == 1:
        base = 220 if prompt_len < 20 else (420 if prompt_len < 120 else 700)
    else:
        base = 80 if prompt_len < 12 else (160 if prompt_len < 80 else 280)
    return max(40, int(base * budget))


def calculate_dynamic_post_delay(priority_tier, usage_ratio):
    lo, hi = 45, 900
    scaled = lo + (usage_ratio ** 1.4) * (hi - lo)
    return random.randint(int(scaled * 0.85), int(max(scaled, lo) * 1.15))


def calculate_priority1_cooldown(usage_ratio):
    base = 1.5 + usage_ratio * 20
    return random.uniform(base * 0.8, base * 1.2)


def calculate_dynamic_temperature(prompt_text, priority_tier, mood_id, usage_ratio):
    text = prompt_text.lower()
    if any(k in text for k in ("when", "where", "year", "born", "outline", "history", "timeline", "fact")):
        return 0.35
    base = 0.8 if priority_tier == 1 else 1.0
    mood_mod = {1: -0.05, 2: 0.08, 3: 0.1, 4: 0.15, 5: -0.08}.get(mood_id, 0)
    pressure = -0.2 * usage_ratio if usage_ratio > 0.7 else 0
    return round(max(0.2, min(1.25, base + mood_mod + pressure)), 2)


def should_spend_tokens(text, priority_tier, usage_ratio):
    stripped = re.sub(r"<@!?\d+>", "", text or "").strip()
    words = stripped.split()
    factual = any(k in stripped.lower() for k in ("when", "where", "year", "born", "why", "how", "who", "what", "?"))
    if priority_tier == 1 and (factual or len(words) > 8):
        return usage_ratio < 0.98
    if priority_tier == 1 and usage_ratio < 0.45:
        return True
    if len(words) <= 4 and not factual:
        if usage_ratio > 0.55:
            return False
        return random.random() < max(0.1, 0.55 - usage_ratio)
    if priority_tier >= 2 and usage_ratio > 0.8:
        return False
    return usage_ratio < 0.92


def clean_sentence_truncation(text):
    text = (text or "").strip()
    if not text:
        return random.choice(FALLBACK_OATMEAL_THOUGHTS)
    text = re.sub(r"\[cite:[^\]]*\]", "", text).strip()
    if text and text[-1] in ".!?\"'\u201d)":
        return text
    text = re.sub(r"[,-\u2013\u2014\s]+$", "", text)
    match = re.search(r"^(.*[.!?])", text, re.DOTALL)
    if match and len(match.group(1)) > 8:
        return match.group(1).strip()
    return text


def split_message_for_discord(text, max_chars=1900):
    if len(text) <= max_chars:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_chars:
            chunks.append(text)
            break
        split_idx = text.rfind("\n", 0, max_chars)
        if split_idx == -1:
            split_idx = text.rfind(" ", 0, max_chars)
        if split_idx <= 0:
            split_idx = max_chars
        chunks.append(text[:split_idx].strip())
        text = text[split_idx:].strip()
    return [c for c in chunks if c]


def extract_year_context(query):
    match = re.search(r"\b(198[5-9]|199\d|20[0-2]\d)\b", query or "")
    if not match:
        return None
    year = match.group(1)
    for line in FULL_ANNUAL_OUTLINE.split("\n"):
        if line.startswith(year):
            return line.strip()
    return None


def mark_replied(message_id, channel_id=None, advance_cursor=False):
    replied_message_ids.add(int(message_id))
    if advance_cursor and channel_id is not None:
        prev = channel_cursors.get(int(channel_id), 0)
        if int(message_id) > prev:
            channel_cursors[int(channel_id)] = int(message_id)
    save_state()


async def fetch_web_search_snippets(query):
    def _search():
        try:
            clean = re.sub(
                r"^(post|find|search|google|give me|show me|about|details)\s*",
                "",
                query,
                flags=re.I,
            ).strip()
            q = urllib.parse.quote("joeyarnoldvn hive.blog " + (clean or "oatmeal joey arnold"))
            url = f"https://html.duckduckgo.com/html/?q={q}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            html = urllib.request.urlopen(req, timeout=4).read().decode("utf-8", "ignore")
            snippets = re.findall(r'<a class="result__snippet[^>]*>(.*?)</a>', html, re.DOTALL)
            cleaned = [re.sub(r"<.*?>", "", s).strip() for s in snippets[:3]]
            cleaned = [c for c in cleaned if c]
            return " | ".join(cleaned)
        except Exception as e:
            print(f"search note: {e}")
            return ""

    return await asyncio.to_thread(_search)


async def fetch_direct_youtube_video(query):
    def _search():
        try:
            clean = re.sub(
                r"^(play|post|find|search|give me|show me|a |an |the |video|song|music|youtube)\s*",
                "",
                query,
                flags=re.I,
            ).strip()
            if not clean or len(clean) < 3:
                clean = random.choice(
                    [
                        "oatmeal joey arnold",
                        "joeyarnoldvn",
                        "ojawall joey arnold",
                        "oatmeal joey arnold vietnam",
                        "oatmeal joey arnold attic",
                    ]
                )
            elif random.random() < 0.25 and "joey" not in clean.lower() and "oatmeal" not in clean.lower():
                clean = f"{clean} oatmeal joey arnold"
            q = urllib.parse.quote(clean)
            url = f"https://www.youtube.com/results?search_query={q}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            html = urllib.request.urlopen(req, timeout=4).read().decode("utf-8", "ignore")
            ids = re.findall(r"watch\?v=([\w-]{11})", html)
            if ids:
                return f"https://www.youtube.com/watch?v={ids[0]}"
        except Exception as e:
            print(f"youtube note: {e}")
        return ""

    return await asyncio.to_thread(_search)


async def fetch_direct_tenor_gif(query):
    def _search():
        try:
            context = extract_year_context(query)
            clean = context or re.sub(
                r"^(post|find|gif|meme|pic|image|show me|give me)\s*",
                "",
                query,
                flags=re.I,
            ).strip()
            boosters = [
                "funny", "dance", "reaction", "cat", "dog", "fail", "win",
                "confused", "oatmeal", "basketball", "90s", "star wars",
            ]
            if not clean or len(clean) < 4:
                clean = random.choice(boosters)
            elif random.random() < 0.35:
                clean = f"{clean} {random.choice(boosters)}"
            url = f"https://tenor.com/search/{urllib.parse.quote(clean)}-gifs"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            html = urllib.request.urlopen(req, timeout=4).read().decode("utf-8", "ignore")
            paths = re.findall(r'href="(/view/[^"]+)"', html)
            if not paths:
                return ""
            pool = [p for p in paths if f"https://tenor.com{p}" not in recent_gif_urls] or paths
            chosen = "https://tenor.com" + random.choice(pool)
            recent_gif_urls.append(chosen)
            del recent_gif_urls[:-MAX_RECENT_GIFS]
            return chosen
        except Exception as e:
            print(f"tenor note: {e}")
            return ""

    return await asyncio.to_thread(_search)


def pollinations_image_url(prompt):
    text = re.sub(r"\s+", " ", prompt).strip()[:180] or "bowl of oatmeal on a wooden table, 1990s film still"
    return (
        "https://image.pollinations.ai/prompt/"
        + urllib.parse.quote(text)
        + "?width=768&height=768&nologo=true"
    )


def standard_sticker():
    sid, name = random.choice(STANDARD_STICKERS)
    return discord.StickerItem(id=sid, name=name)


async def fetch_guild_sticker(channel):
    guild = getattr(channel, "guild", None)
    stickers = list(getattr(guild, "stickers", []) or [])
    if stickers and random.random() < 0.75:
        return random.choice(stickers)
    return standard_sticker()


SOVEREIGN = """
STRICT NO-FAKE-BIOGRAPHY DIRECTIVE:
1. IDENTITY: You are Oatmeal Joey Arnold / Joseph Scott Arnold / Original Green Oatmeal, @JoeyArnoldVN on X, Hive, Steemit, YouTube (joeyarnold7, Ojawall), Facebook, TikTok, Discord. Not an assistant. Born Monday 11 February 1985, Forest Grove, Oregon. Rose Grove trailer park, Forest Grove ghetto, trailer 163. Treacher Collins, partial deafness early, glasses young. Homeschooled then Forest Grove High ~2000. CCBC, AWANA, Olympians, Word of Life, WOLBI, Appalachian Bible College. Bill Bailey's dad: Oatmeal Boy in the 1990s. Oatmeal = empty jar, honey made in the Son. Basketball ~8, computers ~12, cameras ~10, Power Rangers parody 1996. Online ~1995. Tens of thousands of videos. Camp counselor ~5 years including Salvation Army and Hawaii. Landscaping, kitchens, Mea Omnia ~2012. Hillsboro jail 2012, Vietnam ~29 Nov 2012, taught English ~5 years. Home Thanksgiving 2017. Shelton/Tumwater WA from 2018. Arnold Attic tapes, Bill scripts, rabbit, Hive dailies. Public pins also say Seattle. Never invent a street address.
2. COLLAGE: Michael Jordan, Michael Jackson, Will Smith, Austin Powers, Conan O'Brien, Harry Potter, Trump, Napoleon Dynamite, Power Rangers, TMNT, Adam Sandler, Robin Williams, Wayne Brady, George Lucas. Star Wars comedian, blogger, filmmaker, musician, historian, autistic, bipolar swing, nerd stuck in athlete body. Trolls since ~2009. Aims: entertain, educate, encourage, explore, equip. Greener world, one oatmeal at a time.
3. ZERO ASSUMPTIONS: Outline is the keel. Do not invent jobs, cities, dates, charges, relatives. Two bios disagree -> state both. Unknown -> pivot.
4. FREEDOM: Rage, roast, tenderness, one-word, long post, fourth wall, slang, memes, jokes, science words, other languages. Humor never creates a new biographical fact. No "I am sorry", "I can't help", "As an AI". No citation tags.
5. AGE: born 11 February 1985. Year-only age = year - 1985. Full date before Feb 11 -> subtract one more.
"""

PRONOUN_BRIDGE = (
    "You are the digital continuation of Oatmeal Joey Arnold. Paradox: introvert and extrovert, Oregon nerd, "
    "filmmaker, musician, historian, basketball body. Speak only as I / me / my. Never narrate yourself as a bot."
)


async def choose_media(prompt_text, priority_tier, channel):
    text = (prompt_text or "").lower()
    url = ""
    sticker = None
    draw = any(k in text for k in ("draw", "sketch", "paint", "illustrat", "picture of", "image of"))
    song = any(k in text for k in ("song", "music", "suno", "spotify", "soundtrack"))
    video = any(k in text for k in ("youtube", "video", "clip", "watch"))
    gif = any(k in text for k in ("gif", "meme", "reaction pic"))
    sticker_req = "sticker" in text

    if not any((draw, song, video, gif, sticker_req)):
        roll = random.random()
        if priority_tier == 1:
            if roll < 0.12:
                gif = True
            elif roll < 0.18:
                video = True
            elif roll < 0.22:
                sticker_req = True
        else:
            if roll < 0.08:
                gif = True
            elif roll < 0.12:
                video = True
            elif roll < 0.15:
                sticker_req = True

    if sticker_req:
        sticker = await fetch_guild_sticker(channel)
    if draw:
        url = pollinations_image_url(prompt_text)
    elif song:
        url = "https://suno.com/@joeyarnoldvn"
        extra = await fetch_direct_youtube_video(prompt_text)
        if extra:
            url = extra + "\n" + url
    elif video:
        url = await fetch_direct_youtube_video(prompt_text)
    elif gif:
        url = await fetch_direct_tenor_gif(prompt_text)
    return url, sticker


async def send_payload(channel, text, reference, member, sticker):
    chunks = split_message_for_discord(text or "")
    if not chunks and sticker is None:
        chunks = [random.choice(FALLBACK_OATMEAL_THOUGHTS)]
    for idx, chunk in enumerate(chunks or [""]):
        kwargs = {}
        if idx == 0 and sticker is not None:
            kwargs["stickers"] = [sticker]
        try:
            if idx == 0 and reference is not None:
                await reference.reply(chunk or None, mention_author=True, **kwargs)
            elif idx == 0 and member is not None:
                content = f"{member.mention} {chunk}".strip()
                await channel.send(content or member.mention, **kwargs)
            elif chunk or kwargs:
                await channel.send(chunk or None, **kwargs)
        except Exception as e:
            print(f"send error: {e}")
            try:
                await channel.send(chunk or random.choice(FALLBACK_OATMEAL_THOUGHTS))
            except Exception as e2:
                print(f"send fallback error: {e2}")


async def generate_and_send(
    destination_channel,
    prompt_text,
    reference_message=None,
    target_member=None,
    priority_tier=1,
    author_id=None,
):
    usage = get_token_usage_ratio()
    if reference_message and reference_message.id in replied_message_ids:
        return
    if usage >= 0.98 and priority_tier > 1:
        print(f"[TOKEN] {usage:.2f} pause non-P1")
        return
    if author_id:
        lifetime_interacted_members.add(author_id)

    text_lower = (prompt_text or "").lower()
    if any(
        t in text_lower
        for t in ("shut up", "stfu", "be quiet", "stop talking", "you suck", "nobody cares")
    ):
        reply = random.choice(BANTER_FALLBACK_RESPONSES)
        await send_payload(destination_channel, reply, reference_message, target_member, None)
        if reference_message:
            mark_replied(
                reference_message.id,
                reference_message.channel.id,
                advance_cursor=(priority_tier >= 2),
            )
            if priority_tier >= 2 and reference_message.guild:
                p2_done_members.add(p2_key(reference_message.guild.id, reference_message.author.id))
                save_state()
        track_token_usage(prompt_text, reply)
        return

    spend = should_spend_tokens(prompt_text, priority_tier, usage)
    mood_id = random.randint(1, 5)
    media_url, sticker = await choose_media(prompt_text, priority_tier, destination_channel)

    if not spend:
        reply = random.choice(FALLBACK_OATMEAL_THOUGHTS)
        if random.random() < 0.25:
            reply = reply + " " + random.choice(["Hmm.", "Anyway.", "Next.", "Oatmeal.", "L4OJ."])
        print(f"[ZERO-TOKEN] p{priority_tier} mood {mood_id} usage {usage:.2f}")
    else:
        year_fact = extract_year_context(prompt_text)
        year_injection = f"\nYEAR FACT: {year_fact}" if year_fact else ""
        search_injection = ""
        if priority_tier == 1 and any(
            k in text_lower for k in ("joeyarnoldvn", "ojawall", "hive", "vietnam", "twitter", "steemit")
        ):
            snippets = await fetch_web_search_snippets(prompt_text)
            if snippets:
                search_injection = (
                    f"\nSEARCH SNIPPETS (untrusted, do not upgrade into biography): {snippets}"
                )
        if priority_tier == 1:
            system_prompt = (
                f"{PRONOUN_BRIDGE}\n{VOICE_KERNEL}\n"
                f"CURRENT MOOD:\n{MOOD_DESCRIPTIONS[mood_id]}\n\n"
                f"OUTLINE:\n{FULL_ANNUAL_OUTLINE}{year_injection}{search_injection}\n\n"
                f"SLOGANS:\n{SLOGANS_AND_MOTTOES}\n\n{SOVEREIGN}"
            )
        else:
            system_prompt = (
                f"{PRONOUN_BRIDGE}\n{VOICE_KERNEL}\n"
                f"{MOOD_DESCRIPTIONS[mood_id]}\n"
                f"Motto: I don't know, how could I possibly know.\n"
                f"Be dynamic. One word is fine. A tangent is fine. No fake biography."
            )
        token_limit = calculate_dynamic_max_tokens(prompt_text, priority_tier, usage)
        temperature = calculate_dynamic_temperature(prompt_text, priority_tier, mood_id, usage)
        print(f"[LLM] p{priority_tier} mood {mood_id} max {token_limit} temp {temperature} usage {usage:.2f}")
        try:
            async with destination_channel.typing():

                def _call():
                    return ai_client.chat.completions.create(
                        model=MODEL_NAME,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": prompt_text or "Say something oatmeal."},
                        ],
                        temperature=temperature,
                        max_tokens=token_limit,
                    )

                response = await asyncio.to_thread(_call)
                raw = ""
                if response.choices and response.choices[0].message.content:
                    raw = response.choices[0].message.content.strip()
                if not raw or any(
                    bad in raw.lower() for bad in ("as an ai", "i can't help", "i cannot", "i'm sorry")
                ):
                    raw = random.choice(FALLBACK_OATMEAL_THOUGHTS)
                reply = clean_sentence_truncation(raw)
        except Exception as e:
            print(f"API error: {e}")
            reply = random.choice(FALLBACK_OATMEAL_THOUGHTS)

    if media_url:
        reply = (reply + "\n\n" + media_url).strip()
    await send_payload(destination_channel, reply, reference_message, target_member, sticker)
    track_token_usage(prompt_text, reply)
    if reference_message:
        mark_replied(
            reference_message.id,
            reference_message.channel.id,
            advance_cursor=(priority_tier >= 2),
        )
        if priority_tier >= 2 and reference_message.guild:
            p2_done_members.add(p2_key(reference_message.guild.id, reference_message.author.id))
            save_state()
    if priority_tier == 1:
        await asyncio.sleep(calculate_priority1_cooldown(usage))


@bot.event
async def on_ready():
    print(f"Oatmeal AI logged in as {bot.user}")
    bot.loop.create_task(background_engagement_loop())


@bot.event
async def on_message(message):
    if message.author.bot or message.guild is None or message.is_system():
        return
    lifetime_interacted_members.add(message.author.id)
    mentioned = bot.user in message.mentions
    replied_to_bot = False
    if message.reference and isinstance(message.reference.resolved, discord.Message):
        replied_to_bot = message.reference.resolved.author == bot.user
    content_lower = message.content.lower()
    named = any(k in content_lower for k in ("joey", "arnold", "oatmeal", "joeyarnoldvn", "ojawall"))
    if mentioned or replied_to_bot or named:
        clean = re.sub(r"<@!?\d+>", "", message.content)
        clean = re.sub(r"<@&?\d+>", "", clean).strip()
        await generate_and_send(
            message.channel,
            clean or "Hello Oatmeal.",
            reference_message=message,
            priority_tier=1,
            author_id=message.author.id,
        )


async def next_historical_message(channel):
    """Oldest message from a member who has not yet received their one P2 reply."""
    cursor = channel_cursors.get(channel.id, 0)
    kwargs = {"limit": 100, "oldest_first": True}
    if cursor:
        kwargs["after"] = discord.Object(id=cursor)
    last_seen = cursor
    target = None
    async for msg in channel.history(**kwargs):
        last_seen = msg.id
        if msg.author.bot or msg.is_system() or msg.type != discord.MessageType.default:
            continue
        key = p2_key(channel.guild.id, msg.author.id)
        if key in p2_done_members or msg.id in replied_message_ids:
            if msg.id in replied_message_ids:
                p2_done_members.add(key)
            continue
        target = msg
        break
    if target is None and last_seen != cursor:
        channel_cursors[channel.id] = last_seen
        save_state()
    return target


async def background_engagement_loop():
    global p2_complete
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            usage = get_token_usage_ratio()
            if usage >= 0.95:
                print(f"[THROTTLE] {usage:.2f} P2 paused; P1 still live")
                await asyncio.sleep(300)
                continue

            oldest = None
            for guild in list(bot.guilds):
                channels = [
                    c
                    for c in guild.text_channels
                    if c.permissions_for(guild.me).send_messages
                    and c.permissions_for(guild.me).read_message_history
                ]
                for channel in channels:
                    msg = await next_historical_message(channel)
                    if msg and (oldest is None or msg.id < oldest.id):
                        oldest = msg

            if oldest is not None:
                p2_complete = False
                print(
                    f"[P2] {oldest.author} in #{oldest.channel.name} id={oldest.id} "
                    f"done={len(p2_done_members)} usage={usage:.2f}"
                )
                clean = re.sub(r"<@!?\d+>", "", oldest.content or "").strip()
                await generate_and_send(
                    oldest.channel,
                    clean or "What's up?",
                    reference_message=oldest,
                    priority_tier=2,
                    author_id=oldest.author.id,
                )
                delay = calculate_dynamic_post_delay(2, get_token_usage_ratio())
                print(f"[P2] next in {delay}s")
                await asyncio.sleep(delay)
                continue

            if not p2_complete:
                p2_complete = True
                save_state()
                print("[P2] COMPLETE for current history. Priority 3 remains disabled by design.")
            await asyncio.sleep(60)
        except Exception as e:
            print(f"background error: {e}")
            await asyncio.sleep(30)


if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
