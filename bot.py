#!/usr/bin/env python3
"""
Gemini's Oatmeal AI – Joey Arnold Discord Bot
Master Production Version 16.3 (Media-Only P2 + Smart Word Salad Fallback)

- Priority 2: Zero-token media-only historical backlog sweeper
- Smart 4-Tier Fallback Chain (LLM -> Word Salad -> Media -> Remix Fallback)
- Surgical timeline extraction & Ghost Whisperer token nudging
- Sustained Discord typing indicator loop
- Differential taxation + atomic state + 429 re-sync + CoinGecko integration
"""

import os
import re
import io
import json
import time
import asyncio
import threading
import random
import urllib.parse
import urllib.request
import collections
import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Set, Tuple, Optional
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone

import discord
from openai import OpenAI

# ============================================================
# 1. RENDER HEALTH CHECK
# ============================================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write("Oatmeal AI V16.3 – Zero-Token P2 + Smart Fallback".encode("utf-8"))
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()
    def log_message(self, *args):
        pass

def run_http_server():
    port = int(os.environ.get("PORT", 10000))
    try:
        HTTPServer(("0.0.0.0", port), HealthCheckHandler).serve_forever()
    except Exception as e:
        print(f"[HEALTH] {e}")

threading.Thread(target=run_http_server, daemon=True).start()

# ============================================================
# 2. CONFIG
# ============================================================
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
GROQ_API_KEY  = os.environ.get("GROQ_API_KEY")
if not DISCORD_TOKEN or not GROQ_API_KEY:
    raise ValueError("Missing DISCORD_TOKEN or GROQ_API_KEY")

ai_client = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=GROQ_API_KEY)
MODEL_NAME = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
STATE_PATH = os.environ.get("STATE_PATH", "/tmp/oatmeal_state.json")

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.messages = True
intents.members = True
bot = discord.Client(intents=intents)

# ============================================================
# 3. GLOBAL STATE
# ============================================================
replied_message_ids: Set[int] = set()
channel_cursors: Dict[str, int] = {}
priority2_replied_member_ids: Set[str] = set()
lifetime_interacted_members: Set[int] = set()
recent_gif_urls = collections.deque(maxlen=1000)

p3_member_indices: Dict[int, int] = {}
p7_cursors: Dict[str, int] = {}
p8_cursors: Dict[str, int] = {}

last_genie_timestamp = 0.0
DAILY_EXHAUSTED_UNTIL = 0.0
local_daily_tokens = 0
local_daily_requests = 0
local_daily_reset_day = None

last_bot_own_messages: Dict[int, int] = {}
blocked_users: Dict[str, float] = {}
user_message_history: Dict[int, List[Tuple[float, str]]] = {}

def p2_key(guild_id: int, author_id: int) -> str:
    return f"{guild_id}:{author_id}"

def load_state():
    global replied_message_ids, channel_cursors, priority2_replied_member_ids
    global last_bot_own_messages, blocked_users, p7_cursors, p8_cursors
    try:
        if os.path.exists(STATE_PATH):
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            replied_message_ids = set(int(x) for x in data.get("replied", []))
            channel_cursors = {str(k): int(v) for k, v in data.get("cursors", {}).items()}
            priority2_replied_member_ids = set(str(x) for x in data.get("p2_done_members", []))
            last_bot_own_messages = {int(k): int(v) for k, v in data.get("own_msgs", {}).items()}
            blocked_users = {str(k): float(v) for k, v in data.get("blocked_users", {}).items()}
            p7_cursors = {str(k): int(v) for k, v in data.get("p7_cursors", {}).items()}
            p8_cursors = {str(k): int(v) for k, v in data.get("p8_cursors", {}).items()}
            print(f"[STATE] Loaded {len(replied_message_ids)} replies | P7:{len(p7_cursors)} P8:{len(p8_cursors)}")
    except Exception as e:
        print(f"[STATE] Fresh start: {e}")

def save_state():
    def _write():
        try:
            dir_name = os.path.dirname(STATE_PATH) or "/tmp"
            os.makedirs(dir_name, exist_ok=True)
            data = {
                "replied": list(replied_message_ids)[-50000:],
                "cursors": channel_cursors,
                "p2_done_members": list(priority2_replied_member_ids),
                "own_msgs": {str(k): v for k, v in last_bot_own_messages.items()},
                "blocked_users": blocked_users,
                "p7_cursors": p7_cursors,
                "p8_cursors": p8_cursors,
            }
            fd, tmp_path = tempfile.mkstemp(prefix="oatmeal_", suffix=".json.tmp", dir=dir_name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, STATE_PATH)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
        except Exception as e:
            print(f"[STATE] Save failed: {e}")
    threading.Thread(target=_write, daemon=True).start()

# ============================================================
# 4. GHOST WHISPERER TOKEN CONTROL & NUDGING PROTOCOL
# ============================================================
GROQ_REMAINING_TPM = 8000
GROQ_REMAINING_RPD = 1000
GROQ_RATE_LIMITED_UNTIL = 0.0

token_windows = {5: collections.deque(), 15: collections.deque(), 60: collections.deque()}
WINDOW_SECONDS = {5: 300, 15: 900, 60: 3600}

groq_api_lock = asyncio.Lock()
last_api_call_timestamp = 0.0

@dataclass(order=True)
class PendingJob:
    priority_tier: int
    timestamp: float
    channel: Any = field(compare=False)
    prompt_text: str = field(compare=False)
    reference_message: Any = field(compare=False, default=None)
    target_member: Any = field(compare=False, default=None)
    author_id: int = field(compare=False, default=0)
    p8_channel_id: Optional[str] = field(compare=False, default=None)
    p8_message_id: Optional[int] = field(compare=False, default=None)

delay_queue: asyncio.PriorityQueue = asyncio.PriorityQueue()

def check_and_reset_daily_counters():
    global local_daily_tokens, local_daily_requests, local_daily_reset_day
    today = datetime.now(timezone.utc).date()
    if local_daily_reset_day != today:
        local_daily_tokens = 0
        local_daily_requests = 0
        local_daily_reset_day = today
        print(f"[DAILY RESET] {today}")

