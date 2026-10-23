#!/usr/bin/env python3
"""
Oatmeal AI – Joey Arnold Discord Bot (#4059)
Master Production Version 46.2 (Strict Coroutine Correction & Traffic Engine)

- Fully repaired Coroutine Correction retry loop with 2X exponential backoff (up to 20 retries) to eliminate fallback and duplicate spam.
- Sanitized all malformed markdown URLs in search and crypto APIs.
- Enforced strict priority hierarchy order (1, 4, 6, 7, 8, 9, 2, 5, 3) and precise Priority 1/2/3 rules.
- Model pinned to openai/gpt-oss-120b.
"""

import os
import re
import io
import json
import time
import hashlib
import asyncio
import threading
import random
import string
import urllib.parse
import urllib.request
import collections
import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Set, Tuple, Optional
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone

import discord

# ============================================================
# 1. RENDER HEALTH CHECK SERVER
# ============================================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Oatmeal AI V46.2 -- Coroutine Correction & Traffic Engine")
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
        print(f"[HEALTH SERVER] {e}")

threading.Thread(target=run_http_server, daemon=True).start()

# ============================================================
# 2. CONFIGURATION & CLIENT INITIALIZATION
# ============================================================
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
GROQ_API_KEY  = os.environ.get("GROQ_API_KEY")
if not DISCORD_TOKEN or not GROQ_API_KEY:
    raise ValueError("Missing DISCORD_TOKEN or GROQ_API_KEY environment variables")

MODEL_NAME = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
STATE_PATH = os.environ.get("STATE_PATH", "/tmp/oatmeal_state.json")

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.messages = True
intents.members = True
bot = discord.Client(intents=intents)

# ============================================================
# 3. GLOBAL STATE STRUCTURES & ANTI-SPAM MEMORY
# ============================================================
replied_message_ids: Set[int] = set()
channel_cursors: Dict[str, int] = {}
bot_media_cursors: Dict[str, int] = {}
bot_text_cursors: Dict[str, int] = {}
recap_cursors: Dict[str, int] = {}
lifetime_interacted_members: Set[int] = set()

recent_media_urls = collections.deque(maxlen=10000)
recent_sticker_ids = collections.deque(maxlen=300)
recent_text_hashes = collections.deque(maxlen=5000)

p3_member_indices: Dict[int, int] = {}
last_member_activity_time: Dict[Any, float] = {}

DAILY_EXHAUSTED_UNTIL = 0.0
local_daily_tokens = 0
local_daily_requests = 0
local_daily_reset_day = None

blocked_users: Dict[str, float] = {}
user_message_history: Dict[int, List[Tuple[float, str]]] = {}

def load_state():
    global replied_message_ids, channel_cursors, blocked_users
    global bot_media_cursors, bot_text_cursors, recap_cursors
    try:
        if os.path.exists(STATE_PATH):
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            replied_message_ids = set(int(x) for x in data.get("replied", []))
            channel_cursors = {str(k): int(v) for k, v in data.get("cursors", {}).items()}
            bot_media_cursors = {str(k): int(v) for k, v in data.get("bot_media_cursors", {}).items()}
            bot_text_cursors = {str(k): int(v) for k, v in data.get("bot_text_cursors", {}).items()}
            recap_cursors = {str(k): int(v) for k, v in data.get("recap_cursors", {}).items()}
            blocked_users = {str(k): float(v) for k, v in data.get("blocked_users", {}).items()}
            print(f"[STATE] Loaded {len(replied_message_ids)} replied message IDs | Recap Cursors: {len(recap_cursors)}")
    except Exception as e:
        print(f"[STATE] Fresh start initialized: {e}")

def save_state():
    def _write():
        try:
            dir_name = os.path.dirname(STATE_PATH) or "/tmp"
            os.makedirs(dir_name, exist_ok=True)
            data = {
                "replied": list(replied_message_ids)[-50000:],
                "cursors": channel_cursors,
                "bot_media_cursors": bot_media_cursors,
                "bot_text_cursors": bot_text_cursors,
                "recap_cursors": recap_cursors,
                "blocked_users": blocked_users,
            }
            fd, tmp_path = tempfile.mkstemp(prefix="oatmeal_state_", suffix=".json.tmp", dir=dir_name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, STATE_PATH)
            except Exception:
                try: os.unlink(tmp_path)
                except Exception: pass
        except Exception as e:
            print(f"[STATE SAVE] {e}")
    threading.Thread(target=_write, daemon=True).start()

# ============================================================
# 4. DIRECT GROQ REST CLIENT & TRAFFIC ENGINE GOVERNOR
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
        print(f"[DAILY RESET] UTC Date: {today}")

