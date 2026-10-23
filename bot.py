#!/usr/bin/env python3
"""
Oatmeal AI – Joey Arnold Discord Bot (#4059)
Master Production Version 36.2 (Priority 2 Media-Only & 1% Link Frequency Edition)

- Priority 2 strictly restricted to media-only posts (zero text/banter)
- Web links restricted to <=1% frequency in the hybrid media generator (99% GIFs/images/stickers/emoticons)
- Multi-source search engine active exclusively in Priorities 1 and 4
- Tenor link URL hiding via clean Discord embeds (`resolve_media_url` integration)
- Fully flexible, dynamic hybrid media generator (zero-token internet word-mesh + token-assisted AI prompts)
- Anti-repetition state tracking (recent media URLs, sticker IDs, and text hashes)
- Master Traffic Engine with continuous coroutine pacing & sequential zero-token cutover
- Infinite forward chronological self-mirrors (P7 media + P8 text) starting from oldest bot message
- Priority 9 periodic chat recap ("Oatmeal Recap." format)
- 600-second member activity pause on background maintenance priorities
- Atomic state persistence (tempfile -> fsync -> os.replace) & Render health check server
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
# 1. RENDER HEALTH CHECK SERVER
# ============================================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Oatmeal AI V36.2 -- Priority Media Engine Operating")
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
# 3. GLOBAL STATE STRUCTURES & ANTI-REPETITION TRACKERS
# ============================================================
replied_message_ids: Set[int] = set()
channel_cursors: Dict[str, int] = {}
priority2_replied_member_ids: Set[str] = set()
lifetime_interacted_members: Set[int] = set()

recent_media_urls = collections.deque(maxlen=5000)
recent_sticker_ids = collections.deque(maxlen=200)
recent_text_hashes = collections.deque(maxlen=2000)

p3_member_indices: Dict[int, int] = {}
p7_cursors: Dict[str, int] = {}
p8_cursors: Dict[str, int] = {}
last_member_activity_time: Dict[Any, float] = {}

DAILY_EXHAUSTED_UNTIL = 0.0
local_daily_tokens = 0
local_daily_requests = 0
local_daily_reset_day = None

blocked_users: Dict[str, float] = {}
user_message_history: Dict[int, List[Tuple[float, str]]] = {}

def p2_key(guild_id: int, author_id: int) -> str:
    return f"{guild_id}:{author_id}"

def load_state():
    global replied_message_ids, channel_cursors, priority2_replied_member_ids
    global blocked_users, p7_cursors, p8_cursors
    try:
        if os.path.exists(STATE_PATH):
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            replied_message_ids = set(int(x) for x in data.get("replied", []))
            channel_cursors = {str(k): int(v) for k, v in data.get("cursors", {}).items()}
            priority2_replied_member_ids = set(str(x) for x in data.get("p2_done_members", []))
            blocked_users = {str(k): float(v) for k, v in data.get("blocked_users", {}).items()}
            p7_cursors = {str(k): int(v) for k, v in data.get("p7_cursors", {}).items()}
            p8_cursors = {str(k): int(v) for k, v in data.get("p8_cursors", {}).items()}
            print(f"[STATE] Loaded {len(replied_message_ids)} replies | P7:{len(p7_cursors)} P8:{len(p8_cursors)}")
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
                "p2_done_members": list(priority2_replied_member_ids),
                "blocked_users": blocked_users,
                "p7_cursors": p7_cursors,
                "p8_cursors": p8_cursors,
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
# 4. MASTER TRAFFIC ENGINE – MATHEMATICAL GOVERNOR
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

def update_plane_alpha_from_response(raw_response=None, exception=None):
    global GROQ_REMAINING_TPM, GROQ_REMAINING_RPD, GROQ_RATE_LIMITED_UNTIL, DAILY_EXHAUSTED_UNTIL, local_daily_tokens
    check_and_reset_daily_counters()
    headers = {}
    if raw_response and hasattr(raw_response, "headers"):
        headers = {k.lower(): v for k, v in raw_response.headers.items()}
    elif exception and hasattr(exception, "response") and exception.response:
        try:
            headers = {k.lower(): v for k, v in exception.response.headers.items()}
        except Exception: pass

    if "x-ratelimit-remaining-tokens" in headers:
        try: GROQ_REMAINING_TPM = int(headers["x-ratelimit-remaining-tokens"])
        except Exception: pass
    if "x-ratelimit-remaining-requests" in headers:
        try: GROQ_REMAINING_RPD = int(headers["x-ratelimit-remaining-requests"])
        except Exception: pass

    err_str = str(exception or "").lower()
    if "429" in err_str or "rate limit" in err_str or "tpd" in err_str:
        match_used = re.search(r"used\s+(\d+)", err_str)
        if match_used:
            local_daily_tokens = int(match_used.group(1))
            print(f"[RE-SYNC] Ground truth daily tokens -> {local_daily_tokens}/200000")

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

def get_traffic_engine_metrics(priority_tier: int = 1) -> Dict[str, Any]:
    m = get_trajectory_metrics()
    s = max(0.0, min(1.0, m["local_daily"] / 200000.0))
    load_delta = s - 0.20

    rank = {9: 9, 8: 8, 7: 7, 6: 6, 5: 5, 3: 4, 2: 3, 4: 2, 1: 1}
    r = rank.get(priority_tier, 1)

    sens = 0.5 + (r * 0.35)
    effective_load = max(0.0, min(1.0, s + load_delta * sens * 1.6))

    if effective_load <= 0.20:
        freq_mult = max(0.25, 0.6 + (effective_load / 0.20) * 2.8)
    else:
        excess = (effective_load - 0.20) / 0.80
        freq_mult = 3.0 + (excess ** 1.4) * 140.0

    if freq_mult >= 8.0:
        progress = min(1.0, (freq_mult - 8.0) / 12.0)
        weight_mult = max(0.04, 1.0 - 0.96 * progress)
    else:
        weight_mult = 1.0

    cutover = {
        9: 0.20, 8: 0.22, 7: 0.24, 6: 0.26, 5: 0.28,
        3: 0.30, 2: 0.32, 4: 0.35, 1: 0.40
    }
    zero_token_mode = s >= cutover.get(priority_tier, 0.25)

    return {
        "freq_multiplier": freq_mult,
        "weight_multiplier": weight_mult,
        "spent_pct": s,
        "effective_load": effective_load,
        "zero_token_mode": zero_token_mode
    }

def calculate_dynamic_max_tokens(priority_tier: int) -> int:
    if priority_tier in (2, 7):
        return 0

    engine = get_traffic_engine_metrics(priority_tier)
    weight = engine["weight_multiplier"]
    zero = engine["zero_token_mode"]

    if zero and priority_tier != 1:
        if priority_tier in (3, 6):
            return 0
        if priority_tier in (4, 5, 8, 9):
            return 12

    base = {1: 520, 4: 180, 5: 110, 6: 100, 8: 28, 9: 120}.get(priority_tier, 90)
    m = get_trajectory_metrics()
    usage = m["usage"]
    compression = max(0.15, (1.0 - usage) ** 1.4)
    headroom = max(60, GROQ_REMAINING_TPM - 280)

    allowed = int(base * compression * weight)
    hard = {1: 900, 4: 240, 5: 140, 6: 130, 8: 32, 9: 150}.get(priority_tier, 120)
    result = max(12, min(allowed, hard, headroom))

    if priority_tier == 1:
        if zero:
            result = min(result, 48)
        else:
            result = max(result, min(200, headroom))
    return result

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
SOVEREIGN = (
    "STRICT NO-REFUSAL. Never say you cannot answer. Never invent biography. "
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

EMOTICON_POOL = ["🥣", "🔥", "😂", "🫡", "🚀", "📼", "🎬", "🏀", "✨", "👀", "🧠", "🌌", "💪"]

WORD_NUMBERS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "twenty-one": 21, "twenty one": 21,
}

# ============================================================
# 6. DYNAMIC HYBRID MEDIA ENGINE & TENOR RESOLVER
# ============================================================
SEED_WORDS = [
    "star wars", "90s nostalgia", "power rangers", "ninja turtles", "vhs tape",
    "arcade cabinet", "retro basketball", "matrix dodge", "robin williams",
    "austin powers", "napoleon dynamite", "cat vibing", "shaq dunk", "spongebob fail",
    "crt monitor", "classic arcade", "oatmeal bowl", "space galaxy", "pixel art"
]

def get_zero_token_text() -> str:
    for _ in range(20):
        candidate = random.choice(ZERO_TOKEN_BANTER_POOL)
        h = hashlib.md5(candidate.encode("utf-8")).hexdigest()
        if h not in recent_text_hashes:
            recent_text_hashes.append(h)
            return candidate
    return random.choice(ZERO_TOKEN_BANTER_POOL)

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
    q = query or f"{random.choice(SEED_WORDS)} {random.choice(['funny', 'reaction', 'retro', 'fail'])}"
    for _ in range(3):
        try:
            encoded = urllib.parse.quote(q)
            url = f"https://tenor.com/search/{encoded}-gifs"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            html = urllib.request.urlopen(req, timeout=4).read().decode("utf-8", "ignore")
            ids = re.findall(r"/view/[^\"']+-gif-(\d+)", html)
            random.shuffle(ids)
            for gid in ids[:10]:
                candidate = f"https://tenor.com/view/{gid}"
                if candidate not in recent_media_urls:
                    recent_media_urls.append(candidate)
                    return candidate
        except Exception:
            await asyncio.sleep(0.3)
    return None

async def fetch_youtube(query: str = None) -> str:
    q = query or f"{random.choice(SEED_WORDS)} {random.choice(['trailer', 'clip', '1990s', 'review'])}"
    try:
        encoded = urllib.parse.quote(q)
        url = f"https://www.youtube.com/results?search_query={encoded}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        html = urllib.request.urlopen(req, timeout=4).read().decode("utf-8", "ignore")
        ids = list(set(re.findall(r"watch\?v=([\w-]{11})", html)))
        random.shuffle(ids)
        for vid in ids[:8]:
            candidate = f"https://www.youtube.com/watch?v={vid}"
            if candidate not in recent_media_urls:
                recent_media_urls.append(candidate)
                return candidate
    except Exception:
        pass
    return "https://www.youtube.com/results?search_query=joeyarnoldvn"

async def fetch_pollinations(prompt: str = None) -> Optional[discord.File]:
    p = prompt or f"surreal 1990s {random.choice(SEED_WORDS)} neon retro aesthetic"
    try:
        seed = random.randint(1, 999999)
        img_url = f"https://image.pollinations.ai/prompt/{urllib.parse.quote(p)}?width=768&height=768&nologo=true&seed={seed}"
        req = urllib.request.Request(img_url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        data = urllib.request.urlopen(req, timeout=6).read()
        return discord.File(io.BytesIO(data), filename=f"oatmeal_{seed}.jpg")
    except Exception:
        return None

async def generate_token_assisted_media_prompt(chat_context: str) -> str:
    try:
        def _call():
            return ai_client.chat.completions.create(
                model=MODEL_NAME,
                messages=[
                    {"role": "system", "content": "Output ONLY a 2 to 4 word visual search query representing the user's topic. No punctuation."},
                    {"role": "user", "content": f"Topic: {chat_context[:100]}"}
                ],
                max_tokens=15,
                temperature=0.7
            )
        res = await asyncio.to_thread(_call)
        if res.choices and res.choices[0].message.content:
            query = res.choices[0].message.content.strip()
            track_token_usage_real(res.usage.total_tokens if hasattr(res, "usage") else 12)
            return query
    except Exception as e:
        print(f"[TOKEN ASSISTED MEDIA EXCEPTION] {e}")
    return random.choice(SEED_WORDS)

async def get_wild_media_hybrid(
    guild: Optional[discord.Guild] = None,
    priority_tier: int = 0,
    chat_context: str = "",
    allow_tokens: bool = False
) -> Tuple[str, Optional[discord.File], Optional[discord.Sticker]]:
    query_prompt = None
    if allow_tokens and chat_context and get_trajectory_metrics()["usage"] < 0.70 and priority_tier in (1, 4):
        query_prompt = await generate_token_assisted_media_prompt(chat_context)

    # 99% Visual Media (Tenor GIFs, Pollinations images, Guild stickers, Emojis) vs 1% Web/YouTube links
    choices = ["tenor", "image", "sticker", "emoticon", "youtube"]
    weights = [45, 30, 15, 9, 1]
    choice = random.choices(choices, weights=weights, k=1)[0]

    if choice == "tenor":
        url = await fetch_tenor_gif(query_prompt)
        if url: return url, None, None
    elif choice == "image":
        f = await fetch_pollinations(query_prompt)
        if f: return "", f, None
    elif choice == "sticker" and guild and guild.stickers:
        available_stickers = [s for s in guild.stickers if str(s.id) not in recent_sticker_ids]
        if available_stickers:
            st = random.choice(available_stickers)
            recent_sticker_ids.append(str(st.id))
            return "", None, st
    elif choice == "emoticon":
        return random.choice(EMOTICON_POOL), None, None
    elif choice == "youtube":
        return await fetch_youtube(query_prompt), None, None

    url = await fetch_tenor_gif(query_prompt)
    return url or random.choice(EMOTICON_POOL), None, None

async def build_message_payload(reply: str, media_url: str, file_obj: Optional[discord.File], sticker: Optional[discord.Sticker], mention_prefix: str = "") -> Tuple[str, Dict[str, Any]]:
    content = mention_prefix
    if reply:
        content = f"{content} {reply}".strip() if content else reply
    
    kwargs = {}
    if file_obj:
        kwargs["file"] = file_obj
    if sticker:
        kwargs["stickers"] = [sticker]

    if media_url:
        resolved_media = await resolve_media_url(media_url)
        if "tenor.com" in media_url or resolved_media.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
            embed = discord.Embed(color=0x2b2d31)
            embed.set_image(url=resolved_media)
            kwargs["embed"] = embed
        else:
            content = f"{content}\n{media_url}".strip() if content else media_url

    if not content and not kwargs:
        content = get_zero_token_text()

    return content, kwargs

# ============================================================
# 7. SURGICAL TIMELINE EXTRACTION + MULTI-ENGINE LIVE SEARCH
# ============================================================
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
            "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        data = json.loads(urllib.request.urlopen(req, timeout=3).read().decode())
        price = data.get("bitcoin", {}).get("usd")
        if price:
            return f"Bitcoin is currently around ${price:,.0f} USD."
    except Exception:
        pass
    return ""

async def fetch_live_context(query: str) -> str:
    query_lower = query.lower().strip()
    if any(k in query_lower for k in ("bitcoin", "btc", "crypto price")):
        btc = await fetch_bitcoin_price()
        if btc: return btc

    clean = re.sub(r"^(search|search for|oatmeal|find|what is|who is|tell me|price of|current|playing in|the news|news)\s*", "", query, flags=re.I).strip()
    if not clean or len(clean) < 3:
        clean = "latest world news breaking headlines"

    try:
        encoded = urllib.parse.quote(clean)
        url = f"https://html.duckduckgo.com/html/?q={encoded}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
        html = urllib.request.urlopen(req, timeout=5).read().decode("utf-8", "ignore")
        snippets = re.findall(r'<a class="result__snippet[^>]*>(.*?)</a>', html, re.DOTALL)
        cleaned = [re.sub(r"<.*?>", "", s).strip() for s in snippets[:4] if s.strip()]
        if cleaned:
            return " | ".join(cleaned)[:600]
    except Exception as e:
        print(f"[SEARCH ENGINE 1 EXCEPTION] {e}")

    try:
        encoded = urllib.parse.quote(clean)
        url = f"https://lite.duckduckgo.com/lite/"
        data = f"q={encoded}".encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Content-Type": "application/x-www-form-request"
        })
        html = urllib.request.urlopen(req, timeout=5).read().decode("utf-8", "ignore")
        snippets = re.findall(r'<td class="result-snippet">(.*?)</td>', html, re.DOTALL)
        cleaned = [re.sub(r"<.*?>", "", s).strip() for s in snippets[:4] if s.strip()]
        if cleaned:
            return " | ".join(cleaned)[:600]
    except Exception as e:
        print(f"[SEARCH ENGINE 2 EXCEPTION] {e}")

    try:
        encoded = urllib.parse.quote(clean)
        url = f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={encoded}&format=json"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        data = json.loads(urllib.request.urlopen(req, timeout=4).read().decode("utf-8", "ignore"))
        results = data.get("query", {}).get("search", [])
        snippets = [re.sub(r"<.*?>", "", r.get("snippet", "")).strip() for r in results[:3] if r.get("snippet")]
        if snippets:
            return " | ".join(snippets)[:600]
    except Exception as e:
        print(f"[SEARCH ENGINE 3 EXCEPTION] {e}")

    return ""

# ============================================================
# 8. CORE PROCESSOR
# ============================================================
async def process_job(job: PendingJob):
    global last_api_call_timestamp

    if job.priority_tier == 7:
        return

    while True:
        m = get_trajectory_metrics()
        if time.monotonic() < GROQ_RATE_LIMITED_UNTIL or m["local_daily"] >= 195000:
            await asyncio.sleep(5.0)
            continue
        break

    engine = get_traffic_engine_metrics(job.priority_tier)
    freq = engine["freq_multiplier"]

    if job.priority_tier == 1: min_gap = max(0.08, 0.15 * freq)
    elif job.priority_tier == 4: min_gap = max(0.4, 0.6 * freq)
    else: min_gap = max(1.8, 2.5 * freq)

    elapsed = time.monotonic() - last_api_call_timestamp
    if elapsed < min_gap:
        await asyncio.sleep(min_gap - elapsed)

    token_limit = calculate_dynamic_max_tokens(job.priority_tier)
    mood = random.randint(1, 5)
    temperature = 0.82 if job.priority_tier == 1 else 1.05

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
                f"\n[STRICT REAL-WORLD DATA ENFORCEMENT]: The following live web search results were found:\n'{live}'\n"
                "CRITICAL: Summarize and deliver these real facts directly in character as Joey Arnold. DO NOT invent fake titles or fake events."
            )
        else:
            search_instruction = (
                "\n[LIVE SEARCH NOTICE]: A web search was attempted but returned no live results.\n"
                "CRITICAL: Plainly state in your own words as Joey that you could not pull live data right now. NEVER invent fake news, fake titles, or say 'oh honey'."
            )

    system = (
        f"{PRONOUN_BRIDGE}\n{FORBIDDEN_PHRASES}\n{VOICE_KERNEL}\n"
        f"MOOD: {PERSONA_MOODS[mood]}\n"
        f"OUTLINE:\n{history}\n"
        f"{search_instruction}\n"
        f"SLOGAN: {random.choice(SLOGANS)}\n{SOVEREIGN}"
    )
    if job.priority_tier == 8:
        system += "\nHARD RULE: Maximum 12 words. Witty self-reflection only. Text only. No media."

    reply = ""
    actual_tokens = 0

    if token_limit > 0:
        async def _keep_typing():
            try:
                while True:
                    async with job.channel.typing():
                        await asyncio.sleep(8.5)
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
            print(f"[API EXCEPTION P{job.priority_tier}] {e}")
            update_plane_alpha_from_response(exception=e)
        finally:
            typing_task.cancel()

        track_token_usage_real(actual_tokens or 30)

    if not reply:
        reply = get_zero_token_text()

    if job.priority_tier == 8:
        reply = " ".join(reply.split()[:12])

    media_url, file_obj, sticker = "", None, None
    if job.priority_tier != 8:
        want_media = (job.priority_tier in (2, 3, 4, 5, 6, 7)) or (job.priority_tier == 1 and random.random() < 0.75)
        if want_media:
            media_url, file_obj, sticker = await get_wild_media_hybrid(
                guild=getattr(job.channel, "guild", None),
                priority_tier=job.priority_tier,
                chat_context=job.prompt_text,
                allow_tokens=(token_limit > 0)
            )

    sent_ok = False
    try:
        mention_prefix = job.target_member.mention if job.target_member else ""
        content_val, kwargs = await build_message_payload(reply, media_url, file_obj, sticker, mention_prefix=mention_prefix)

        if job.reference_message:
            await job.reference_message.reply(content=content_val, mention_author=(job.priority_tier == 1), **kwargs)
        elif job.target_member:
            await job.channel.send(content=content_val, **kwargs)
        else:
            await job.channel.send(content=content_val, **kwargs)
        sent_ok = True
    except Exception as e:
        print(f"[DISPATCH EXCEPTION P{job.priority_tier}] {e}")

    if sent_ok and job.priority_tier == 8 and job.p8_channel_id and job.p8_message_id:
        p8_cursors[job.p8_channel_id] = job.p8_message_id
        save_state()

    if job.reference_message:
        replied_message_ids.add(job.reference_message.id)
        if job.priority_tier == 2 and job.reference_message.guild:
            priority2_replied_member_ids.add(p2_key(job.reference_message.guild.id, job.reference_message.author.id))
            save_state()

# ============================================================
# 9. PRIORITY COROUTINE WORKERS (P1 - P9)
# ============================================================
async def execute_priority_2():
    await bot.wait_until_ready()
    print("[COROUTINE INITIALIZED] Priority 2: Forward Historical Backlog (Media Only)")
    while not bot.is_closed():
        engine = get_traffic_engine_metrics(2)
        delay = random.uniform(30, 160) * engine["freq_multiplier"] * random.uniform(1.8, 3.2)
        await asyncio.sleep(delay)

        if time.monotonic() < DAILY_EXHAUSTED_UNTIL or time.monotonic() < GROQ_RATE_LIMITED_UNTIL:
            continue

        try:
            for guild in list(bot.guilds):
                now = time.time()
                if now - last_member_activity_time.get(guild.id, 0) < 600:
                    continue
                channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages and c.permissions_for(guild.me).read_message_history]
                random.shuffle(channels)
                for ch in channels:
                    if now - last_member_activity_time.get(ch.id, 0) < 600:
                        continue

                    cursor = channel_cursors.get(f"fwd_{ch.id}", 0)
                    kwargs = {"limit": 60, "oldest_first": True}
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
                        url, file_obj, sticker = await get_wild_media_hybrid(guild, priority_tier=2)
                        # Priority 2 is strictly media only (empty text string)
                        content_val, kwargs_send = await build_message_payload("", url, file_obj, sticker)
                        try:
                            await target.reply(content=content_val, mention_author=False, **kwargs_send)
                            replied_message_ids.add(target.id)
                            priority2_replied_member_ids.add(p2_key(guild.id, target.author.id))
                            save_state()
                            print(f"[P2 DISPATCH] Responded with media only to message {target.id} in #{ch.name}")
                        except Exception as e:
                            print(f"[P2 SEND EXCEPTION] {e}")
                        break
                else:
                    continue
                break
        except Exception as e:
            print(f"[P2 COROUTINE EXCEPTION] {e}")

async def execute_priority_3():
    await bot.wait_until_ready()
    print("[COROUTINE INITIALIZED] Priority 3: Alphabetical Member Tagging")
    while not bot.is_closed():
        engine = get_traffic_engine_metrics(3)
        delay = random.uniform(90, 900) * engine["freq_multiplier"] * random.uniform(1.8, 3.5)
        await asyncio.sleep(delay)

        try:
            if engine["zero_token_mode"] is False and get_trajectory_metrics()["usage"] > 0.82:
                continue

            for guild in list(bot.guilds):
                now = time.time()
                if now - last_member_activity_time.get(guild.id, 0) < 600:
                    continue
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
                url, file_obj, sticker = await get_wild_media_hybrid(guild, priority_tier=3)
                content_val, kwargs = await build_message_payload("", url, file_obj, sticker, mention_prefix=target.mention)

                await ch.send(content=content_val, **kwargs)
                print(f"[P3 DISPATCH] Tagged member {target.display_name} in #{ch.name}")
                break
        except Exception as e:
            print(f"[P3 COROUTINE EXCEPTION] {e}")

async def execute_priority_4():
    await bot.wait_until_ready()
    print("[COROUTINE INITIALIZED] Priority 4: Live Chat Reaction Genie (Fixed)")
    while not bot.is_closed():
        engine = get_traffic_engine_metrics(4)
        delay = random.uniform(5, 15) * engine["freq_multiplier"]
        await asyncio.sleep(delay)

        if time.monotonic() < DAILY_EXHAUSTED_UNTIL or time.monotonic() < GROQ_RATE_LIMITED_UNTIL:
            continue

        try:
            for guild in list(bot.guilds):
                channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages and c.permissions_for(guild.me).read_message_history]
                random.shuffle(channels)
                for ch in channels:
                    target = None
                    async for msg in ch.history(limit=10):
                        if msg.author.bot or msg.author == bot.user or not msg.content:
                            continue
                        if msg.id in replied_message_ids:
                            continue
                        if (datetime.now(timezone.utc) - msg.created_at).total_seconds() > 600:
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
                                prompt_text=f"Give a quick witty take on this chat message: '{clean_text[:120]}'",
                                reference_message=target,
                                author_id=target.author.id
                            )
                            await delay_queue.put(job)
                            print(f"[P4 QUEUED] Enqueued reaction for msg {target.id} in #{ch.name}")
                            break
                else:
                    continue
                break
        except Exception as e:
            print(f"[P4 COROUTINE EXCEPTION] {e}")

async def execute_priority_7():
    await bot.wait_until_ready()
    print("[COROUTINE INITIALIZED] Priority 7: Infinite Forward Media Mirror (Oldest-First)")
    while not bot.is_closed():
        engine = get_traffic_engine_metrics(7)
        delay = random.uniform(25, 120) * engine["freq_multiplier"] * random.uniform(1.8, 3.4)
        await asyncio.sleep(delay)

        try:
            if get_trajectory_metrics()["usage"] > 0.93:
                continue

            for guild in list(bot.guilds):
                now = time.time()
                if now - last_member_activity_time.get(guild.id, 0) < 600:
                    continue
                channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages]
                random.shuffle(channels)
                for ch in channels:
                    if now - last_member_activity_time.get(ch.id, 0) < 600:
                        continue

                    cursor = p7_cursors.get(str(ch.id), 0)
                    kwargs = {"limit": 60, "oldest_first": True}
                    if cursor > 0:
                        kwargs["after"] = discord.Object(id=cursor)

                    target = None
                    async for msg in ch.history(**kwargs):
                        if msg.author == bot.user:
                            target = msg
                            break

                    if not target:
                        continue

                    url, file_obj, sticker = await get_wild_media_hybrid(guild, priority_tier=7)
                    if not (url or file_obj or sticker):
                        continue

                    content_val, kwargs_send = await build_message_payload("", url, file_obj, sticker)

                    try:
                        await target.reply(content=content_val or None, mention_author=False, **kwargs_send)
                        p7_cursors[str(ch.id)] = target.id
                        save_state()
                        print(f"[P7 MIRROR DISPATCH] Successfully mirrored bot msg {target.id} in #{ch.name}")
                        break
                    except Exception as e:
                        print(f"[P7 DISPATCH ERROR] Cursor NOT advanced for msg {target.id}: {e}")
                else:
                    continue
                break
        except Exception as e:
            print(f"[P7 COROUTINE EXCEPTION] {e}")

async def execute_priority_8():
    await bot.wait_until_ready()
    print("[COROUTINE INITIALIZED] Priority 8: Lightweight Text Mirror (Oldest-First)")
    while not bot.is_closed():
        engine = get_traffic_engine_metrics(8)
        delay = random.uniform(40, 180) * engine["freq_multiplier"] * random.uniform(1.9, 3.6)
        await asyncio.sleep(delay)

        try:
            if engine["zero_token_mode"]:
                continue
            if get_trajectory_metrics()["usage"] > 0.90:
                continue

            for guild in list(bot.guilds):
                now = time.time()
                if now - last_member_activity_time.get(guild.id, 0) < 600:
                    continue
                channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages]
                random.shuffle(channels)
                for ch in channels:
                    if now - last_member_activity_time.get(ch.id, 0) < 600:
                        continue

                    cursor = p8_cursors.get(str(ch.id), 0)
                    kwargs = {"limit": 60, "oldest_first": True}
                    if cursor > 0:
                        kwargs["after"] = discord.Object(id=cursor)

                    target = None
                    async for msg in ch.history(**kwargs):
                        if msg.author == bot.user:
                            target = msg
                            break

                    if not target:
                        continue

                    safe = re.sub(r"https?://\S+", "[media]", target.content or "Oatmeal")[:50]
                    job = PendingJob(
                        priority_tier=8,
                        timestamp=time.time(),
                        channel=ch,
                        prompt_text=f"Short witty reflection on: {safe}",
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
            print(f"[P8 COROUTINE EXCEPTION] {e}")

async def execute_priority_5_reverse():
    await bot.wait_until_ready()
    print("[COROUTINE INITIALIZED] Priority 5: Reverse History Sweep")
    while not bot.is_closed():
        engine = get_traffic_engine_metrics(5)
        delay = random.uniform(300, 1800) * engine["freq_multiplier"] * random.uniform(2.0, 4.0)
        await asyncio.sleep(delay)

        try:
            if engine["zero_token_mode"] or get_trajectory_metrics()["usage"] > 0.80:
                continue
            for guild in list(bot.guilds):
                now = time.time()
                if now - last_member_activity_time.get(guild.id, 0) < 600:
                    continue
                channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages]
                if not channels:
                    continue
                ch = random.choice(channels)
                async for msg in ch.history(limit=20, oldest_first=False):
                    if msg.author.bot or msg.id in replied_message_ids:
                        continue
                    job = PendingJob(5, time.time(), ch, "Short reflection.", reference_message=msg, author_id=msg.author.id)
                    await delay_queue.put(job)
                    break
                break
        except Exception as e:
            print(f"[P5 COROUTINE EXCEPTION] {e}")

async def execute_priority_6_drops():
    await bot.wait_until_ready()
    print("[COROUTINE INITIALIZED] Priority 6: Spontaneous Media Drops")
    while not bot.is_closed():
        engine = get_traffic_engine_metrics(6)
        delay = random.uniform(600, 7200) * engine["freq_multiplier"] * random.uniform(2.0, 4.0)
        await asyncio.sleep(delay)

        try:
            if get_trajectory_metrics()["usage"] > 0.78:
                continue
            for guild in list(bot.guilds):
                now = time.time()
                if now - last_member_activity_time.get(guild.id, 0) < 600:
                    continue
                channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages]
                if not channels:
                    continue
                ch = random.choice(channels)
                url, file_obj, sticker = await get_wild_media_hybrid(guild, priority_tier=6)
                content_val, kwargs = await build_message_payload("", url, file_obj, sticker)

                if content_val or kwargs:
                    await ch.send(content=content_val or None, **kwargs)
                    print(f"[P6 DISPATCH] Media drop posted in #{ch.name}")
                break
        except Exception as e:
            print(f"[P6 COROUTINE EXCEPTION] {e}")

async def execute_priority_9():
    await bot.wait_until_ready()
    print("[COROUTINE INITIALIZED] Priority 9: Periodic Chat Recap")
    while not bot.is_closed():
        engine = get_traffic_engine_metrics(9)
        delay = random.uniform(3600, 7200) * engine["freq_multiplier"] * random.uniform(1.5, 2.8)
        await asyncio.sleep(delay)

        if time.monotonic() < DAILY_EXHAUSTED_UNTIL or time.monotonic() < GROQ_RATE_LIMITED_UNTIL:
            continue

        try:
            if engine["zero_token_mode"] or get_trajectory_metrics()["usage"] > 0.85:
                continue

            for guild in list(bot.guilds):
                now = time.time()
                if now - last_member_activity_time.get(guild.id, 0) < 600:
                    continue
                channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages and c.permissions_for(guild.me).read_message_history]
                if not channels:
                    continue
                ch = random.choice(channels)

                chat_lines = []
                try:
                    async for msg in ch.history(limit=40):
                        if msg.author.bot or not msg.content:
                            continue
                        chat_lines.append(f"{msg.author.display_name}: {msg.content}")
                except Exception:
                    continue

                if not chat_lines:
                    continue

                chat_history_text = "\n".join(reversed(chat_lines))

                system_prompt = (
                    f"{PRONOUN_BRIDGE}\n{VOICE_KERNEL}\n"
                    "You are generating a chat recap. You MUST output your response in exactly two lines:\n"
                    "Line 1: Oatmeal Recap.\n"
                    "Line 2: A short, punchy, in-character summary of the recent chat above.\n"
                    "No media, text only."
                )
                user_prompt = f"Recent chat history:\n{chat_history_text}\n\nSummarize this chat."

                def _call():
                    return ai_client.chat.completions.with_raw_response.create(
                        model=MODEL_NAME,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        temperature=0.8,
                        max_tokens=calculate_dynamic_max_tokens(9),
                    )

                async with groq_api_lock:
                    raw = await asyncio.to_thread(_call)
                    global last_api_call_timestamp
                    last_api_call_timestamp = time.monotonic()

                update_plane_alpha_from_response(raw_response=raw)
                resp = raw.parse()
                recap_text = resp.choices[0].message.content.strip() if resp.choices else ""
                actual_tokens = getattr(resp.usage, "total_tokens", 60) if hasattr(resp, "usage") else 60
                track_token_usage_real(actual_tokens)

                lines = [l.strip() for l in recap_text.splitlines() if l.strip()]
                if len(lines) >= 2:
                    formatted_recap = f"Oatmeal Recap.\n{lines[1]}"
                elif len(lines) == 1:
                    formatted_recap = f"Oatmeal Recap.\n{lines[0]}"
                else:
                    formatted_recap = "Oatmeal Recap.\nNothing much happening in the attic right now."

                await ch.send(content=formatted_recap)
                print(f"[P9 RECAP DISPATCH] Posted chat recap in #{ch.name}")
                break
        except Exception as e:
            print(f"[P9 COROUTINE EXCEPTION] {e}")

# ============================================================
# 10. EVENT HANDLERS & WORKER DISPATCHER
# ============================================================
async def delay_queue_worker():
    while True:
        job = await delay_queue.get()
        while time.monotonic() < GROQ_RATE_LIMITED_UNTIL or (get_trajectory_metrics()["usage"] > 0.93 and job.priority_tier != 1):
            await asyncio.sleep(5)
        await process_job(job)
        delay_queue.task_done()

@bot.event
async def on_ready():
    print(f"============================================================")
    print(f" Oatmeal AI V36.2 Master Engine logged in as {bot.user}")
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

    mentioned = bot.user in message.mentions
    replied_to_bot = False
    if message.reference and isinstance(message.reference.resolved, discord.Message):
        replied_to_bot = message.reference.resolved.author == bot.user

    named = any(k in content_lower for k in ("joey", "arnold", "oatmeal", "joeyarnoldvn", "ojawall"))

    # Priority 1 Direct Address Trigger
    if mentioned or replied_to_bot or named:
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