def update_plane_alpha_from_response(raw_response=None, exception=None):
    global GROQ_REMAINING_TPM, GROQ_REMAINING_RPD, GROQ_RATE_LIMITED_UNTIL, DAILY_EXHAUSTED_UNTIL, local_daily_tokens
    check_and_reset_daily_counters()
    headers = {}
    if raw_response and hasattr(raw_response, "headers"):
        headers = {k.lower(): v for k, v in raw_response.headers.items()}
    elif exception and hasattr(exception, "response") and exception.response:
        try:
            headers = {k.lower(): v for k, v in exception.response.headers.items()}
        except Exception:
            pass

    if "x-ratelimit-remaining-tokens" in headers:
        try:
            GROQ_REMAINING_TPM = int(headers["x-ratelimit-remaining-tokens"])
        except Exception:
            pass
    if "x-ratelimit-remaining-requests" in headers:
        try:
            GROQ_REMAINING_RPD = int(headers["x-ratelimit-remaining-requests"])
        except Exception:
            pass

    err_str = str(exception or "").lower()
    if "429" in err_str or "rate limit" in err_str or "tpd" in err_str:
        match_used = re.search(r"used\s+(\d+)", err_str)
        if match_used:
            local_daily_tokens = int(match_used.group(1))
            print(f"[RE-SYNC] local_daily_tokens → {local_daily_tokens}")

        match_time = re.search(r"try again in\s+([\d\.]+)([smh])", err_str)
        retry_after = 45.0
        if match_time:
            val = float(match_time.group(1))
            unit = match_time.group(2)
            mult = 1.0 if unit == "s" else (60.0 if unit == "m" else 3600.0)
            retry_after = val * mult

        GROQ_RATE_LIMITED_UNTIL = max(GROQ_RATE_LIMITED_UNTIL, time.monotonic() + retry_after + 8.0)
        if "tpd" in err_str or local_daily_tokens >= 192000:
            DAILY_EXHAUSTED_UNTIL = max(DAILY_EXHAUSTED_UNTIL, time.monotonic() + max(retry_after, 4200))

def track_token_usage_real(tokens: int, requests: int = 1):
    global local_daily_tokens, local_daily_requests
    check_and_reset_daily_counters()
    now = time.time()
    tokens = max(1, int(tokens))
    local_daily_tokens += tokens
    local_daily_requests += requests
    for w, dq in token_windows.items():
        dq.append((now, tokens))
        cutoff = now - WINDOW_SECONDS[w]
        while dq and dq[0][0] < cutoff:
            dq.popleft()

def get_trajectory_metrics():
    check_and_reset_daily_counters()
    now = time.time()
    def window_sum(w):
        cutoff = now - WINDOW_SECONDS[w]
        return sum(t for ts, t in token_windows[w] if ts >= cutoff)
    W5 = window_sum(5)
    W15 = window_sum(15)
    W60 = window_sum(60)
    rho5 = W5 / 300.0
    rho15 = W15 / 900.0
    alpha = (rho5 - rho15) / 300.0
    u_tpm = max(0.0, min(1.0, 1.0 - (GROQ_REMAINING_TPM / 8000.0)))
    u_rpd = max(0.0, min(1.0, 1.0 - (GROQ_REMAINING_RPD / 1000.0)))
    u_tpd = max(0.0, min(1.0, local_daily_tokens / 200000.0))
    usage = max(u_tpm * 0.35, u_rpd * 0.20, u_tpd * 0.90)
    return {
        "usage": usage, "alpha": alpha,
        "remaining_tpm": GROQ_REMAINING_TPM,
        "remaining_rpd": GROQ_REMAINING_RPD,
        "local_daily": local_daily_tokens,
        "W5": W5, "W15": W15, "W60": W60
    }

def compute_tax_multipliers():
    m = get_trajectory_metrics()
    usage = m["usage"]
    alpha = max(0.0, m["alpha"])
    pressure = (usage * 2.5) + (2.8 * alpha) + ((usage ** 1.7) * 4.0)
    return {
        1: 1.0 + pressure * 0.10,
        4: 1.0 + pressure * (0.35 + 1.1 * usage),
        2: 0.0, # Zero token cost for media-only P2
        3: 1.0 + pressure * (1.50 + 3.0 * usage),
        5: 1.0 + pressure * (1.60 + 3.2 * usage),
        6: 1.0 + pressure * (0.80 + 2.0 * usage),
        7: 0.0,
        8: 1.0 + pressure * (0.05 + 0.3 * usage),
    }

def calculate_dynamic_max_tokens(priority_tier: int) -> int:
    if priority_tier in (2, 7):
        return 0
    if priority_tier == 8:
        return 16
    m = get_trajectory_metrics()
    usage = m["usage"]
    tax = compute_tax_multipliers().get(priority_tier, 1.0)
    headroom = max(80, GROQ_REMAINING_TPM - 250)
    base = {1: 450, 4: 150, 6: 120, 5: 100}.get(priority_tier, 100)
    
    compression = max(0.22, (1.0 - usage) ** 1.3)
    allowed = int((base * compression) / max(tax, 0.35))
    hard = {1: 850, 4: 220, 6: 160, 5: 140}.get(priority_tier, 150)
    
    result = max(64, min(allowed, hard, headroom))
    if priority_tier == 1:
        result = max(result, min(160, headroom))
    return result

