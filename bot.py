#!/usr/init/env python3
"""
Oatmeal AI – Joey Arnold Discord Bot (#4059)
Master Production Version 56.0 ("HEART BEAT" Default Schedule & Delay Integration)

- Integrated exact "HEART BEAT" Default Schedules and Default Delays per priority tier.
- Regulated Action Types (TOKEN-SPENDING vs TOKEN-FREE) and Per-Action Protocols (TEXT-ONLY, MEDIA-ONLY, MEDIA/TEXT).
- Maintained forward-chronological native Discord history cursor sweepers for P2, P7, P8, P9.
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
        self.wfile.write(b"Oatmeal AI V56.0 -- Heart Beat Schedules & Delays Active")
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
p2_replied_member_ids: Set[int] = set()
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
    global replied_message_ids, p2_replied_member_ids, channel_cursors
    global bot_media_cursors, bot_text_cursors, recap_cursors, blocked_users
    try:
        if os.path.exists(STATE_PATH):
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            replied_message_ids = set(int(x) for x in data.get("replied", []))
            p2_replied_member_ids = set(int(x) for x in data.get("p2_replied_members", []))
            channel_cursors = {str(k): int(v) for k, v in data.get("cursors", {}).items()}
            bot_media_cursors = {str(k): int(v) for k, v in data.get("bot_media_cursors", {}).items()}
            bot_text_cursors = {str(k): int(v) for k, v in data.get("bot_text_cursors", {}).items()}
            recap_cursors = {str(k): int(v) for k, v in data.get("recap_cursors", {}).items()}
            blocked_users = {str(k): float(v) for k, v in data.get("blocked_users", {}).items()}
            print(f"[STATE] Loaded {len(replied_message_ids)} replied IDs | P2 Members: {len(p2_replied_member_ids)}")
    except Exception as e:
        print(f"[STATE] Fresh start initialized: {e}")

def save_state():
    def _write():
        try:
            dir_name = os.path.dirname(STATE_PATH) or "/tmp"
            os.makedirs(dir_name, exist_ok=True)
            data = {
                "replied": list(replied_message_ids)[-50000:],
                "p2_replied_members": list(p2_replied_member_ids)[-10000:],
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
# 4. HEART BEAT SPECIFICATIONS (DEFAULT SCHEDULES & DELAYS)
# ============================================================
PRIORITY_SPECS = {
    1: {
        "dsn": 1000, "dsl": 8000, "action_type": "TOKEN-SPENDING", "option": "MEDIA/TEXT", "search": True,
        "delay_min": 0, "delay_max": 36000, "schedule_min": 0, "schedule_max": 86400
    },
    4: {
        "dsn": 950,  "dsl": 2000, "action_type": "TOKEN-SPENDING", "option": "MEDIA/TEXT", "search": True,
        "delay_min": 10, "delay_max": 90000, "schedule_min": 0, "schedule_max": 90000
    },
    6: {
        "dsn": 0,    "dsl": 700,  "action_type": "TOKEN-FREE",     "option": "MEDIA-ONLY", "search": False,
        "delay_min": 20, "delay_max": 93600, "schedule_min": 600, "schedule_max": 1800
    },
    7: {
        "dsn": 0,    "dsl": 400,  "action_type": "TOKEN-FREE",     "option": "MEDIA-ONLY", "search": False,
        "delay_min": 120, "delay_max": 97200, "schedule_min": 600, "schedule_max": 1800
    },
    8: {
        "dsn": 800,  "dsl": 1000, "action_type": "TOKEN-SPENDING", "option": "TEXT-ONLY",  "search": False,
        "delay_min": 180, "delay_max": 100800, "schedule_min": 600, "schedule_max": 1800
    },
    9: {
        "dsn": 1000, "dsl": 3000, "action_type": "TOKEN-SPENDING", "option": "TEXT-ONLY",  "search": False,
        "delay_min": 36000, "delay_max": 104400, "schedule_min": 18000, "schedule_max": 36000
    },
    2: {
        "dsn": 0,    "dsl": 700,  "action_type": "TOKEN-FREE",     "option": "MEDIA-ONLY", "search": False,
        "delay_min": 1800, "delay_max": 108000, "schedule_min": 6000, "schedule_max": 6000
    },
    5: {
        "dsn": 0,    "dsl": 700,  "action_type": "TOKEN-FREE",     "option": "MEDIA-ONLY", "search": False,
        "delay_min": 2400, "delay_max": 111600, "schedule_min": 6000, "schedule_max": 6600
    },
    3: {
        "dsn": 0,    "dsl": 700,  "action_type": "TOKEN-FREE",     "option": "MEDIA-ONLY", "search": False,
        "delay_min": 3000, "delay_max": 115200, "schedule_min": 6000, "schedule_max": 7200
    },
}

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
        "User-Agent": "OatmealAI/56.0"
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
            msg_obj = body.get("choices", [{}])[0].get("message", {})
            text = msg_obj.get("content", "")
            if not text and "tool_calls" in msg_obj:
                text = "What in the Oatmeal tool call."
            text = text.strip()
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
    m = get_trajectory_metrics()
    s = max(0.0, min(1.0, m["local_daily"] / 200000.0))
    scaling_step = round(s * 100.0) / 100.0

    rank_order = {1: 1, 4: 2, 6: 3, 7: 4, 8: 5, 9: 6, 2: 7, 5: 8, 3: 9}
    r = rank_order.get(priority_tier, 5)

    effective_load = max(0.0, min(1.0, scaling_step + (scaling_step * 0.25 * (r / 9.0))))

    if effective_load <= 0.30:
        freq_mult = max(0.35, 0.7 + (effective_load / 0.30) * 0.8) * (r * 0.15 + 0.85)
    else:
        excess = (effective_load - 0.30) / 0.70
        freq_mult = (1.5 + (excess ** 1.2) * 2.0) * (r * 0.1 + 0.9)

    if priority_tier in (7, 8, 9):
        freq_mult = min(freq_mult, 1.5)
    else:
        freq_mult = min(freq_mult, 3.0)

    weight_mult = max(0.10, 1.0 - (effective_load * 0.75))

    return {
        "freq_multiplier": freq_mult,
        "weight_multiplier": weight_mult,
        "spent_pct": s,
        "scaling_step": scaling_step,
        "effective_load": effective_load
    }

def calculate_dynamic_max_tokens(priority_tier: int) -> int:
    spec = PRIORITY_SPECS.get(priority_tier, {"dsn": 500, "dsl": 1000, "action_type": "TOKEN-FREE"})
    if spec["action_type"] == "TOKEN-FREE":
        return 0

    dsl = spec["dsl"]
    dsn = spec["dsn"]

    engine = get_traffic_engine_metrics(priority_tier)
    weight = engine["weight_multiplier"]
    
    base = min(dsl, max(dsn // 4, 70))
    if priority_tier == 1:
        base = min(800, dsl // 2)
    elif priority_tier == 4:
        base = min(300, dsl // 2)

    m = get_trajectory_metrics()
    usage = m["usage"]
    compression = max(0.25, (1.0 - usage) ** 1.1)
    
    allowed = int(base * compression * weight)
    hard_cap = min(dsl, GROQ_REMAINING_TPM - 200)
    return max(20, min(allowed, hard_cap))

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
2017: Slept at Opera Tea Club. YouTube ban. Flew home on Thanksgiving Day. Saw The Simpsons, Power Rangers, and Blossom. Got Bitcoin. Joined Discord and Steemit. Began blogging on Hive and Steemit.
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
    "Speak as Joey Arnold: authentic 90s Oregon energy, raw, humorous, direct. "
    "NEVER output JSON tool calls, function calls, or tool syntax. Output plain text only."
)
VOICE_KERNEL = (
    "Prefer short punchy sentences (under 12 words when possible). "
    "Longer only when the question truly needs depth. "
    "Puns, sarcasm, hyperbole, 1990s nostalgia, Star Wars, occasional Scripture flicker. "
    "Never invent mundane biography or make up real-world news/facts."
)

ANTI_HALLUCINATION_GUARD = (
    "STRICT TRUTH & ANTI-FABRICATION RULE: "
    "You must rely *strictly* and *only* on the provided outline text when answering personal biography or historical questions. "
    "When live search results are provided for real-world queries (like Bitcoin prices, current news, or web searches), you must use the live search data accurately."
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

# ============================================================
# 6. ZERO-TOKEN / LOW-TOKEN TIMELINE MATCHER
# ============================================================
def try_zero_token_timeline_response(prompt: str) -> Optional[str]:
    if not prompt:
        return None
    prompt_lower = prompt.lower()
    
    match = re.search(r"\b(19\d{2}|20\d{2})\b", prompt)
    if match:
        year = match.group(1)
        for line in FULL_ANNUAL_OUTLINE.strip().splitlines():
            if line.startswith(f"{year}:"):
                content = line[len(year)+1:].strip()
                return f"Back in {year}, {content}"

    locations = ["california", "vietnam", "saigon", "forest grove", "shelton", "portland", "hawaii", "new york", "west virginia"]
    for loc in locations:
        if loc in prompt_lower:
            matching_lines = []
            for line in FULL_ANNUAL_OUTLINE.strip().splitlines():
                if loc in line.lower():
                    matching_lines.append(line)
            if matching_lines:
                chosen = random.choice(matching_lines)
                return f"Ah, talking about {loc.title()}? {chosen}"

    return None

# ============================================================
# 7. ROBUST DUAL-MODE MEDIA ENGINE WITH RETRY & CATEGORY ROTATION
# ============================================================
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

async def fetch_video_media(query: str = None) -> str:
    rand_chars = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
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

async def fetch_gif_media(query: str = None) -> Optional[str]:
    rand_chars = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
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
    return "https://tenor.com/view/cat-vibing-gif-22441235"

async def fetch_photo_media() -> str:
    try:
        query = random.choice(SEED_WORDS)
        encoded = urllib.parse.quote(query)
        url = f"https://commons.wikimedia.org/w/api.php?action=query&generator=search&gsrsearch={encoded}&gsrnamespace=6&gsrlimit=10&prop=imageinfo&iiprop=url&format=json"
        req = urllib.request.Request(url, headers={"User-Agent": "OatmealAI/56.0"})
        data = json.loads(urllib.request.urlopen(req, timeout=4).read().decode("utf-8", "ignore"))
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
    except Exception:
        pass

    seed_id = random.randint(10, 99999)
    fallback_url = f"https://picsum.photos/seed/{seed_id}/1280/720"
    if fallback_url not in recent_media_urls:
        recent_media_urls.append(fallback_url)
    return fallback_url

async def fetch_drawing_media(prompt: str = None) -> Optional[discord.File]:
    rand_chars = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    base_p = prompt or f"surreal 1990s {random.choice(SEED_WORDS)} neon retro aesthetic illustration with Joey Arnold cinematic lighting"
    p = f"{base_p} variant {rand_chars}"
    try:
        seed = random.randint(1, 99999999)
        img_url = f"https://image.pollinations.ai/prompt/{urllib.parse.quote(p)}?width=768&height=768&nologo=true&seed={seed}"
        req = urllib.request.Request(img_url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        data = urllib.request.urlopen(req, timeout=6).read()
        return discord.File(io.BytesIO(data), filename=f"oatmeal_art_{seed}.jpg")
    except Exception:
        return None

async def fetch_audio_media() -> str:
    rand_chars = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    track_id = "".join(random.choices(string.ascii_lowercase + string.ascii_uppercase + string.digits, k=22))
    genre = random.choice(["90s+hip+hop", "retro+synthwave", "classic+rock", "lofi+beats", "ambient+jazz", "grunge+punk"])
    tracks = [
        f"https://open.spotify.com/track/{track_id}?ref={rand_chars}",
        f"https://soundcloud.com/search?q={genre}+{rand_chars}",
        f"https://bandcamp.com/tag/{genre}"
    ]
    cand = random.choice(tracks)
    if cand not in recent_media_urls:
        recent_media_urls.append(cand)
        return cand
    return cand

async def fetch_weblink_media() -> str:
    try:
        req = urllib.request.Request("https://en.wikipedia.org/api/rest_v1/page/random/summary", headers={"User-Agent": "OatmealAI/56.0"})
        data = json.loads(urllib.request.urlopen(req, timeout=4).read().decode("utf-8", "ignore"))
        wiki_url = data.get("content_urls", {}).get("desktop", {}).get("page")
        if wiki_url and wiki_url not in recent_media_urls:
            recent_media_urls.append(wiki_url)
            return wiki_url
    except Exception:
        pass
    return "https://archive.org/details/softwarelibrary_msdos_games"

async def fetch_ascii_art() -> str:
    ascii_pool = [
        "```text\n  /\\_/\\\n ( o.o )\n  > ^ <   Oatmeal Cat\n```",
        "```text\n  ____  _                  _\n / ___|| |__   ___   ___  | |\n \\___ \\| '_ \\ / _ \\ / _ \\ | |\n  ___) | | | | (_) | (_) || |\n |____/|_| |_|\\___/ \\___/ |_|\n```",
        "```text\n    ____\n   /\\'u`\\\n  (_(_/(_)\n  Retro NES Cart\n```",
        "```text\n  (o_o)  Joey's Attic Camcorder\n  //|\\\\\n  / ^ \\\n```"
    ]
    return random.choice(ascii_pool)

async def fetch_emoticon() -> str:
    emoticons = ["¯\\_(ツ)_/¯", "(▀̿ĹM▀̿ ̿)", "( ͡° ͜ʖ ͡°)", "ʕ•ᴥ•ʔ", "(=^ ^=)", "\\m/(>_<)\\m/", "(╯°□°)╯︵ ┻━┻", "V(^_^)V", "<(^_^)>"]
    return random.choice(emoticons)

async def fetch_guild_sticker(guild: Optional[discord.Guild]) -> Optional[discord.Sticker]:
    if guild and guild.stickers:
        try:
            return random.choice(guild.stickers)
        except Exception:
            pass
    return None

async def get_wild_media_hybrid(
    guild: Optional[discord.Guild] = None,
    priority_tier: int = 0,
    chat_context: str = "",
    mode: str = "RANDOM",
    category_override: Optional[int] = None
) -> Tuple[str, Optional[discord.File], Optional[discord.Sticker]]:
    if random.random() < 0.05 and not category_override:
        st = await fetch_guild_sticker(guild)
        if st:
            return "", None, st

    cat_choice = category_override if category_override is not None else random.randint(1, 8)
    if cat_choice == 1:
        return await fetch_video_media(chat_context if mode == "CUSTOM" else None), None, None
    elif cat_choice == 2:
        return await fetch_gif_media(chat_context if mode == "CUSTOM" else None), None, None
    elif cat_choice == 3:
        return await fetch_photo_media(), None, None
    elif cat_choice == 4:
        drawing = await fetch_drawing_media(chat_context if mode == "CUSTOM" else None)
        if drawing:
            return "", drawing, None
        return await fetch_photo_media(), None, None
    elif cat_choice == 5:
        return await fetch_audio_media(), None, None
    elif cat_choice == 6:
        return await fetch_weblink_media(), None, None
    elif cat_choice == 7:
        return await fetch_ascii_art(), None, None
    elif cat_choice == 8:
        return await fetch_emoticon(), None, None

    return await fetch_photo_media(), None, None

async def build_message_payload(reply: str, media_url: str, file_obj: Optional[discord.File], sticker: Optional[discord.Sticker], mention_prefix: str = "", priority_tier: int = 1) -> Tuple[str, Dict[str, Any]]:
    spec = PRIORITY_SPECS.get(priority_tier, {"option": "MEDIA/TEXT"})
    option = spec["option"]

    clean_reply = sanitize_reply_text(reply) if option in ("TEXT-ONLY", "MEDIA/TEXT") else ""
    content = mention_prefix if mention_prefix else ""
    
    if clean_reply:
        content = f"{content} {clean_reply}".strip() if content else clean_reply

    kwargs = {}
    if option in ("MEDIA-ONLY", "MEDIA/TEXT"):
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
                else:
                    content = f"{content}\n{media_url}".strip() if content else media_url

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
        req = urllib.request.Request("[https://api.coinbase.com/v2/prices/BTC-USD/spot](https://api.coinbase.com/v2/prices/BTC-USD/spot)", headers={"User-Agent": "Mozilla/5.0"})
        data = json.loads(urllib.request.urlopen(req, timeout=4).read().decode())
        amount = data.get("data", {}).get("amount")
        if amount:
            return f"Bitcoin is currently around ${float(amount):,.2f} USD."
    except Exception as e:
        print(f"[BITCOIN EXCEPTION] {e}")
    return ""

async def fetch_wikipedia_search(query: str) -> str:
    try:
        encoded = urllib.parse.quote(query)
        url = f"[https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch=](https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch=){encoded}&format=json"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        data = json.loads(urllib.request.urlopen(req, timeout=4).read().decode("utf-8", "ignore"))
        snippets = [re.sub(r"<.*?>", "", item.get("snippet", "")) for item in data.get("query", {}).get("search", [])[:3]]
        if snippets:
            return " | ".join(snippets)
    except Exception as e:
        print(f"[WIKI EXCEPTION] {e}")
    return ""

async def fetch_live_context(query: str) -> str:
    query_lower = query.lower().strip()
    if any(k in query_lower for k in ("bitcoin", "btc", "crypto price", "price of bitcoin")):
        btc = await fetch_bitcoin_price()
        if btc: return btc

    clean = re.sub(r"^(search|search for|google|look|find|locate|duckduckgo|oatmeal|what is|who is|tell me|price of|current|playing in|the news|news)\s*", "", query, flags=re.I).strip()
    if not clean or len(clean) < 2:
        clean = query or "latest world news breaking headlines"

    try:
        encoded = urllib.parse.quote(clean)
        url = f"[https://html.duckduckgo.com/html/?q=](https://html.duckduckgo.com/html/?q=){encoded}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        html = urllib.request.urlopen(req, timeout=5).read().decode("utf-8", "ignore")
        snippets = re.findall(r'<a class="result__snippet[^>]*>(.*?)</a>', html, re.DOTALL)
        cleaned = [re.sub(r"<.*?>", "", s).strip() for s in snippets[:4] if s.strip()]
        if cleaned:
            return " | ".join(cleaned)[:600]
    except Exception as e:
        print(f"[DDG EXCEPTION] {e}")

    return await fetch_wikipedia_search(clean) or "No direct web search snippets found."

async def process_job(job: PendingJob):
    global last_api_call_timestamp
    prompt_lower = (job.prompt_text or "").lower()
    search_triggers = ("search", "search for", "google", "look", "find", "locate", "duckduckgo", "bitcoin", "btc", "crypto", "price", "news", "current")
    needs_search = job.priority_tier in (1, 4) and (any(k in prompt_lower for k in search_triggers) or "?" in prompt_lower or len(prompt_lower) > 3)

    if job.priority_tier in (1, 4) and not needs_search:
        zero_token_reply = try_zero_token_timeline_response(job.prompt_text)
        if zero_token_reply:
            try:
                mention_prefix = job.target_member.mention if job.target_member else ""
                content_val, kwargs = await build_message_payload(zero_token_reply, "", None, None, mention_prefix=mention_prefix, priority_tier=job.priority_tier)
                if job.reference_message:
                    await job.reference_message.reply(content=content_val or None, mention_author=(job.priority_tier == 1), **kwargs)
                else:
                    await job.channel.send(content=content_val or None, **kwargs)
                if job.reference_message:
                    replied_message_ids.add(job.reference_message.id)
                    save_state()
                return
            except Exception as e:
                print(f"[ZERO-TOKEN DISPATCH EXCEPTION] {e}")

    max_retries = 20
    base_delay = 1.0

    for attempt in range(max_retries):
        while True:
            m = get_trajectory_metrics()
            if time.monotonic() < GROQ_RATE_LIMITED_UNTIL or m["local_daily"] >= 195000:
                await asyncio.sleep(5.0)
                continue
            break

        token_limit = calculate_dynamic_max_tokens(job.priority_tier)
        engine = get_traffic_engine_metrics(job.priority_tier)
        freq = engine["freq_multiplier"]

        spec = PRIORITY_SPECS.get(job.priority_tier, {})
        base_delay_min = spec.get("delay_min", 1.0)
        min_gap = max(base_delay_min * 0.001, freq * 0.1)

        elapsed = time.monotonic() - last_api_call_timestamp
        if elapsed < min_gap:
            await asyncio.sleep(min_gap - elapsed)

        mood = random.randint(1, 5)
        temperature = 0.88 if job.priority_tier == 1 else 1.05

        live = await fetch_live_context(job.prompt_text) if needs_search else ""
        history = extract_relevant_context(job.prompt_text) if job.priority_tier in (1, 4) else SHORT_IDENTITY

        search_instruction = f"\n[LIVE WEB SEARCH]:\n'{live}'" if needs_search and live else ""
        system = f"{PRONOUN_BRIDGE}\n{FORBIDDEN_PHRASES}\n{VOICE_KERNEL}\nMOOD: {PERSONA_MOODS[mood]}\nOUTLINE:\n{history}\n{search_instruction}\nSLOGAN: {random.choice(SLOGANS)}\n{SOVEREIGN}"

        reply = ""
        actual_tokens = 0
        try:
            messages_payload = [{"role": "system", "content": system}, {"role": "user", "content": job.prompt_text or "Thought?"}]
            async with groq_api_lock:
                reply, actual_tokens, headers = await asyncio.to_thread(call_groq_rest, messages_payload, token_limit or 150, temperature)
                last_api_call_timestamp = time.monotonic()
        except Exception as e:
            print(f"[TOKEN-SPENDING EXCEPTION P{job.priority_tier}] {e}")

        track_token_usage_real(actual_tokens or 30)

        if not reply or reply in ZERO_TOKEN_BANTER_POOL:
            reply = get_zero_token_text()

        spec_option = spec.get("option", "MEDIA/TEXT")
        media_url, file_obj, sticker = "", None, None
        if spec_option == "MEDIA/TEXT" and not needs_search:
            media_url, file_obj, sticker = await get_wild_media_hybrid(guild=getattr(job.channel, "guild", None), priority_tier=job.priority_tier, chat_context=job.prompt_text, mode="CUSTOM", category_override=(attempt % 8) + 1)

        try:
            mention_prefix = job.target_member.mention if job.target_member else ""
            content_val, kwargs = await build_message_payload(reply, media_url, file_obj, sticker, mention_prefix=mention_prefix, priority_tier=job.priority_tier)

            if job.reference_message:
                await job.reference_message.reply(content=content_val or None, mention_author=(job.priority_tier == 1), **kwargs)
            elif job.target_member:
                await job.channel.send(content=content_val or None, **kwargs)
            else:
                await job.channel.send(content=content_val or None, **kwargs)

            if job.reference_message:
                replied_message_ids.add(job.reference_message.id)
                save_state()
            break
        except Exception as e:
            print(f"[DISPATCH EXCEPTION P{job.priority_tier}]: {e}")
            await asyncio.sleep(base_delay * (2.0 ** attempt))

async def execute_priority_2():
    """PRIORITY 2: TOKEN-FREE | MEDIA-ONLY (Heart Beat Schedule: 100 min intervals, Delay: 30m - 30h)"""
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            spec = PRIORITY_SPECS[2]
            delay = random.uniform(spec["schedule_min"], spec["schedule_max"])
            await asyncio.sleep(delay)
            for guild in list(bot.guilds):
                channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages and c.permissions_for(guild.me).read_message_history]
                if not channels: continue
                ch = random.choice(channels)
                cursor = channel_cursors.get(f"p2_fwd_{ch.id}", 0)
                kwargs = {"limit": 50, "oldest_first": True}
                if cursor: kwargs["after"] = discord.Object(id=cursor)
                target_msg = None
                async for msg in ch.history(**kwargs):
                    channel_cursors[f"p2_fwd_{ch.id}"] = msg.id
                    if msg.author.bot or msg.author == bot.user or msg.id in replied_message_ids or msg.author.id in p2_replied_member_ids:
                        continue
                    target_msg = msg
                    break
                if target_msg:
                    url, file_obj, sticker = await get_wild_media_hybrid(guild, priority_tier=2, mode="RANDOM")
                    content_val, kwargs_send = await build_message_payload("", url, file_obj, sticker, priority_tier=2)
                    await target_msg.reply(content=content_val or None, mention_author=False, **kwargs_send)
                    replied_message_ids.add(target_msg.id)
                    p2_replied_member_ids.add(target_msg.author.id)
                    save_state()
                    break
        except Exception as e:
            print(f"[P2 EXCEPTION] {e}")
            await asyncio.sleep(30.0)

async def execute_priority_3():
    """PRIORITY 3: TOKEN-FREE | MEDIA-ONLY (Heart Beat Schedule: 100-120 min intervals, Delay: 50m - 32h)"""
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            spec = PRIORITY_SPECS[3]
            delay = random.uniform(spec["schedule_min"], spec["schedule_max"])
            await asyncio.sleep(delay)
            for guild in list(bot.guilds):
                members = sorted([m for m in guild.members if not m.bot], key=lambda x: x.name.lower())
                if not members: continue
                idx = p3_member_indices.get(guild.id, 0) % len(members)
                target = members[idx]
                p3_member_indices[guild.id] = (idx + 1) % len(members)
                channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages]
                if not channels: continue
                ch = random.choice(channels)
                url, file_obj, sticker = await get_wild_media_hybrid(guild, priority_tier=3, mode="RANDOM")
                content_val, kwargs = await build_message_payload("", url, file_obj, sticker, mention_prefix=f"<@{target.id}>", priority_tier=3)
                await ch.send(content=content_val or None, **kwargs)
                break
        except Exception as e:
            print(f"[P3 EXCEPTION] {e}")
            await asyncio.sleep(30.0)

async def execute_priority_4():
    """PRIORITY 4: TOKEN-SPENDING | MEDIA/TEXT (Heart Beat Schedule: instant up to 25h, Delay: 10s - 25h)"""
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            spec = PRIORITY_SPECS[4]
            delay = random.uniform(10, 300)
            await asyncio.sleep(delay)
            for guild in list(bot.guilds):
                channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages and c.permissions_for(guild.me).read_message_history]
                if not channels: continue
                ch = random.choice(channels)
                async for msg in ch.history(limit=25):
                    if msg.author.bot or msg.author == bot.user or not msg.content or msg.id in replied_message_ids:
                        continue
                    if (datetime.now(timezone.utc) - msg.created_at).total_seconds() > 3600:
                        continue
                    job = PendingJob(priority_tier=4, timestamp=time.monotonic(), channel=ch, prompt_text=msg.content[:120], reference_message=msg, author_id=msg.author.id)
                    await delay_queue.put(job)
                    break
        except Exception as e:
            print(f"[P4 EXCEPTION] {e}")
            await asyncio.sleep(15.0)

async def execute_priority_5_reverse():
    """PRIORITY 5: TOKEN-FREE | MEDIA-ONLY (Heart Beat Schedule: 100-110 min intervals, Delay: 40m - 31h)"""
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            spec = PRIORITY_SPECS[5]
            delay = random.uniform(spec["schedule_min"], spec["schedule_max"])
            await asyncio.sleep(delay)
            for guild in list(bot.guilds):
                channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages and c.permissions_for(guild.me).read_message_history]
                if not channels: continue
                ch = random.choice(channels)
                async for msg in ch.history(limit=25, oldest_first=False):
                    if msg.author.bot or msg.id in replied_message_ids:
                        continue
                    url, file_obj, sticker = await get_wild_media_hybrid(guild, priority_tier=5, mode="RANDOM")
                    content_val, kwargs_send = await build_message_payload("", url, file_obj, sticker, priority_tier=5)
                    await msg.reply(content=content_val or None, mention_author=False, **kwargs_send)
                    replied_message_ids.add(msg.id)
                    save_state()
                    break
                break
        except Exception as e:
            print(f"[P5 EXCEPTION] {e}")
            await asyncio.sleep(30.0)

async def execute_priority_6_drops():
    """PRIORITY 6: TOKEN-FREE | MEDIA-ONLY (Heart Beat Schedule: 10-30 min intervals, Delay: 20s - 26h)"""
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            spec = PRIORITY_SPECS[6]
            delay = random.uniform(spec["schedule_min"], spec["schedule_max"])
            await asyncio.sleep(delay)
            for guild in list(bot.guilds):
                channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages]
                if not channels: continue
                ch = random.choice(channels)
                url, file_obj, sticker = await get_wild_media_hybrid(guild, priority_tier=6, mode="RANDOM")
                content_val, kwargs = await build_message_payload("", url, file_obj, sticker, priority_tier=6)
                if content_val or kwargs:
                    await ch.send(content=content_val or None, **kwargs)
                break
        except Exception as e:
            print(f"[P6 EXCEPTION] {e}")
            await asyncio.sleep(30.0)

async def execute_priority_7():
    """PRIORITY 7: TOKEN-FREE | MEDIA-ONLY (Heart Beat Schedule: 10-30 min intervals, Delay: 2m - 27h)"""
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            spec = PRIORITY_SPECS[7]
            delay = random.uniform(spec["schedule_min"], spec["schedule_max"])
            await asyncio.sleep(delay)
            for guild in list(bot.guilds):
                channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages and c.permissions_for(guild.me).read_message_history]
                if not channels: continue
                ch = random.choice(channels)
                cursor = bot_media_cursors.get(f"p7_bot_media_{ch.id}", 0)
                kwargs = {"limit": 50, "oldest_first": True}
                if cursor: kwargs["after"] = discord.Object(id=cursor)
                target_msg = None
                async for msg in ch.history(**kwargs):
                    bot_media_cursors[f"p7_bot_media_{ch.id}"] = msg.id
                    if msg.author == bot.user and not msg.pinned:
                        target_msg = msg
                        break
                if target_msg:
                    url, file_obj, sticker = await get_wild_media_hybrid(guild, priority_tier=7, mode="RANDOM")
                    content_val, kwargs_send = await build_message_payload("", url, file_obj, sticker, priority_tier=7)
                    await target_msg.reply(content=content_val or None, mention_author=False, **kwargs_send)
                    break
        except Exception as e:
            print(f"[P7 EXCEPTION] {e}")
            await asyncio.sleep(30.0)

async def execute_priority_8():
    """PRIORITY 8: TOKEN-SPENDING | TEXT-ONLY (Heart Beat Schedule: 10-30 min intervals, Delay: 3m - 28h)"""
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            spec = PRIORITY_SPECS[8]
            delay = random.uniform(spec["schedule_min"], spec["schedule_max"])
            await asyncio.sleep(delay)
            for guild in list(bot.guilds):
                channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages and c.permissions_for(guild.me).read_message_history]
                if not channels: continue
                ch = random.choice(channels)
                cursor = bot_text_cursors.get(f"p8_bot_text_{ch.id}", 0)
                kwargs = {"limit": 50, "oldest_first": True}
                if cursor: kwargs["after"] = discord.Object(id=cursor)
                target_msg = None
                async for msg in ch.history(**kwargs):
                    bot_text_cursors[f"p8_bot_text_{ch.id}"] = msg.id
                    if msg.author == bot.user and not msg.pinned and len(msg.content or "") > 2:
                        target_msg = msg
                        break
                if target_msg:
                    job = PendingJob(priority_tier=8, timestamp=time.time(), channel=ch, prompt_text=f"Reflection on past message: '{target_msg.content[:60]}'", reference_message=target_msg)
                    await delay_queue.put(job)
                    break
        except Exception as e:
            print(f"[P8 EXCEPTION] {e}")
            await asyncio.sleep(30.0)

async def execute_priority_9():
    """PRIORITY 9: TOKEN-SPENDING | TEXT-ONLY (Heart Beat Schedule: 5-10 hours intervals, Delay: 10h - 29h)"""
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            spec = PRIORITY_SPECS[9]
            delay = random.uniform(spec["schedule_min"], spec["schedule_max"])
            await asyncio.sleep(delay)
            for guild in list(bot.guilds):
                channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages and c.permissions_for(guild.me).read_message_history]
                if not channels: continue
                ch = random.choice(channels)
                cursor = recap_cursors.get(f"p9_recap_{ch.id}", 0)
                kwargs = {"limit": 40, "oldest_first": True}
                if cursor: kwargs["after"] = discord.Object(id=cursor)
                chat_lines = []
                last_msg_id = cursor
                async for msg in ch.history(**kwargs):
                    last_msg_id = msg.id
                    if not msg.author.bot and msg.content:
                        chat_lines.append(f"{msg.author.display_name}: {msg.content}")
                if last_msg_id != cursor:
                    recap_cursors[f"p9_recap_{ch.id}"] = last_msg_id
                    save_state()
                if chat_lines:
                    job = PendingJob(priority_tier=9, timestamp=time.time(), channel=ch, prompt_text=f"Chat history segment:\n" + "\n".join(chat_lines[-30:]) + "\n\nSummarize this chat.")
                    await delay_queue.put(job)
                    break
        except Exception as e:
            print(f"[P9 EXCEPTION] {e}")
            await asyncio.sleep(60.0)

async def delay_queue_worker():
    while True:
        try:
            job = await delay_queue.get()
            while time.monotonic() < GROQ_RATE_LIMITED_UNTIL or (get_trajectory_metrics()["usage"] > 0.98 and job.priority_tier != 1):
                await asyncio.sleep(3)
            await process_job(job)
            delay_queue.task_done()
        except Exception as e:
            print(f"[WORKER EXCEPTION] {e}")
            await asyncio.sleep(2.0)

@bot.event
async def on_ready():
    print(f"============================================================")
    print(f" Oatmeal AI V56.0 Heart Beat Active logged in as {bot.user}")
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
    mentioned_bot = (bot.user in message.mentions) or any(n in message.content.lower() for n in ("oatmeal ai#4059", str(bot.user.id)))
    replied_to_bot = message.reference and isinstance(message.reference.resolved, discord.Message) and message.reference.resolved.author == bot.user
    named = any(nick in message.content.lower() for nick in ("joey", "arnold", "oatmeal", "joeyarnoldvn", "ojawall"))

    if mentioned_bot or replied_to_bot or named:
        replied_message_ids.add(message.id)
        job = PendingJob(priority_tier=1, timestamp=now, channel=message.channel, prompt_text=message.content, reference_message=message, author_id=message.author.id)
        await delay_queue.put(job)
        return

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