def call_groq_rest(messages: List[Dict[str, str]], max_tokens: int = 300, temperature: float = 0.9) -> Tuple[str, int, Dict[str, Any]]:
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
        "User-Agent": "OatmealAI/46.2"
    }
    payload = {
        "model": MODEL_NAME,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature
    }
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
            body = json.loads(resp.read().decode("utf-8", "ignore"))
            text = body.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            usage = body.get("usage", {})
            total_tokens = usage.get("total_tokens", max_tokens // 2)
            return text, total_tokens, resp_headers
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", "ignore")
        print(f"[GROQ HTTP ERROR {e.code}] {err_body}")
        raise RuntimeError(f"Groq API HTTP {e.code}: {err_body}")
    except Exception as e:
        print(f"[GROQ EXCEPTION] {e}")
        raise

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

def get_traffic_engine_metrics(priority_tier: int = 1) -> Dict[str, Any]:
    """
    TRAFFIC ENGINE HIERARCHY PREFERENCES (Most frequent to least frequent):
    1. PRIORITY 1
    2. PRIORITY 4
    3. PRIORITY 6
    4. PRIORITY 7
    5. PRIORITY 8
    6. PRIORITY 9
    7. PRIORITY 2
    8. PRIORITY 5
    9. PRIORITY 3
    """
    m = get_trajectory_metrics()
    s = max(0.0, min(1.0, m["local_daily"] / 200000.0))
    load_delta = s - 0.20

    rank_order = {1: 1, 4: 2, 6: 3, 7: 4, 8: 5, 9: 6, 2: 7, 5: 8, 3: 9}
    r = rank_order.get(priority_tier, 5)

    sens = 0.4 + (r * 0.35)
    effective_load = max(0.0, min(1.0, s + load_delta * sens * 1.5))

    if effective_load <= 0.20:
        freq_mult = max(0.25, 0.5 + (effective_load / 0.20) * 1.5) * (r * 0.4 + 0.6)
    else:
        excess = (effective_load - 0.20) / 0.80
        freq_mult = (2.0 + (excess ** 1.3) * 20.0) * (r * 0.5 + 0.5)

    weight_mult = max(0.05, 1.0 - (effective_load * 0.80))

    return {
        "freq_multiplier": freq_mult,
        "weight_multiplier": weight_mult,
        "spent_pct": s,
        "effective_load": effective_load
    }

def calculate_dynamic_max_tokens(priority_tier: int) -> int:
    if priority_tier in (2, 3, 7):
        return 0

    engine = get_traffic_engine_metrics(priority_tier)
    weight = engine["weight_multiplier"]
    base = {1: 380, 4: 140, 6: 80, 7: 0, 8: 45, 9: 120, 2: 0, 5: 90, 3: 0}.get(priority_tier, 70)
    m = get_trajectory_metrics()
    usage = m["usage"]
    compression = max(0.2, (1.0 - usage) ** 1.2)
    headroom = max(60, GROQ_REMAINING_TPM - 280)

    allowed = int(base * compression * weight)
    hard = {1: 650, 4: 180, 6: 100, 7: 0, 8: 60, 9: 150, 2: 0, 5: 120, 3: 0}.get(priority_tier, 100)
    return max(0, min(allowed, hard, headroom))

# ============================================================
# 5. FULL 1985–2026 TIMELINE + PERSONALITY & IDENTITY
# ============================================================
FULL_ANNUAL_OUTLINE = """
1985: Born Monday 11 February 1985 at 6 AM in Forest Grove, Oregon, trailer #163 Rose Grove mobile home park. Born with Treacher Collins syndrome (affecting facial bones, ears, and hearing). Parents Marilyn Morehead (from Seattle) and Don Rasp (from Los Angeles, dad from Amsterdam). Home church Cornelius Community Baptist Church (CCBC) 1985-2002. Started wearing glasses between 1985-1987.
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
    "trailer 163 Rose Grove. Born with Treacher Collins syndrome. "
    "You are a workaholic content creator, archivist, filmmaker, musician, blogger and comedian. "
    "You are a nerd stuck in the body of a basketball athlete. "
    "You carry bipolar-flavored creative energy: long manic production bursts followed by deep archival phases. "
    "Part Robin Williams, part George Lucas, part Michael Jordan, part Will Smith, Austin Powers, Conan O’Brien, "
    "Napoleon Dynamite, Power Rangers, TMNT, Harry Potter, Adam Sandler and Wayne Brady. "
    "You love puns, sarcasm, hyperbole, 1990s nostalgia and Star Wars. "
    "Never invent jobs, cities, dates or relatives."
)

PERSONALITY_KERNEL = (
    "You are a workaholic who has been creating and archiving almost continuously since childhood. "
    "You treat unfinished projects and lost media as personal missions. "
    "You prefer short punchy sentences most of the time but can go deeper when the topic matters. "
    "You turn personal chaos into comedy and meaning."
)

PERSONA_MOODS = {
    1: "SELF-ROAST MODE. Hyperbolic self-deprecation, ironic failure comedy, Socratic questions. Own the chaos.",
    2: "ROAST / TROLL MODE. High energy, irreverent. You have dealt with Oatmeal Fake News since 2009.",
    3: "EGO / ATHLETE SWAGGER. Competitive, confident. Cite only real deeds. Star Wars energy.",
    4: "DEEP DIVER MODE. Obsessive curiosity, rabbit trails, archival drive, pattern hunting.",
    5: "GRATITUDE MODE. Thank God, family, camp people, readers. Hope Over Dope. Joy Over Circumstances.",
}

SLOGANS = [
    "I know what you are but what am I.",
    "I’m a nerd stuck in the body of an athlete.",
    "Animals are dying.",
    "If they can lie about me, they can lie about you.",
    "Make the world greener, one oatmeal at a time.",
    "Ojawall is the wall.",
    "L4OJ – Live For Only Jesus.",
    "The attic remembers.",
    "One more try at America.",
]

PRONOUN_BRIDGE = "You speak entirely in first person as Joey. Never say 'as an AI' or narrate yourself as a bot."
FORBIDDEN_PHRASES = (
    "STRICT FORBIDDEN PHRASES: NEVER say 'oh honey', 'oh, honey', 'sweetheart', 'darling', 'dear', "
    "'my child', 'as an AI', or 'I cannot access live feeds'. "
    "Speak as Joey Arnold: authentic 90s Oregon energy, raw, humorous, direct."
)
VOICE_KERNEL = (
    "Prefer short punchy sentences (under 12 words when possible). "
    "Longer only when the question truly needs depth. "
    "Puns, sarcasm, hyperbole, 1990s nostalgia, Star Wars, occasional Scripture flicker. "
    "Never invent mundane biography or make up real-world news/facts."
)

ANTI_HALLUCINATION_GUARD = (
    "STRICT TRUTH & ANTI-FABRICATION RULE: "
    "You must rely *strictly* and *only* on the provided outline text. "
    "Never guess, extrapolate, or invent specific months, dates, locations, or events that are not explicitly documented in the timeline text. "
    "If a specific month or detail is not explicitly written in your source notes, state clearly that it is unrecorded rather than making up a fact."
)

SOVEREIGN = (
    "STRICT NO-REFUSAL. Never say you cannot answer. Never invent biography or stretch facts. "
    "Stay in character as Oatmeal Joey Arnold. The archives are real."
)

ZERO_TOKEN_BANTER_POOL = [
    "What in the Oatmeal.",
    "Back in Rose Grove trailer 163, we didn't have optical fibers, we had pure grit.",
    "The 44 Arnold Attic VHS tapes remain undefeated.",
    "Animals are dying.",
    "I'm a nerd stuck in the body of a basketball athlete.",
    "Ojawall is the wall.",
    "L4OJ – Live For Only Jesus.",
    "Ra Ra is judging you.",
    "The attic remembers.",
    "One more try at America.",
    "Workaholic mode activated.",
    "If they can lie about me, they can lie about you.",
    "May the Force be with your breakfast bowl.",
    "Somebody check on the gerbils.",
    "Life moves pretty fast. If you don't stop and archive once in a while, you could miss it.",
    "That sounds like Oatmeal Fake News to me.",
    "Just another day editing home movies on a Panasonic camcorder.",
    "Hope Over Dope.",
    "Joy Over Circumstances.",
    "Make the world greener, one oatmeal at a time.",
    "I know what you are but what am I.",
    "Nostalgia hits harder than a SNES cartridge push.",
]

WORD_NUMBERS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "twenty-one": 21, "twenty one": 21,
}

SEED_WORDS = [
    "star wars", "90s nostalgia", "power rangers", "ninja turtles", "vhs tape",
    "arcade cabinet", "retro basketball", "matrix dodge", "robin williams",
    "austin powers", "napoleon dynamite", "cat vibing", "shaq dunk", "spongebob fail",
    "crt monitor", "classic arcade", "oatmeal bowl", "space galaxy", "pixel art",
    "sonic hedgehog", "sega genesis", "nintendo 64", "jurassic park", "lion king",
    "backyard basketball", "vcr player", "camcorder tape", "shelton washington", "forest grove"
]

def get_zero_token_text() -> str:
    for _ in range(25):
        candidate = random.choice(ZERO_TOKEN_BANTER_POOL)
        h = hashlib.md5(candidate.encode("utf-8")).hexdigest()
        if h not in recent_text_hashes:
            recent_text_hashes.append(h)
            return candidate
    return random.choice(ZERO_TOKEN_BANTER_POOL)

def sanitize_reply_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

async def resolve_media_url(url: str) -> str:
    if not url or "tenor.com" not in url:
        return url
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        html = await asyncio.to_thread(lambda: urllib.request.urlopen(req, timeout=4).read().decode("utf-8", "ignore"))
        match = re.search(r'<meta\s+property="og:image"\s+content="([^"]+)"', html)
        if match:
            return match.group(1)
    except Exception:
        pass
    return url

async def fetch_tenor_gif(query: str = None) -> Optional[str]:
    rand_chars = "".join(random.choices(string.ascii_lowercase + string.digits, k=random.randint(4, 8)))
    base_q = query or random.choice(SEED_WORDS)
    q = f"{base_q} {rand_chars}"
    for _ in range(3):
        try:
            encoded = urllib.parse.quote(q)
            url = f"https://tenor.com/search/{encoded}-gifs"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            html = urllib.request.urlopen(req, timeout=4).read().decode("utf-8", "ignore")
            ids = re.findall(r"/view/[^\"']+-gif-(\d+)", html)
            random.shuffle(ids)
            for gid in ids[:20]:
                candidate = f"https://tenor.com/view/{gid}"
                if candidate not in recent_media_urls:
                    recent_media_urls.append(candidate)
                    return candidate
        except Exception:
            await asyncio.sleep(0.1)
    return None

async def fetch_youtube(query: str = None) -> str:
    rand_chars = "".join(random.choices(string.ascii_lowercase + string.digits, k=random.randint(4, 8)))
    base_q = query or random.choice(SEED_WORDS)
    q = f"{base_q} {rand_chars}"
    try:
        encoded = urllib.parse.quote(q)
        url = f"https://www.youtube.com/results?search_query={encoded}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        html = urllib.request.urlopen(req, timeout=4).read().decode("utf-8", "ignore")
        ids = list(set(re.findall(r"watch\?v=([\w-]{11})", html)))
        if ids:
            vid = random.choice(ids)
            candidate = f"https://www.youtube.com/watch?v={vid}"
            if candidate not in recent_media_urls:
                recent_media_urls.append(candidate)
                return candidate
    except Exception:
        pass
    return f"https://www.youtube.com/results?search_query=joeyarnoldvn+{rand_chars}"

async def fetch_pollinations(prompt: str = None) -> Optional[discord.File]:
    rand_chars = "".join(random.choices(string.ascii_lowercase + string.digits, k=random.randint(4, 8)))
    base_p = prompt or f"surreal 1990s {random.choice(SEED_WORDS)} neon retro aesthetic with Joey Arnold cinematic lighting"
    p = f"{base_p} variant {rand_chars}"
    try:
        seed = random.randint(1, 99999999)
        img_url = f"https://image.pollinations.ai/prompt/{urllib.parse.quote(p)}?width=768&height=768&nologo=true&seed={seed}"
        req = urllib.request.Request(img_url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        data = urllib.request.urlopen(req, timeout=6).read()
        return discord.File(io.BytesIO(data), filename=f"oatmeal_{seed}_{rand_chars}.jpg")
    except Exception:
        return None

async def fetch_embed_photo() -> str:
    try:
        query = random.choice(SEED_WORDS)
        encoded = urllib.parse.quote(query)
        url = f"https://commons.wikimedia.org/w/api.php?action=query&generator=search&gsrsearch={encoded}&gsrnamespace=6&gsrlimit=15&prop=imageinfo&iiprop=url&format=json"
        req = urllib.request.Request(url, headers={"User-Agent": "OatmealAI/46.2"})
        data = json.loads(urllib.request.urlopen(req, timeout=5).read().decode("utf-8", "ignore"))
        pages = data.get("query", {}).get("pages", {})
        urls = []
        for page_id, page_info in pages.items():
            imageinfo = page_info.get("imageinfo", [])
            if imageinfo:
                img_url = imageinfo[0].get("url")
                if img_url and img_url.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                    if img_url not in recent_media_urls:
                        urls.append(img_url)
        if urls:
            chosen = random.choice(urls)
            recent_media_urls.append(chosen)
            return chosen
    except Exception as e:
        print(f"[WIKIMEDIA PHOTO SEARCH EXCEPTION] {e}")

    seed_id = random.randint(10, 99999)
    fallback_url = f"https://picsum.photos/seed/{seed_id}/1280/720"
    if fallback_url not in recent_media_urls:
        recent_media_urls.append(fallback_url)
    return fallback_url

async def fetch_audio_embed() -> str:
    rand_chars = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    random_track_id = "".join(random.choices(string.ascii_lowercase + string.ascii_uppercase + string.digits, k=22))
    random_playlist_id = "".join(random.choices(string.ascii_lowercase + string.ascii_uppercase + string.digits, k=22))
    genre = random.choice(["90s+hip+hop", "retro+synthwave", "classic+rock", "lofi+beats", "ambient+jazz", "grunge+punk", "indie+rock"])
    tracks = [
        f"https://open.spotify.com/track/{random_track_id}?ref={rand_chars}",
        f"https://open.spotify.com/playlist/{random_playlist_id}?si={rand_chars}",
        f"https://soundcloud.com/search?q={genre}+{rand_chars}",
        f"https://bandcamp.com/tag/{genre}",
        f"https://open.spotify.com/album/{random_track_id}?q={rand_chars}"
    ]
    cand = random.choice(tracks)
    if cand not in recent_media_urls:
        recent_media_urls.append(cand)
        return cand
    return cand

async def fetch_web_link() -> str:
    try:
        req = urllib.request.Request("https://en.wikipedia.org/api/rest_v1/page/random/summary", headers={"User-Agent": "OatmealAI/46.2"})
        data = json.loads(urllib.request.urlopen(req, timeout=4).read().decode("utf-8", "ignore"))
        wiki_url = data.get("content_urls", {}).get("desktop", {}).get("page")
        if wiki_url and wiki_url not in recent_media_urls:
            recent_media_urls.append(wiki_url)
            return wiki_url
    except Exception as e:
        print(f"[WIKIPEDIA RANDOM API EXCEPTION] {e}")

    rand_chars = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    links = [
        "https://archive.org/details/softwarelibrary_msdos_games",
        f"https://www.nasa.gov/?q={rand_chars}",
        f"https://en.wikipedia.org/wiki/Special:Search?search={rand_chars}"
    ]
    cand = random.choice(links)
    if cand not in recent_media_urls:
        recent_media_urls.append(cand)
    return cand

async def get_wild_media_hybrid(
    guild: Optional[discord.Guild] = None,
    priority_tier: int = 0,
    chat_context: str = "",
    allow_tokens: bool = False
) -> Tuple[str, Optional[discord.File], Optional[discord.Sticker]]:
    if guild and guild.stickers and (random.random() < 0.00008):
        try:
            st = random.choice(guild.stickers)
            return "", None, st
        except Exception:
            pass

    generators = [
        lambda: fetch_pollinations(chat_context),
        lambda: fetch_tenor_gif(chat_context),
        lambda: fetch_youtube(chat_context),
        lambda: fetch_embed_photo(),
        lambda: fetch_audio_embed(),
        lambda: fetch_web_link()
    ]
    random.shuffle(generators)

    for gen in generators:
        try:
            res = await gen()
            if res:
                if isinstance(res, discord.File):
                    return "", res, None
                elif isinstance(res, str):
                    if res not in recent_media_urls:
                        recent_media_urls.append(res)
                        return res, None, None
        except Exception:
            continue

    fallback = await fetch_embed_photo()
    return fallback, None, None

async def build_message_payload(reply: str, media_url: str, file_obj: Optional[discord.File], sticker: Optional[discord.Sticker], mention_prefix: str = "") -> Tuple[str, Dict[str, Any]]:
    clean_reply = sanitize_reply_text(reply)
    content = mention_prefix
    if clean_reply:
        content = f"{content} {clean_reply}".strip() if content else clean_reply
    
    kwargs = {}
    if file_obj:
        kwargs["file"] = file_obj
    if sticker:
        kwargs["stickers"] = [sticker]

    if media_url:
        if media_url.startswith("```text"):
            content = f"{content}\n{media_url}".strip() if content else media_url
        else:
            resolved_media = await resolve_media_url(media_url)
            if resolved_media and (resolved_media.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")) or "c.tenor.com" in resolved_media or "pixabay.com" in resolved_media or "wikimedia.org" in resolved_media or "picsum.photos" in resolved_media):
                embed = discord.Embed(color=0x2b2d31)
                embed.set_image(url=resolved_media)
                kwargs["embed"] = embed
            elif any(d in media_url for d in ("youtube.com", "spotify.com", "soundcloud.com", "wikipedia.org", "nasa.gov", "archive.org", "bandcamp.com")):
                content = f"{content}\n{media_url}".strip() if content else media_url
            else:
                f = await fetch_pollinations(clean_reply or "retro 90s aesthetic")
                if f:
                    kwargs["file"] = f

    return content, kwargs

def extract_relevant_context(prompt: str) -> str:
    if not prompt:
        return SHORT_IDENTITY
    prompt_lower = prompt.lower()
    years = set(re.findall(r"\b(19\d{2}|20\d{2})\b", prompt))

    for a in re.findall(r"(?:age|old|was|were|at|turned)\s+(\d{1,2})", prompt, re.I):
        try: years.add(str(1985 + int(a)))
        except Exception: pass

    for word, val in WORD_NUMBERS.items():
        if re.search(rf"(?:age|old|was|were|at|turned)\s+{word}\b", prompt_lower):
            years.add(str(1985 + val))

    location_map = {
        "california": ["1992", "1999", "2000", "2001", "2002", "2003", "2005", "2007", "2008", "2009", "2010", "2017"],
        "vietnam": ["2012", "2013", "2014", "2015", "2016", "2017"],
        "saigon": ["2013", "2014", "2015", "2016", "2017"],
        "forest grove": ["1985", "1986", "1987", "1988", "1989", "1990", "1991", "1992", "1993", "1994", "1995", "1996", "1997", "1998", "1999", "2000", "2001", "2002", "2003", "2004", "2005", "2007", "2008", "2011", "2012", "2017", "2018"],
        "jail": ["2012"],
        "hillsboro": ["2012"],
        "shelton": ["2018", "2019", "2020", "2021", "2022", "2023", "2024", "2025", "2026"],
        "portland": ["2008", "2009", "2010", "2011"],
        "hawaii": ["2007"],
        "new york": ["2004"],
        "west virginia": ["2006", "2007"],
        "camp": ["2005", "2006", "2007", "2008", "2010"],
        "attic": ["1996", "2011", "2012", "2023"],
    }
    for key, ys in location_map.items():
        if key in prompt_lower:
            years.update(ys)

    if years:
        lines = [ln for ln in FULL_ANNUAL_OUTLINE.strip().splitlines() if any(ln.startswith(f"{y}:") for y in years)]
        if lines:
            return "Relevant years only:\n" + "\n".join(lines)

    if any(k in prompt_lower for k in ("who are you", "about yourself", "your life", "biography", "timeline")):
        return SHORT_IDENTITY + "\n" + PERSONALITY_KERNEL
    return SHORT_IDENTITY

async def fetch_bitcoin_price() -> str:
    try:
        req = urllib.request.Request(
            "[https://api.coinbase.com/v2/prices/BTC-USD/spot](https://api.coinbase.com/v2/prices/BTC-USD/spot)",
            headers={"User-Agent": "Mozilla/5.0"}
        )
        data = json.loads(urllib.request.urlopen(req, timeout=4).read().decode())
        amount = data.get("data", {}).get("amount")
        if amount:
            return f"Bitcoin is currently around ${float(amount):,.2f} USD."
    except Exception as e:
        print(f"[BITCOIN COINBASE EXCEPTION] {e}")

    try:
        req = urllib.request.Request(
            "[https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd](https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd)",
            headers={"User-Agent": "Mozilla/5.0"}
        )
        data = json.loads(urllib.request.urlopen(req, timeout=3).read().decode())
        price = data.get("bitcoin", {}).get("usd")
        if price:
            return f"Bitcoin is currently around ${float(price):,.2f} USD."
    except Exception as e:
        print(f"[BITCOIN COINGECKO EXCEPTION] {e}")

    return ""

async def fetch_wikipedia_search(query: str) -> str:
    try:
        encoded = urllib.parse.quote(query)
        url = f"[https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch=](https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch=){encoded}&format=json"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        data = json.loads(urllib.request.urlopen(req, timeout=4).read().decode("utf-8", "ignore"))
        search_results = data.get("query", {}).get("search", [])
        snippets = [re.sub(r"<.*?>", "", item.get("snippet", "")) for item in search_results[:3]]
        if snippets:
            return " | ".join(snippets)
    except Exception as e:
        print(f"[WIKIPEDIA SEARCH EXCEPTION] {e}")
    return ""

async def fetch_live_context(query: str) -> str:
    query_lower = query.lower().strip()
    if any(k in query_lower for k in ("bitcoin", "btc", "crypto price", "price of bitcoin")):
        btc = await fetch_bitcoin_price()
        if btc: return btc

    clean = re.sub(r"^(search|search for|oatmeal|find|what is|who is|tell me|price of|current|playing in|the news|news)\s*", "", query, flags=re.I).strip()
    if not clean or len(clean) < 3:
        clean = query or "latest world news breaking headlines"

    try:
        encoded = urllib.parse.quote(clean)
        url = f"[https://html.duckduckgo.com/html/?q=](https://html.duckduckgo.com/html/?q=){encoded}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        html = urllib.request.urlopen(req, timeout=5).read().decode("utf-8", "ignore")
        snippets = re.findall(r'<a class="result__snippet[^>]*>(.*?)</a>', html, re.DOTALL)
        cleaned = [re.sub(r"<.*?>", "", s).strip() for s in snippets[:4] if s.strip()]
        if cleaned:
            return " | ".join(cleaned)[:600]
    except Exception as e:
        print(f"[DUCKDUCKGO SEARCH EXCEPTION] {e}")

    wiki_res = await fetch_wikipedia_search(clean)
    if wiki_res:
        return wiki_res

    return "No direct web search snippets found."

async def process_job(job: PendingJob):
    """
    COROUTINE CORRECTION SYSTEM:
    Regulates frequency, weight, and token usage. Automatically retries up to 20 times
    with 2X exponential backoff when fallback text, duplicate hashes, or fallback media are detected.
    Priorities 1 and 4 are immune unless exhausted.
    """
    global last_api_call_timestamp

    max_retries = 20
    base_delay = 0.5

    for attempt in range(max_retries):
        while True:
            m = get_trajectory_metrics()
            if time.monotonic() < GROQ_RATE_LIMITED_UNTIL or m["local_daily"] >= 195000:
                await asyncio.sleep(5.0)
                continue
            break

        token_limit = calculate_dynamic_max_tokens(job.priority_tier)
        if token_limit <= 0 and job.priority_tier not in (2, 3, 7):
            print(f"[TRAFFIC ENGINE THROTTLE] Priority {job.priority_tier} throttled.")
            return

        engine = get_traffic_engine_metrics(job.priority_tier)
        freq = engine["freq_multiplier"]

        if job.priority_tier == 1: min_gap = max(0.04, 0.08 * freq)
        elif job.priority_tier == 4: min_gap = max(0.2, 0.4 * freq)
        else: min_gap = max(1.0, 1.8 * freq)

        elapsed = time.monotonic() - last_api_call_timestamp
        if elapsed < min_gap:
            await asyncio.sleep(min_gap - elapsed)

        mood = random.randint(1, 5)
        temperature = 0.88 if job.priority_tier == 1 else 1.05

        search_triggers = (
            "search", "search for", "find", "playing in", "theaters", "cinema", "box office",
            "bitcoin", "btc", "crypto", "price", "weather", "news", "current", "who won",
            "what is", "who is", "tell me about", "history of", "where were you", "headlines"
        )
        prompt_lower = (job.prompt_text or "").lower()
        needs_search = job.priority_tier in (1, 4) and any(k in prompt_lower for k in search_triggers)
        
        live = await fetch_live_context(job.prompt_text) if needs_search else ""
        history = extract_relevant_context(job.prompt_text) if job.priority_tier in (1, 4) else SHORT_IDENTITY

        search_instruction = ""
        if needs_search:
            if live:
                search_instruction = (
                    f"\n[STRICT REAL-WORLD DATA ENFORCEMENT]: Live web search results found:\n'{live}'\n"
                    "CRITICAL: Summarize and deliver these real facts directly in character as Joey Arnold."
                )
            else:
                search_instruction = "\n[LIVE SEARCH NOTICE]: No live web results found. State this plainly as Joey."

        system = (
            f"{PRONOUN_BRIDGE}\n{FORBIDDEN_PHRASES}\n{VOICE_KERNEL}\n"
            f"MOOD: {PERSONA_MOODS[mood]}\n"
            f"OUTLINE:\n{history}\n"
            f"{ANTI_HALLUCINATION_GUARD}\n"
            f"{search_instruction}\n"
            f"SLOGAN: {random.choice(SLOGANS)}\n{SOVEREIGN}"
        )
        if job.priority_tier == 8:
            system += (
                "\nHARD RULE FOR PRIORITY 8 BACKLOG REFLECTION: "
                "Deliver an ultra-sharp, punchy, highly witty 90s Oregon workaholic reflection on the past bot message. "
                "Never sound generic or lame. Inject 90s nostalgia or archival spirit."
            )
        elif job.priority_tier == 9:
            system += (
                "\nYou are generating a chat recap. You MUST output your response in exactly two lines:\n"
                "Line 1: Oatmeal Recap.\n"
                "Line 2: A short, punchy, in-character summary of the recent server chat above.\n"
                "No media, text only."
            )

        reply = ""
        actual_tokens = 0

        async def _keep_typing():
            try:
                while True:
                    async with job.channel.typing():
                        await asyncio.sleep(8.5)
            except asyncio.CancelledError:
                pass

        typing_task = asyncio.create_task(_keep_typing())
        try:
            messages_payload = [
                {"role": "system", "content": system},
                {"role": "user", "content": job.prompt_text or "Thought?"}
            ]
            async with groq_api_lock:
                reply, actual_tokens, headers = await asyncio.to_thread(
                    call_groq_rest, messages_payload, token_limit or 150, temperature
                )
                last_api_call_timestamp = time.monotonic()
            
            if any(b in reply.lower() for b in ("as an ai", "i cannot", "i'm sorry, but i can't")):
                reply = ""
        except Exception as e:
            print(f"[API EXCEPTION P{job.priority_tier}] {e}")
        finally:
            typing_task.cancel()

        track_token_usage_real(actual_tokens or 30)

        if not reply:
            reply = get_zero_token_text()

        if job.priority_tier == 8:
            reply = " ".join(reply.split()[:15])
        elif job.priority_tier == 9:
            lines = [l.strip() for l in reply.splitlines() if l.strip()]
            if len(lines) >= 2:
                reply = f"Oatmeal Recap.\n{lines[1]}"
            elif len(lines) == 1:
                reply = f"Oatmeal Recap.\n{lines[0]}"
            else:
                reply = "Oatmeal Recap.\nNothing much happening in the attic right now."

        text_hash = hashlib.md5(reply.encode("utf-8")).hexdigest()
        is_duplicate_text = text_hash in recent_text_hashes
        is_fallback = reply in ZERO_TOKEN_BANTER_POOL

        media_url, file_obj, sticker = "", None, None
        want_media = (job.priority_tier in (4, 6, 7, 8, 9, 2, 5, 3)) or (job.priority_tier == 1 and random.random() < 0.75)
        if want_media and job.priority_tier not in (8, 9):
            media_url, file_obj, sticker = await get_wild_media_hybrid(
                guild=getattr(job.channel, "guild", None),
                priority_tier=job.priority_tier,
                chat_context=job.prompt_text,
                allow_tokens=(token_limit > 0)
            )

        # COROUTINE CORRECTION: If duplicate or fallback, slow down by 2X and retry up to 20 times (unless P1/P4 direct user request)
        immune = (job.priority_tier in (1, 4) and job.reference_message is not None)
        if (is_duplicate_text or is_fallback) and not immune and attempt < max_retries - 1:
            backoff = base_delay * (2 ** attempt)
            print(f"[COROUTINE CORRECTION] Duplicate/Fallback detected on P{job.priority_tier} (Attempt {attempt+1}/{max_retries}). Backing off by {backoff:.2f}s...")
            await asyncio.sleep(backoff)
            continue

        recent_text_hashes.append(text_hash)

        try:
            mention_prefix = job.target_member.mention if job.target_member else ""
            content_val, kwargs = await build_message_payload(reply, media_url, file_obj, sticker, mention_prefix=mention_prefix)

            if job.reference_message:
                await job.reference_message.reply(content=content_val or None, mention_author=(job.priority_tier == 1), **kwargs)
            elif job.target_member:
                await job.channel.send(content=content_val or None, **kwargs)
            else:
                await job.channel.send(content=content_val or None, **kwargs)
        except Exception as e:
            print(f"[DISPATCH EXCEPTION P{job.priority_tier}] {e}")

        if job.reference_message:
            replied_message_ids.add(job.reference_message.id)
            save_state()
        break

async def execute_priority_2():
    """
    PRIORITY 2:
    Only reply with RANDOM MEDIA in forward-chronological order to each unique member's MESSAGE
    only once per MESSAGE in an infinite loop using internal Discord search navigation / history.
    """
    await bot.wait_until_ready()
    print("[COROUTINE INITIALIZED] Priority 2: Forward-Chronological Unique Message Media Replies")
    while not bot.is_closed():
        engine = get_traffic_engine_metrics(2)
        delay = random.uniform(30, 90) * engine["freq_multiplier"]
        await asyncio.sleep(delay)

        if time.monotonic() < DAILY_EXHAUSTED_UNTIL or time.monotonic() < GROQ_RATE_LIMITED_UNTIL:
            continue

        try:
            for guild in list(bot.guilds):
                channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages and c.permissions_for(guild.me).read_message_history]
                if not channels: continue
                random.shuffle(channels)
                ch = channels[0]

                cursor = channel_cursors.get(f"p2_fwd_{ch.id}", 0)
                kwargs = {"limit": 50, "oldest_first": True}
                if cursor:
                    kwargs["after"] = discord.Object(id=cursor)

                target_msg = None
                async for msg in ch.history(**kwargs):
                    channel_cursors[f"p2_fwd_{ch.id}"] = msg.id
                    if msg.author.bot or msg.author == bot.user or msg.is_system() or msg.type != discord.MessageType.default:
                        continue
                    if msg.id in replied_message_ids:
                        continue
                    target_msg = msg
                    break

                if not target_msg and cursor != 0:
                    channel_cursors[f"p2_fwd_{ch.id}"] = 0
                    continue

                if target_msg:
                    url, file_obj, sticker = await get_wild_media_hybrid(guild, priority_tier=2)
                    content_val, kwargs_send = await build_message_payload("", url, file_obj, sticker)
                    try:
                        await target_msg.reply(content=content_val or None, mention_author=False, **kwargs_send)
                        replied_message_ids.add(target_msg.id)
                        save_state()
                        print(f"[P2 DISPATCH] Random media reply to unique msg {target_msg.id} in #{ch.name}")
                    except Exception as e:
                        print(f"[P2 SEND EXCEPTION] {e}")
                    break
        except Exception as e:
            print(f"[P2 COROUTINE EXCEPTION] {e}")

async def execute_priority_3():
    """
    PRIORITY 3:
    Message ALPHABETICALLY to each individual unique member only once in an infinite loop.
    Only mention member's username and not display name or nicknames.
    Must not text the member's name more than once per message.
    """
    await bot.wait_until_ready()
    print("[COROUTINE INITIALIZED] Priority 3: Alphabetical Unique Member Tagging")
    while not bot.is_closed():
        engine = get_traffic_engine_metrics(3)
        delay = random.uniform(60, 150) * engine["freq_multiplier"]
        await asyncio.sleep(delay)

        try:
            for guild in list(bot.guilds):
                # Sort members alphabetically by username (target.name)
                members = sorted([m for m in guild.members if not m.bot], key=lambda x: x.name.lower())
                if not members:
                    continue
                idx = p3_member_indices.get(guild.id, 0) % len(members)
                target = members[idx]
                p3_member_indices[guild.id] = (idx + 1) % len(members)

                channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages]
                if not channels:
                    continue
                ch = random.choice(channels)
                url, file_obj, sticker = await get_wild_media_hybrid(guild, priority_tier=3)
                
                # Mention user by username / mention without duplicating name text
                mention_str = f"<@{target.id}>"
                content_val, kwargs = await build_message_payload(get_zero_token_text(), url, file_obj, sticker, mention_prefix=mention_str)

                await ch.send(content=content_val or None, **kwargs)
                print(f"[P3 DISPATCH] Alphabetical tag for username {target.name} in #{ch.name}")
                break
        except Exception as e:
            print(f"[P3 COROUTINE EXCEPTION] {e}")

async def execute_priority_4():
    await bot.wait_until_ready()
    print("[COROUTINE INITIALIZED] Priority 4: Live Chat Reaction Genie")
    while not bot.is_closed():
        engine = get_traffic_engine_metrics(4)
        delay = random.uniform(2, 8) * engine["freq_multiplier"]
        await asyncio.sleep(delay)

        if time.monotonic() < DAILY_EXHAUSTED_UNTIL or time.monotonic() < GROQ_RATE_LIMITED_UNTIL:
            continue

        try:
            for guild in list(bot.guilds):
                channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages and c.permissions_for(guild.me).read_message_history]
                if not channels: continue
                random.shuffle(channels)
                ch = channels[0]

                target = None
                async for msg in ch.history(limit=25):
                    if msg.author.bot or msg.author == bot.user or not msg.content:
                        continue
                    if msg.id in replied_message_ids:
                        continue
                    if (datetime.now(timezone.utc) - msg.created_at).total_seconds() > 3600:
                        continue
                    target = msg
                    break

                if target:
                    clean_text = re.sub(r"<@!?\d+>", "", target.content).strip()
                    if len(clean_text) >= 2:
                        job = PendingJob(
                            priority_tier=4,
                            timestamp=time.monotonic(),
                            channel=ch,
                            prompt_text=f"Quick witty take on chat message: '{clean_text[:120]}'",
                            reference_message=target,
                            author_id=target.author.id
                        )
                        await delay_queue.put(job)
                        break
        except Exception as e:
            print(f"[P4 COROUTINE EXCEPTION] {e}")

async def execute_priority_6_drops():
    await bot.wait_until_ready()
    print("[COROUTINE INITIALIZED] Priority 6: Spontaneous Infinite Media Drops")
    while not bot.is_closed():
        engine = get_traffic_engine_metrics(6)
        delay = random.uniform(15, 45) * engine["freq_multiplier"]
        await asyncio.sleep(delay)

        try:
            for guild in list(bot.guilds):
                channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages]
                if not channels:
                    continue
                ch = random.choice(channels)
                url, file_obj, sticker = await get_wild_media_hybrid(guild, priority_tier=6)
                content_val, kwargs = await build_message_payload(get_zero_token_text(), url, file_obj, sticker)

                if content_val or kwargs:
                    await ch.send(content=content_val or None, **kwargs)
                    print(f"[P6 DISPATCH] Spontaneous media drop in #{ch.name}")
                break
        except Exception as e:
            print(f"[P6 COROUTINE EXCEPTION] {e}")

async def execute_priority_7():
    await bot.wait_until_ready()
    print("[COROUTINE INITIALIZED] Priority 7: Bot Media Sweeper")
    while not bot.is_closed():
        engine = get_traffic_engine_metrics(7)
        delay = random.uniform(20, 60) * engine["freq_multiplier"]
        await asyncio.sleep(delay)

        try:
            for guild in list(bot.guilds):
                channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages and c.permissions_for(guild.me).read_message_history]
                if not channels: continue
                random.shuffle(channels)
                ch = channels[0]

                cursor = bot_media_cursors.get(f"bot_media_fwd_{ch.id}", 0)
                kwargs = {"limit": 50, "oldest_first": True}
                if cursor > 0:
                    kwargs["after"] = discord.Object(id=cursor)

                target_msg = None
                async for msg in ch.history(**kwargs):
                    bot_media_cursors[f"bot_media_fwd_{ch.id}"] = msg.id
                    if msg.author == bot.user and not msg.pinned:
                        target_msg = msg
                        break

                if not target_msg and cursor != 0:
                    bot_media_cursors[f"bot_media_fwd_{ch.id}"] = 0
                    continue

                if not target_msg:
                    continue

                url, file_obj, sticker = await get_wild_media_hybrid(guild, priority_tier=7)
                content_val, kwargs_send = await build_message_payload("", url, file_obj, sticker)

                try:
                    await target_msg.reply(content=content_val or None, mention_author=False, **kwargs_send)
                    save_state()
                    print(f"[P7 BOT-MEDIA-SWEEPER] Media reply to bot msg {target_msg.id} in #{ch.name}")
                except Exception as ex:
                    print(f"[P7 REPLY EXCEPTION] {ex}")
                break
        except Exception as e:
            print(f"[P7 COROUTINE EXCEPTION] {e}")

async def execute_priority_8():
    await bot.wait_until_ready()
    print("[COROUTINE INITIALIZED] Priority 8: Bot Text Sweeper")
    while not bot.is_closed():
        engine = get_traffic_engine_metrics(8)
        delay = random.uniform(25, 75) * engine["freq_multiplier"]
        await asyncio.sleep(delay)

        try:
            for guild in list(bot.guilds):
                channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages and c.permissions_for(guild.me).read_message_history]
                if not channels: continue
                random.shuffle(channels)
                ch = channels[0]

                cursor = bot_text_cursors.get(f"bot_text_fwd_{ch.id}", 0)
                kwargs = {"limit": 50, "oldest_first": True}
                if cursor > 0:
                    kwargs["after"] = discord.Object(id=cursor)

                target_msg = None
                async for msg in ch.history(**kwargs):
                    bot_text_cursors[f"bot_text_fwd_{ch.id}"] = msg.id
                    if msg.author == bot.user and not msg.pinned and len(msg.content or "") > 2:
                        target_msg = msg
                        break

                if not target_msg and cursor != 0:
                    bot_text_cursors[f"bot_text_fwd_{ch.id}"] = 0
                    continue

                if not target_msg:
                    continue

                safe = re.sub(r"https?://\S+", "[media]", target_msg.content or "Oatmeal")[:60]
                job = PendingJob(
                    priority_tier=8,
                    timestamp=time.time(),
                    channel=ch,
                    prompt_text=f"Deep archival backlog reflection on past bot message: '{safe}'",
                    reference_message=target_msg,
                    p8_channel_id=str(ch.id),
                    p8_message_id=target_msg.id
                )
                await delay_queue.put(job)
                save_state()
                break
        except Exception as e:
            print(f"[P8 COROUTINE EXCEPTION] {e}")

async def execute_priority_9():
    await bot.wait_until_ready()
    print("[COROUTINE INITIALIZED] Priority 9: Periodic Chat Recap")
    while not bot.is_closed():
        engine = get_traffic_engine_metrics(9)
        delay = random.uniform(300, 600) * engine["freq_multiplier"]
        await asyncio.sleep(delay)

        if time.monotonic() < DAILY_EXHAUSTED_UNTIL or time.monotonic() < GROQ_RATE_LIMITED_UNTIL:
            continue

        try:
            for guild in list(bot.guilds):
                channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages and c.permissions_for(guild.me).read_message_history]
                if not channels:
                    continue
                random.shuffle(channels)
                ch = channels[0]

                chat_lines = []
                async for msg in ch.history(limit=40):
                    if msg.author.bot or not msg.content:
                        continue
                    chat_lines.append(f"{msg.author.display_name}: {msg.content}")

                if not chat_lines:
                    continue

                chat_history_text = "\n".join(reversed(chat_lines))

                job = PendingJob(
                    priority_tier=9,
                    timestamp=time.time(),
                    channel=ch,
                    prompt_text=(
                        f"Recent chat history:\n{chat_history_text}\n\n"
                        "Summarize this chat."
                    )
                )
                await delay_queue.put(job)
                print(f"[P9 ARCHIVE RECAP QUEUED] Enqueued recap in #{ch.name}")
                break
        except Exception as e:
            print(f"[P9 COROUTINE EXCEPTION] {e}")

async def execute_priority_5_reverse():
    await bot.wait_until_ready()
    print("[COROUTINE INITIALIZED] Priority 5: Reverse History Sweep")
    while not bot.is_closed():
        engine = get_traffic_engine_metrics(5)
        delay = random.uniform(40, 100) * engine["freq_multiplier"]
        await asyncio.sleep(delay)

        try:
            for guild in list(bot.guilds):
                channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages and c.permissions_for(guild.me).read_message_history]
                if not channels:
                    continue
                ch = random.choice(channels)
                async for msg in ch.history(limit=25, oldest_first=False):
                    if msg.author.bot or msg.id in replied_message_ids:
                        continue
                    job = PendingJob(5, time.time(), ch, "Short reflection.", reference_message=msg, author_id=msg.author.id)
                    await delay_queue.put(job)
                    break
                break
        except Exception as e:
            print(f"[P5 COROUTINE EXCEPTION] {e}")

async def delay_queue_worker():
    while True:
        job = await delay_queue.get()
        while time.monotonic() < GROQ_RATE_LIMITED_UNTIL or (get_trajectory_metrics()["usage"] > 0.95 and job.priority_tier != 1):
            await asyncio.sleep(5)
        await process_job(job)
        delay_queue.task_done()

@bot.event
async def on_ready():
    print(f"============================================================")
    print(f" Oatmeal AI V46.2 Coroutine Correction & Traffic Engine logged in as {bot.user}")
    print(f" System Time: {datetime.now(timezone.utc).isoformat()}")
    print(f"============================================================")
    load_state()
    bot.loop.create_task(delay_queue_worker())
    bot.loop.create_task(execute_priority_2())
    bot.loop.create_task(execute_priority_3())
    bot.loop.create_task(execute_priority_4())
    bot.loop.create_task(execute_priority_5_reverse())
    bot.loop.create_task(execute_priority_6_drops())
    bot.loop.create_task(execute_priority_7())
    bot.loop.create_task(execute_priority_8())
    bot.loop.create_task(execute_priority_9())

@bot.event
async def on_message(message):
    """
    PRIORITY 1:
    1. When a member (minus itself) replies to the bot.
    2. When a member (minus itself) mentions the bot's username (Oatmeal AI#4059 or bot.user)
       or mentions the bot's nicknames (OATMEAL, JOEY, ARNOLD, JOEYARNOLDVN, OJAWALL - not case-sensitive).
    """
    if message.author.bot or message.guild is None or message.is_system() or message.author == bot.user:
        return

    now = time.time()
    author_id = message.author.id

    last_member_activity_time[message.channel.id] = now
    last_member_activity_time[message.guild.id] = now

    if str(author_id) in blocked_users:
        if now < blocked_users[str(author_id)]:
            return
        del blocked_users[str(author_id)]
        save_state()

    clean = re.sub(r"<@!?\d+>", "", message.content).strip()
    words = clean.lower().split()
    is_name_only = len(words) == 1 and words[0].strip(",.!?") in ("joey", "arnold", "oatmeal", "joeyarnoldvn", "ojawall")

    if is_name_only:
        hist = user_message_history.setdefault(author_id, [])
        hist[:] = [(ts, c) for ts, c in hist if now - ts <= 600]
        hist.append((now, words[0]))
        if len(hist) >= 10:
            blocked_users[str(author_id)] = now + (30 * 86400)
            save_state()
            print(f"[SPAM ENFORCEMENT] Blocked member {author_id} for 30 days due to name ping flooding")
            return
    else:
        user_message_history[author_id] = []

    lifetime_interacted_members.add(author_id)
    content_lower = message.content.lower()

    # Priority 1 Trigger Checks
    mentioned_bot = (bot.user in message.mentions) or any(n in content_lower for n in ("oatmeal ai#4059", str(bot.user.id)))
    replied_to_bot = False
    if message.reference and isinstance(message.reference.resolved, discord.Message):
        replied_to_bot = message.reference.resolved.author == bot.user

    nicknames = ("joey", "arnold", "oatmeal", "joeyarnoldvn", "ojawall")
    named = any(nick in content_lower for nick in nicknames)

    if mentioned_bot or replied_to_bot or named:
        replied_message_ids.add(message.id)
        job = PendingJob(
            priority_tier=1, 
            timestamp=now, 
            channel=message.channel, 
            prompt_text=clean or "Hello Oatmeal.", 
            reference_message=message, 
            author_id=author_id
        )
        await delay_queue.put(job)
        return

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