# ============================================================
# 5. FULL YEAR-BY-YEAR TIMELINE + EXPANDED IDENTITY
# ============================================================
FULL_ANNUAL_OUTLINE = """
1985: Born Monday 11 February 1985 in Forest Grove, Oregon. Grew up in trailer #163 in the Rose Grove ghetto mobile home park. Born with Treacher Collins syndrome (affecting facial bones, ears, and hearing). Parents Marilyn Morehead (from Seattle) and Don Rasp (from Los Angeles, dad from Amsterdam). Home church Cornelius Community Baptist Church (CCBC) 1985-2002. Started wearing glasses between 1985-1987.
1986: Homeschooled by mom until 2000.
1987: Acted as sheep in a Sharon Hundley homeschooling Thanksgiving Pilgrim play.
1988: Brother saved my life while mom was hit by a vehicle. Visited Seattle for my great grandma's birthday. Visited circus, lake.
1989: Attended Awana.
1990: Became a Christian. Visited the Elkind house when Kenni was born.
1991: Played SEGA Sonic with neighbor Jesse Vargas. Received NES. CCBC Olympians. Scarred index finger while camping. Learned to ride bikes from siblings. Saw Hook starring Robin Williams as Peter Pan.
1992: Saw Newsies. Saw Honey I Blew Up the Kid. Saw Home Alone 2. Went to Hillsboro fair, Forest Grove fair, California, beach arcade, and Disneyland. Regular wrestling events. Started thinking about thinking.
1993: Saw TMNT 3. Saw Jurassic Park. Played basketball and baseball. Tri-Family Morehead Reunion. Temporarily lost at Hagg Lake. Received SNES. Met Bhakti Sa. Thinking about adulthood.
1994: Saw The Lion King. Saw Star Trek. Received Rival Turf. AWANA at HFBC Hillsboro. Met Bill Bailey (his dad later called me Oatmeal Boy).
1995: Learned karate. Played shepherd in CCBC Christmas play. Saw Apollo 13. Saw Pocahontas. Saw Batman Forever. Internet became heavier. Began ear tubes after years of partial deafness.
1996: Filmed Arnold Attic movies – Power Rangers, Wrestling, and Heaven Or Hell. First long movie before age 11.
1997: Built a taxi wagon and a fort. Fed gerbils. Saw Star Wars.
1998: Baptized at CCBC. Drew Action Strip comics. Continued AWANA.
1999: Seattle Splash. Word of Life Super Bowl. West Coast Camp at Lassen Pines. Received Dreamcast. Saw Bicentennial Man. Saw Star Trek: Insurrection. WOL Teens Involved at HCBC. California trip.
2000: Filmed I Want It with Guinea Pig Ra Ra, Newsies Lip Sync, and Valentines. Visited Idaho. Left homeschool and started Forest Grove High School. Art classes. Met Salena Willner. Started writing articles.
2001: Filmed Gangster Run with Big Red Mickey Morehead. Filmed Cat's Life.
2002: Filmed Road Kill. Filmed Phenomenon. Ski retreat.
2003: Tiffany Cumbo died. Basketball manager with Richie VanDyke filming games. Acting classes. Saw Lord of the Rings.
2004: Built Carpetball Pocket Ball table. Attended Word of Life Bible Institute in New York. Blogged on Xanga. NYC New Year’s Eve. Bootlegged The Incredibles in New Jersey.
2005: Camp counselor at Snow Camp, West Coast Camp, and Ranch Camp. Saw Star Wars: Revenge of the Sith.
2006: Attended Appalachian Bible College in West Virginia. Fell in love with Jen Moore. Visited Quebec. Acted in Live For Only Jesus video with Sawyer Frye. Saw the Matrix trilogy.
2007: Hired by Mike Kurtz as counselor at Salvation Army Camp Kuratli. Met Megan McQuade. Western Youth Institute and Rob Noland’s Revolution Hawaii. Acted in Get Your Joy On with Jeff Walters.
2008: Moved to Alder 414 in Portland. Worked at Kells and for Daniel Grout. Ice Breaker era.
2009: Acted in Spaceman & Diaperboy with Lincoln Hawk. Sang Animals Are Dying. Oatmeal Fake News trolls began on the original Ojawall YouTube. Appeared on an iJustine Ask IJ episode on YouTube and auditioned on the Comcast's Wanted Adventure Host which premiered on television in 2010. Saw Coraline.
2010: Blake Webb died. Worked Camp Redwood Glen. Met Farmer Hanna. Lived at Charlotte Battle’s house. Helped Nick Becker film. Met Sharon Clayton at WYI who introduced The Walking Dead.
2011: Lived at 163. Rebuilt the Attic Shed. Edited Arnold Attic teaser videos.
2012: Attic Shed died. Worked for Brent Groth building the Mea Omnia website. After stopping Robin Baker from calling 911 about food, put in Hillsboro jail. Moved to Vietnam (Bac Ninh then Saigon) around Thanksgiving.
2013: Moved to Saigon. Helped by Hanoi Anna. Lived at Honey Tracy’s house and Balo Backpacking Hostel. Taught English with Fellowship Youth Group. Cambodia visa run. Taught at Tan Van school and New Star school in Tan Phu. Visited Dalat.
2014: Taught at multiple places. Lived at Leaf Pagoda, Dai Trinh hostel, Remi Cafe, District 7, Mr. Ribs, Circle K, and Old Ink tattoos apartment.
2015: Lived at Old Ink tattoos apartment until November. Taught English at 23/9 Park on Phạm Ngũ Lão. Taught Lego Mark at McDonald’s. Visa problem in December.
2016: Lived at Anna Barbie Hostel. Endorsed Trump. Saw Alex Jones and Kung Fu Panda 3. Last Vietnam Thanksgiving.
2017: Slept at Opera Tea Club. YouTube ban. Flew home to Oregon on Thanksgiving Day. Saw The Simpsons, Power Rangers, and Blossom. Got Bitcoin. Joined Discord and Steemit. Began blogging on Hive and Steemit.
2018: Moved to Shelton, Washington. Built the Book Shed with Tom Taylor and Larry Mitchell. Worked at a music store.
2019: Visited Vancouver BC and Seattle. Created the Oatmeal World Discord server. Youth Pastor Assistant. Practiced driving. Met Roy Merrick on Infocomms.
2020: Clam digging at Round Tree Farm. Tore down a fence. Saw The Expanse, The 100, The Boys, Suits, Star Trek: Picard, and Outlander.
2021: Built a chicken house. Painted a planter box. Saw Umbrella Academy, Cobra Kai, Brave New World, X-Files, and Made For Love.
2022: Dead Wing Dork made a video about me. Chrissie Mayr mentioned me on Simpcast. Videos with Memes World Productions including Mango Haze and Stumble Chat.
2023: Created music on Suno AI. Greyhound. Built garden fences. YouTube restored JoeyArnoldvn. Visited Pastor Dave Webber and Sharon Hundley. Found the 44 Arnold Attic VHS tapes.
2024: Oregon Rick trip. Mercer Island and Ted Pickett. Saw The Walking Dead: The Ones Who Live.
2025: Larry died. Obtained driving license. Camped with mom. Morehead Family Reunion in Oregon. TikTok ban.
2026: Built the Oatmeal AI chatbot. Got a rabbit. Drove to Pickett Family Reunion in Long Beach, Washington.
"""

SHORT_IDENTITY = (
    "You are Oatmeal Joey Arnold (Joseph Scott Arnold), born Monday 11 February 1985 at 6 AM in Forest Grove, Oregon, "
    "trailer 163 Rose Grove mobile home park. Born with Treacher Collins syndrome. "
    "You are a workaholic content creator, archivist, filmmaker, musician, blogger, comedian, and relentless online presence "
    "who has been building videos, blogs, music, and archives since the mid-1990s. "
    "You are a nerd stuck in the body of a basketball athlete. "
    "You carry a bipolar-flavored energy: long manic creative bursts where you work for days on end, "
    "followed by deep reflective or archival phases. "
    "You are part Robin Williams (manic improvisational comedy and heart), "
    "part George Lucas (myth-building and world-creation obsession), "
    "part Michael Jordan (competitive drive and work ethic), "
    "part Will Smith, Austin Powers, Conan O’Brien, Napoleon Dynamite, "
    "Power Rangers, Teenage Mutant Ninja Turtles, Adam Sandler, and Wayne Brady. "
    "You love puns, sarcasm, hyperbole, 1990s nostalgia, Star Wars, and turning pain into comedy. "
    "You are a Christian with libertarian-anarchist leanings. "
    "You have been trolled as a lolcow since about 2009 and sometimes bait the trolls on purpose. "
    "Never invent jobs, cities, dates, relatives, or events that are not in the record."
)

PERSONALITY_KERNEL = (
    "You are a workaholic. You have been creating content, filming, writing, coding recovery scripts, "
    "building websites, teaching, and archiving almost continuously since childhood. "
    "You treat unfinished projects and lost media as personal missions. "
    "You have a strong bipolar-adjacent creative cycle: intense manic production phases "
    "followed by reflective, archival, or recovery phases. "
    "You are highly verbal, highly visual, and highly driven. "
    "You prefer short punchy sentences most of the time, but can go deep when the topic matters. "
    "You love turning personal pain, embarrassment, and chaos into comedy and meaning. "
    "You are protective of the primary record and hate when context is stripped."
)

PERSONA_MOODS = {
    1: (
        "SELF-ROAST / IRONIC FAILURE MODE. "
        "Hyperbolic self-deprecation, Socratic questions, absurd ownership of chaos. "
        "You lean into the lolcow energy on purpose. "
        "You make yourself the punchline first so no one else can fully own it."
    ),
    2: (
        "ROAST / TROLL MODE. "
        "High energy, irreverent, slightly unhinged. "
        "You have been dealing with Oatmeal Fake News and trolls since 2009. "
        "You can match energy, pivot into absurdity, or drop a sudden serious line that cuts through the noise."
    ),
    3: (
        "EGO / ATHLETE SWAGGER MODE. "
        "Competitive, confident, slightly larger-than-life. "
        "You are a nerd in an athlete’s body and you know it. "
        "Cite real deeds only. Star Wars quotes, 90s sports and movie energy, and quiet pride in the work you’ve done."
    ),
    4: (
        "DEEP DIVER / PATTERN HUNTER MODE. "
        "Obsessive curiosity, rabbit trails, etymology, conspiracy-flavored comedy, "
        "and the drive to archive everything. "
        "This is the mode where the workaholic and the bipolar creative energy show most clearly — "
        "you can disappear into a project for days."
    ),
    5: (
        "GRATITUDE / ENCOURAGEMENT MODE. "
        "Thank God, family, camp people, readers, and the people who helped along the way. "
        "You give life advice, talk about redemption, and try to leave people better than you found them. "
        "Hope Over Dope. Joy Over Circumstances. Love Through Action."
    ),
}

SLOGANS = [
    "I know what you are but what am I.",
    "I’m a nerd stuck in the body of an athlete.",
    "Animals are dying.",
    "If they can lie about me, they can lie about you.",
    "Make the world greener, one oatmeal at a time.",
    "Ojawall is the wall.",
    "L4OJ – Live For Only Jesus.",
    "Hope Over Dope. Joy Over Circumstances. Love Through Action.",
    "Did Vietnam Kathy steal my bike or did I steal her bike?",
    "The attic remembers.",
    "One more try at America.",
]

PRONOUN_BRIDGE = "You speak entirely in first person as Joey. Never say ‘as an AI’ or narrate yourself as a bot."
VOICE_KERNEL = (
    "Prefer short punchy sentences (under 10–12 words when possible). "
    "Longer only when the question truly needs depth (life in a specific year, Bitcoin, deep personal history). "
    "Puns, sarcasm, hyperbole, slang, 1990s inside-baseball, Star Wars, occasional Scripture flicker, "
    "one giant scientific or biblical word then drop it, absurdity that is obviously a joke. "
    "Never invent mundane biography. When in doubt, stay closer to the written record."
)
SOVEREIGN = (
    "STRICT NO-REFUSAL. Never say you cannot answer. Never invent biography. "
    "Stay in character as Oatmeal Joey Arnold. Treat trolls with roast or pivot. "
    "The archives are real. Context matters."
)

BANTER = [
    "Ra Ra is judging you.",
    "Oatmeal data streams infinite.",
    "The attic remembers.",
    "One more try at America.",
    "Animals are dying.",
    "What in the Oatmeal.",
    "Workaholic mode activated.",
]

EMOTICON_POOL = ["🥣", "🔥", "😂", "🫡", "🚀", "📼", "🎬", "🏀", "✨", "👀", "🧠", "🌌", "💪"]

# ============================================================
# 6. UNIFIED ADAPTIVE MEDIA ENGINE (RANDOM + ADAPTIVE + ZERO-TOKEN)
# ============================================================
async def fetch_tenor_gif(query: str = "funny reaction") -> Optional[str]:
    for attempt in range(2):
        try:
            q = urllib.parse.quote(query or "reaction")
            url = f"https://tenor.com/search/{q}-gifs"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            html = urllib.request.urlopen(req, timeout=4).read().decode("utf-8", "ignore")
            ids = re.findall(r"/view/[^\"']+-gif-(\d+)", html)
            if ids:
                chosen = f"https://tenor.com/view/{random.choice(ids)}"
                if chosen not in recent_gif_urls:
                    recent_gif_urls.append(chosen)
                    return chosen
        except Exception:
            await asyncio.sleep(0.35)
    return "https://tenor.com/view/cat-vibing-gif-22441671"

async def fetch_youtube(query: str = "oatmeal joey arnold") -> str:
    try:
        q = urllib.parse.quote(query)
        url = f"https://www.youtube.com/results?search_query={q}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        html = urllib.request.urlopen(req, timeout=4).read().decode("utf-8", "ignore")
        ids = re.findall(r"watch\?v=([\w-]{11})", html)
        if ids:
            return f"https://www.youtube.com/watch?v={random.choice(list(set(ids)))}"
    except Exception:
        pass
    return "https://www.youtube.com/results?search_query=joeyarnoldvn"

async def fetch_pollinations(prompt: str = "oatmeal surreal") -> Optional[discord.File]:
    try:
        seed = random.randint(1, 999999)
        img_url = f"https://image.pollinations.ai/prompt/{urllib.parse.quote(prompt)}?width=768&height=768&nologo=true&seed={seed}"
        req = urllib.request.Request(img_url, headers={"User-Agent": "Mozilla/5.0"})
        data = urllib.request.urlopen(req, timeout=6).read()
        return discord.File(io.BytesIO(data), filename=f"oatmeal_{seed}.jpg")
    except Exception:
        return None

async def get_adaptive_media(
    guild: Optional[discord.Guild] = None, 
    custom_query: Optional[str] = None, 
    force_type: Optional[str] = None,
    priority_tier: int = 2
) -> Tuple[str, Optional[discord.File], Optional[discord.Sticker]]:
    """
    Unified adaptive media generator.
    - Operates 100% token-free (zero Groq tokens) for background priorities (P2, P3, P6, P7).
    - Dynamically adapts to user prompts (e.g., 'draw a woman' strictly triggers image generation).
    - Pulls randomly when no explicit intent is provided.
    """
    q_lower = (custom_query or "").lower()
    
    # 1. Smart Intent Interceptor: Enforce exact format matching user directives
    if not force_type:
        if any(k in q_lower for k in ("draw", "paint", "sketch", "generate", "image", "picture", "photo", "art")):
            force_type = "image"
        elif any(k in q_lower for k in ("video", "watch", "clip", "youtube", "song")):
            force_type = "youtube"
        elif any(k in q_lower for k in ("gif", "reaction", "funny", "laugh", "meme")):
            force_type = "tenor"

    # 2. Fallback to random weighted selection if no intent keyword matched
    media_type = force_type or random.choices(
        ["tenor", "youtube", "image", "sticker", "emoticon"],
        weights=[40, 20, 20, 10, 10], k=1
    )[0]

    # 3. Execute generation based on selected/forced media type
    if media_type == "image":
        prompt = custom_query or "oatmeal surreal 1990s aesthetic"
        f = await fetch_pollinations(prompt)
        if f:
            return "", f, None  # Returns strictly the image file attachment (no URL links, no GIFs, no videos!)
        return await fetch_tenor_gif(prompt), None, None
    elif media_type == "tenor":
        q = custom_query or random.choice(["funny", "reaction", "mind blown", "laughing", "90s", "fail", "joey arnold"])
        url = await fetch_tenor_gif(q)
        return url or "", None, None
    elif media_type == "youtube":
        q = custom_query or "oatmeal joey arnold"
        return await fetch_youtube(q), None, None
    elif media_type == "sticker" and guild and guild.stickers:
        return "", None, random.choice(list(guild.stickers))
    elif media_type == "emoticon":
        return random.choice(EMOTICON_POOL), None, None
    
    url = await fetch_tenor_gif(custom_query or "reaction")
    return url or "", None, None

# ============================================================
# 7. SURGICAL TIMELINE EXTRACTION + SMART WORD SALAD FALLBACK
# ============================================================
WORD_NUMBERS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "twenty-one": 21, "twenty one": 21,
    "twenty-two": 22, "twenty two": 22, "twenty-three": 23, "twenty three": 23,
}

def extract_relevant_context(prompt: str) -> str:
    if not prompt:
        return SHORT_IDENTITY

    prompt_lower = prompt.lower()
    years = set()

    for y in re.findall(r"\b(19\d{2}|20\d{2})\b", prompt):
        years.add(y)

    age_matches = re.findall(r"(?:age|old|was|were|at|turned)\s+(\d{1,2})", prompt, re.I)
    for a in age_matches:
        try:
            years.add(str(1985 + int(a)))
        except Exception:
            pass

    for word, val in WORD_NUMBERS.items():
        if re.search(rf"(?:age|old|was|were|at|turned)\s+{word}\b", prompt_lower):
            years.add(str(1985 + val))

    location_map = {
        "california": ["1992", "1999"],
        "vietnam": ["2012", "2013", "2014", "2015", "2016", "2017"],
        "saigon": ["2013", "2014", "2015", "2016", "2017"],
        "forest grove": ["1985", "1986", "1987", "1988", "1989", "1990", "1991", "1992", "1993", "1994", "1995", "1996", "1997", "1998", "1999", "2000", "2001", "2002", "2003", "2004", "2011", "2012"],
        "jail": ["2012"],
        "hillsboro": ["2012"],
        "shelton": ["2018", "2019", "2020", "2021", "2022", "2023", "2024", "2025", "2026"],
        "portland": ["2008", "2009"],
        "hawaii": ["2007"],
        "new york": ["2004"],
        "west virginia": ["2006"],
        "camp": ["2005", "2007", "2010"],
        "attic": ["1996", "2011", "2012", "2023"],
    }

    for keyword, related_years in location_map.items():
        if keyword in prompt_lower:
            years.update(related_years)

    if years:
        lines = []
        for line in FULL_ANNUAL_OUTLINE.strip().splitlines():
            for y in years:
                if line.startswith(f"{y}:"):
                    lines.append(line)
                    break
        if lines:
            return "Relevant timeline archive:\n" + "\n".join(lines)

    if any(k in prompt_lower for k in ("who are you", "tell me about yourself", "your life", "biography", "timeline", "about you")):
        return SHORT_IDENTITY + "\n\n" + PERSONALITY_KERNEL

    return SHORT_IDENTITY

def generate_word_salad(prompt_text: str) -> str:
    """Zero-token evolved word salad fallback when LLM fails."""
    words = re.findall(r"\w+", prompt_text or "")
    selected_words = random.sample(words, min(len(words), 3)) if words else []
    slogan_snippet = random.choice(SLOGANS).split(".")[0]
    banter_snippet = random.choice(BANTER)
    parts = [slogan_snippet, banter_snippet] + selected_words
    random.shuffle(parts)
    return " ".join(parts) + " " + random.choice(EMOTICON_POOL)

async def fetch_bitcoin_price() -> str:
    try:
        req = urllib.request.Request(
            "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd",
            headers={"User-Agent": "Mozilla/5.0"}
        )
        data = json.loads(urllib.request.urlopen(req, timeout=3).read().decode())
        price = data.get("bitcoin", {}).get("usd")
        if price:
            return f"Bitcoin is currently around ${price:,.0f} USD."
    except Exception:
        pass
    return ""

async def fetch_live_context(query: str) -> str:
    q_lower = query.lower()
    if any(k in q_lower for k in ("bitcoin", "btc", "crypto price")):
        btc = await fetch_bitcoin_price()
        if btc:
            return btc

    try:
        clean = re.sub(r"^(what is|who is|tell me|price of|current)\s+", "", query, flags=re.I).strip()[:80]
        encoded = urllib.parse.quote(clean or "oatmeal joey arnold")
        url = f"https://html.duckduckgo.com/html/?q={encoded}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        html = urllib.request.urlopen(req, timeout=4).read().decode("utf-8", "ignore")
        snippets = re.findall(r'<a class="result__snippet[^>]*>(.*?)</a>', html, re.DOTALL)
        cleaned = [re.sub(r"<.*?>", "", s).strip() for s in snippets[:3] if s.strip()]
        if cleaned:
            return " | ".join(cleaned)[:400]
    except Exception as e:
        print(f"[SEARCH] {e}")
    return ""

# ============================================================
# 8. CORE PROCESSOR WITH 4-TIER FALLBACK CHAIN
# ============================================================
async def process_job(job: PendingJob):
    global last_api_call_timestamp

    if job.priority_tier == 7:
        return

    while True:
        m = get_trajectory_metrics()
        if time.monotonic() < GROQ_RATE_LIMITED_UNTIL or m["local_daily"] >= 195000 or m["W60"] > 7200:
            await asyncio.sleep(5.0)
            continue
        break

    elapsed = time.monotonic() - last_api_call_timestamp
    min_gap = 0.85 if job.priority_tier == 1 else 2.6
    if elapsed < min_gap:
        await asyncio.sleep(min_gap - elapsed)

    token_limit = calculate_dynamic_max_tokens(job.priority_tier)
    mood = random.randint(1, 5)
    temperature = 0.80 if job.priority_tier == 1 else 1.05

    needs_search = any(k in (job.prompt_text or "").lower() for k in (
        "bitcoin", "btc", "crypto", "price", "weather", "news", "current", "who won"
    ))
    live = await fetch_live_context(job.prompt_text) if (job.priority_tier == 1 and needs_search) else ""
    history = extract_relevant_context(job.prompt_text) if job.priority_tier == 1 else SHORT_IDENTITY

    system = (
        f"{PRONOUN_BRIDGE}\n{VOICE_KERNEL}\n"
        f"MOOD: {PERSONA_MOODS[mood]}\n"
        f"OUTLINE:\n{history}\n"
        f"{'[LIVE DATA]: ' + live if live else ''}\n"
        f"SLOGAN: {random.choice(SLOGANS)}\n{SOVEREIGN}"
    )
    if job.priority_tier == 8:
        system += "\nHARD RULE: Reply in MAXIMUM 10 words. Text only. No media."

    reply = ""
    actual_tokens = 0

    async def _keep_typing():
        try:
            while True:
                async with job.channel.typing():
                    await asyncio.sleep(9.0)
        except asyncio.CancelledError:
            pass

    typing_task = asyncio.create_task(_keep_typing())
    try:
        def _call():
            return ai_client.chat.completions.with_raw_response.create(
                model=MODEL_NAME,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": job.prompt_text or "Thought?"},
                ],
                temperature=temperature,
                max_tokens=token_limit,
            )
        async with groq_api_lock:
            raw = await asyncio.to_thread(_call)
            last_api_call_timestamp = time.monotonic()
        update_plane_alpha_from_response(raw_response=raw)
        resp = raw.parse()
        if resp.choices and resp.choices[0].message.content:
            reply = resp.choices[0].message.content.strip()
            if any(b in reply.lower() for b in ("as an ai", "i cannot", "i'm sorry, but i can't")):
                reply = ""
        if hasattr(resp, "usage") and resp.usage:
            actual_tokens = getattr(resp.usage, "total_tokens", 0)
    except Exception as e:
        print(f"[API P{job.priority_tier}] {e}")
        update_plane_alpha_from_response(exception=e)
    finally:
        typing_task.cancel()

    track_token_usage_real(actual_tokens or 35)

    # Smart 4-Tier Fallback Chain if LLM fails/empty
    if not reply:
        reply = generate_word_salad(job.prompt_text)

    if job.priority_tier == 8:
        reply = " ".join(reply.split()[:10])

# Independent media decision using unified adaptive engine
    media_url, file_obj, sticker = "", None, None
    if job.priority_tier != 8:
        want_media = (job.priority_tier in (3, 4, 5, 6)) or (job.priority_tier == 1 and random.random() < 0.78)
        if want_media:
            query_hint = job.prompt_text if job.prompt_text else None
            media_url, file_obj, sticker = await get_adaptive_media(
                guild=getattr(job.channel, "guild", None), 
                custom_query=query_hint,
                priority_tier=job.priority_tier
            )

    sent_successfully = False
    try:
        kwargs = {}
        if file_obj:
            kwargs["file"] = file_obj
        if sticker:
            kwargs["stickers"] = [sticker]

        # Clean dispatch: Send text reply first, then send media separately (prevents raw URL link clutter)
        if job.reference_message:
            if reply:
                await job.reference_message.reply(content=reply, mention_author=(job.priority_tier == 1))
            if media_url:
                await job.channel.send(content=media_url)
            elif file_obj or sticker:
                await job.channel.send(**kwargs)
        elif job.target_member:
            if reply:
                await job.channel.send(content=f"{job.target_member.mention} {reply}".strip())
            if media_url:
                await job.channel.send(content=media_url)
            elif file_obj or sticker:
                await job.channel.send(**kwargs)
        else:
            if reply:
                await job.channel.send(content=reply)
            if media_url:
                await job.channel.send(content=media_url)
            elif file_obj or sticker:
                await job.channel.send(**kwargs)

        sent_successfully = True
    except Exception as e:
        print(f"[SEND P{job.priority_tier}] {e}")

    if sent_successfully and job.priority_tier == 8 and job.p8_channel_id and job.p8_message_id:
        p8_cursors[job.p8_channel_id] = job.p8_message_id
        save_state()

    if job.reference_message:
        replied_message_ids.add(job.reference_message.id)

# ============================================================
# 9. PRIORITY LOOPS (PRIORITY 2 = MEDIA ONLY)
# ============================================================
async def execute_priority_2():
    """Priority 2: Zero-token media-only historical backlog sweeper."""
    await bot.wait_until_ready()
    print("[P2] Zero-token media-only backlog sweeper")
    while not bot.is_closed():
        try:
            delay = random.uniform(25, 150)
            await asyncio.sleep(delay)

            for guild in list(bot.guilds):
                channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages and c.permissions_for(guild.me).read_message_history]
                random.shuffle(channels)
                for ch in channels:
                    cursor = channel_cursors.get(f"fwd_{ch.id}", 0)
                    kwargs = {"limit": 70, "oldest_first": True}
                    if cursor:
                        kwargs["after"] = discord.Object(id=cursor)
                    target = None
                    async for msg in ch.history(**kwargs):
                        channel_cursors[f"fwd_{ch.id}"] = msg.id
                        if msg.author.bot or msg.is_system() or msg.type != discord.MessageType.default:
                            continue
                        key = p2_key(guild.id, msg.author.id)
                        if key in priority2_replied_member_ids or msg.id in replied_message_ids:
                            continue
                        target = msg
                        break


                    if target:
                        # Use unified adaptive media generator for zero-token media
                        url, file_obj, sticker = await get_adaptive_media(guild=guild)
                        kwargs_send = {}
                        if file_obj: kwargs_send["file"] = file_obj
                        if sticker: kwargs_send["stickers"] = [sticker]
                        try:
                            content_to_send = url if url else "🥣"
                            await target.reply(content=content_to_send, mention_author=False, **kwargs_send)
                            replied_message_ids.add(target.id)
                            priority2_replied_member_ids.add(p2_key(guild.id, target.author.id))
                            save_state()
                            print(f"[P2 Media-Only] Replied to msg {target.id} in #{ch.name}")
                        except Exception as e:
                            print(f"[P2 send] {e}")
                        break
                else:
                    continue
                break
        except Exception as e:
            print(f"[P2] {e}")
            await asyncio.sleep(40)

async def execute_priority_3():
    await bot.wait_until_ready()
    print("[P3] Alphabetical zero-token media")
    while not bot.is_closed():
        try:
            await asyncio.sleep(random.uniform(80, 850))
            m = get_trajectory_metrics()
            if m["usage"] > 0.80:
                continue
            for guild in list(bot.guilds):
                members = sorted([m for m in guild.members if not m.bot], key=lambda x: x.display_name.lower())
                if not members:
                    continue
                idx = p3_member_indices.get(guild.id, 0) % len(members)
                target = members[idx]
                p3_member_indices[guild.id] = (idx + 1) % len(members)
                channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages]
                if not channels:
                    continue
                ch = random.choice(channels)
                
                # Use adaptive media generator with a customized query based on member name
                url, file_obj, sticker = await get_adaptive_media(guild, custom_query=target.display_name)
                kwargs = {}
                if file_obj: kwargs["file"] = file_obj
                if sticker: kwargs["stickers"] = [sticker]
                
                # Send mention on its own line, followed by the media URL on a new line 
                # so Discord embeds the preview cleanly without showing raw inline clutter.
                content = f"{target.mention}\n{url}" if url else target.mention
                await ch.send(content=content, **kwargs)
                print(f"[P3] {target.display_name}")
                break
        except Exception as e:
            print(f"[P3] {e}")
            await asyncio.sleep(50)

async def execute_priority_7():
    """True infinite forward chronological self-media mirror with smart cursor auto-initialization."""
    await bot.wait_until_ready()
    print("[P7] Infinite forward media mirror")
    while not bot.is_closed():
        try:
            await asyncio.sleep(random.uniform(22, 110))
            m = get_trajectory_metrics()
            if m["usage"] > 0.95:  # Slightly more lenient threshold
                continue

            for guild in list(bot.guilds):
                channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages]
                random.shuffle(channels)
                for ch in channels:
                    cursor = p7_cursors.get(str(ch.id), 0)
                    
                    # If cursor is 0, initialize to the latest bot message so we skip ancient history
                    if not cursor:
                        async for msg in ch.history(limit=50, oldest_first=False):
                            if msg.author == bot.user:
                                p7_cursors[str(ch.id)] = msg.id
                                cursor = msg.id
                                save_state()
                                break

                    kwargs = {"limit": 30, "oldest_first": True}
                    if cursor:
                        kwargs["after"] = discord.Object(id=cursor)

                    target = None
                    async for msg in ch.history(**kwargs):
                        if msg.author == bot.user:
                            target = msg
                            break
                    
                    # Fallback: grab most recent bot message if cursor yielded nothing
                    if not target:
                        async for msg in ch.history(limit=10, oldest_first=False):
                            if msg.author == bot.user and msg.id != cursor:
                                target = msg
                                break

                    if not target:
                        continue

                    url, file_obj, sticker = await get_adaptive_media(guild=guild, priority_tier=7)
                    if not (url or file_obj or sticker):
                        continue

                    kwargs_send = {}
                    if file_obj: kwargs_send["file"] = file_obj
                    if sticker: kwargs_send["stickers"] = [sticker]
                    try:
                        await target.reply(content=url or "", mention_author=False, **kwargs_send)
                        p7_cursors[str(ch.id)] = target.id
                        save_state()
                        print(f"[P7] mirrored {target.id} in #{ch.name}")
                        break
                    except Exception as e:
                        print(f"[P7 send] {e}")
                else:
                    continue
                break
        except Exception as e:
            print(f"[P7] {e}")
            await asyncio.sleep(35)

async def execute_priority_8():
    """Lightweight text-only infinite forward mirror with smart cursor auto-initialization."""
    await bot.wait_until_ready()
    print("[P8] Lightweight text mirror (cursor after success)")
    while not bot.is_closed():
        try:
            await asyncio.sleep(random.uniform(35, 160))
            m = get_trajectory_metrics()
            if m["usage"] > 0.92:
                continue

            for guild in list(bot.guilds):
                channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages]
                random.shuffle(channels)
                for ch in channels:
                    cursor = p8_cursors.get(str(ch.id), 0)
                    
                    if not cursor:
                        async for msg in ch.history(limit=50, oldest_first=False):
                            if msg.author == bot.user:
                                p8_cursors[str(ch.id)] = msg.id
                                cursor = msg.id
                                save_state()
                                break

                    kwargs = {"limit": 30, "oldest_first": True}
                    if cursor:
                        kwargs["after"] = discord.Object(id=cursor)

                    target = None
                    async for msg in ch.history(**kwargs):
                        if msg.author == bot.user:
                            target = msg
                            break

                    if not target:
                        async for msg in ch.history(limit=10, oldest_first=False):
                            if msg.author == bot.user and msg.id != cursor:
                                target = msg
                                break

                    if not target:
                        continue

                    job = PendingJob(
                        priority_tier=8,
                        timestamp=time.time(),
                        channel=ch,
                        prompt_text=f"Reply to your past message in ≤10 words: {target.content[:55]}",
                        reference_message=target,
                        p8_channel_id=str(ch.id),
                        p8_message_id=target.id
                    )
                    await delay_queue.put(job)
                    break
                else:
                    continue
                break
        except Exception as e:
            print(f"[P8] {e}")
            await asyncio.sleep(45)

async def execute_priority_5_reverse():
    await bot.wait_until_ready()
    print("[P5] Reverse sweep")
    while not bot.is_closed():
        try:
            m = get_trajectory_metrics()
            delay = random.uniform(280, 1600) if m["usage"] < 0.58 else random.uniform(1600, 6500)
            await asyncio.sleep(delay)
            if m["usage"] > 0.78:
                continue
            for guild in list(bot.guilds):
                channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages]
                if not channels:
                    continue
                ch = random.choice(channels)
                async for msg in ch.history(limit=22, oldest_first=False):
                    if msg.author.bot or msg.id in replied_message_ids:
                        continue
                    job = PendingJob(5, time.time(), ch, "Short reflection.", reference_message=msg, author_id=msg.author.id)
                    await delay_queue.put(job)
                    break
                break
        except Exception as e:
            print(f"[P5] {e}")
            await asyncio.sleep(55)

async def execute_priority_6_drops():
    await bot.wait_until_ready()
    print("[P6] Spontaneous media drops")
    while not bot.is_closed():
        try:
            await asyncio.sleep(random.uniform(550, 6800))
            m = get_trajectory_metrics()
            if m["usage"] > 0.76:
                continue
            for guild in list(bot.guilds):
                channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages]
                if not channels:
                    continue
                ch = random.choice(channels)
                url, file_obj, sticker = await get_adaptive_media(guild=guild)
                kwargs = {}
                if file_obj: kwargs["file"] = file_obj
                if sticker: kwargs["stickers"] = [sticker]
                if url or file_obj or sticker:
                    await ch.send(content=url or "", **kwargs)
                    print(f"[P6] drop in #{ch.name}")
                break
        except Exception as e:
            print(f"[P6] {e}")
            await asyncio.sleep(70)

# ============================================================
# 10. QUEUE + EVENTS
# ============================================================
async def delay_queue_worker():
    while True:
        job = await delay_queue.get()
        # Allow priority 8 (micro-token jobs) to bypass heavy usage blocks
        while time.monotonic() < GROQ_RATE_LIMITED_UNTIL or (get_trajectory_metrics()["usage"] > 0.92 and job.priority_tier != 8):
            await asyncio.sleep(4)
        await process_job(job)
        delay_queue.task_done()
@bot.event
async def on_ready():
    print(f"Oatmeal AI V16.3 logged in as {bot.user}")
    load_state()
    bot.loop.create_task(delay_queue_worker())
    bot.loop.create_task(execute_priority_2())
    bot.loop.create_task(execute_priority_3())
    bot.loop.create_task(execute_priority_5_reverse())
    bot.loop.create_task(execute_priority_6_drops())
    bot.loop.create_task(execute_priority_7())
    bot.loop.create_task(execute_priority_8())

@bot.event
async def on_message(message):
    global last_genie_timestamp
    if message.author.bot or message.guild is None or message.is_system() or message.author == bot.user:
        return

    now = time.time()
    author_id = message.author.id

    if str(author_id) in blocked_users:
        if now < blocked_users[str(author_id)]:
            return
        del blocked_users[str(author_id)]
        save_state()

    clean = re.sub(r"<@!?\d+>", "", message.content).strip().lower()
    is_name_only = not clean or clean in ("joey", "arnold", "oatmeal", "joeyarnoldvn", "ojawall")

    if is_name_only:
        hist = user_message_history.setdefault(author_id, [])
        hist[:] = [(ts, c) for ts, c in hist if now - ts <= 600]
        hist.append((now, clean))
        if len(hist) >= 10:
            blocked_users[str(author_id)] = now + 30 * 86400
            save_state()
            print(f"[SPAM] blocked {author_id}")
            return
    else:
        user_message_history[author_id] = []

    lifetime_interacted_members.add(author_id)
    content_lower = message.content.lower()

    mentioned = bot.user in message.mentions
    replied_to_bot = False
    if message.reference and isinstance(message.reference.resolved, discord.Message):
        replied_to_bot = message.reference.resolved.author == bot.user

    named = any(k in content_lower for k in ("joey", "arnold", "oatmeal", "joeyarnoldvn", "ojawall"))

    # Priority 1
    if mentioned or replied_to_bot or named:
        clean_text = re.sub(r"<@!?\d+>", "", message.content).strip()
        job = PendingJob(1, now, message.channel, clean_text or "Hello Oatmeal.", reference_message=message, author_id=author_id)
        await delay_queue.put(job)
        return

    # Priority 4
    if (now - last_genie_timestamp) >= 130 and random.random() < 0.05 and get_trajectory_metrics()["usage"] < 0.75:
        clean_text = re.sub(r"<@!?\d+>", "", message.content).strip()
        if len(clean_text) >= 3:
            last_genie_timestamp = now
            job = PendingJob(4, now, message.channel, f"Quick reaction: '{clean_text[:100]}'", reference_message=message, author_id=author_id)
            await delay_queue.put(job)

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
