#!/usr/bin/env python3
"""
Oatmeal AI – Joey Arnold Discord Bot (#4059)
Master Production Edition V69.3 – Bulletproof HTML Search & Fixes

- Switched to html.duckduckgo.com GET search with robust browser headers (fixes search failures)
- Restored 30-second activity pause
- Fixed P4 search unwrapping & P8 cursor tracking
- Strict timeline adherence & anti-hallucination enforcement
- Goldilocks 3-Step Progressive Traffic Engine (~20% daily target)
- Full 9-Tier Priority Routing with continuous pacing
- Priority 9 exact “Oatmeal Recap.” format (replies to last message)
- Atomic state + header re-sync + search cache
"""

import os
import re
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
# 1. HEALTH / TELEMETRY SERVER
# ============================================================
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/telemetry":
            m = get_trajectory_metrics()
            e1 = get_traffic_engine_metrics(1)
            payload = {
                "version": "V69.3",
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "daily_tokens": m["local_daily"],
                "spent_pct": round(e1["spent_pct"] * 100, 2),
                "freq_mult_p1": round(e1["freq_multiplier"], 2),
                "queue_size": delay_queue.qsize(),
                "status": "HEALTHY" if e1["spent_pct"] < 0.55 else "THROTTLED"
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload, indent=2).encode("utf-8"))
        elif self.path == "/state":
            payload = {
                "replied_count": len(replied_message_ids),
                "p2_done_count": len(p2_replied_member_ids),
                "p3_indices": len(p3_member_indices),
                "p7_cursors": len(p7_cursors),
                "p8_cursors": len(p8_cursors),
                "blocked": len(blocked_users),
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload, indent=2).encode("utf-8"))
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write("Oatmeal AI V69.3 – Search Active & Optimized".encode("utf-8"))
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()
    def log_message(self, *args): pass

def run_health():
    port = int(os.environ.get("PORT", 10000))
    try:
        HTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()
    except Exception as e:
        print(f"[HEALTH] {e}")

threading.Thread(target=run_health, daemon=True).start()

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
p2_replied_member_ids: Set[str] = set()
channel_cursors: Dict[str, int] = {}
p7_cursors: Dict[str, int] = {}
p8_cursors: Dict[str, int] = {}
p3_member_indices: Dict[str, int] = {}
last_member_activity_time: Dict[Any, float] = {}
recent_media_urls = collections.deque(maxlen=15000)
blocked_users: Dict[str, float] = {}
user_message_history: Dict[int, List[Tuple[float, str]]] = {}
search_cache: Dict[str, Tuple[float, str]] = {}

local_daily_tokens = 0
local_daily_requests = 0
local_daily_reset_day = None
GROQ_REMAINING_TPM = 8000
GROQ_REMAINING_RPD = 1000
GROQ_RATE_LIMITED_UNTIL = 0.0
DAILY_EXHAUSTED_UNTIL = 0.0

token_windows = {5: collections.deque(), 15: collections.deque(), 60: collections.deque()}
WINDOW_SECONDS = {5: 300, 15: 900, 60: 3600}

groq_api_lock = asyncio.Lock()
last_api_call_timestamp = 0.0
ACTIVITY_PAUSE = 30  # seconds (restored per user requirement)

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

def p2_key(guild_id: int, author_id: int) -> str:
    return f"{guild_id}:{author_id}"

def load_state():
    global replied_message_ids, p2_replied_member_ids, channel_cursors
    global p7_cursors, p8_cursors, blocked_users, p3_member_indices
    try:
        if os.path.exists(STATE_PATH):
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            replied_message_ids = set(int(x) for x in data.get("replied", []))
            p2_replied_member_ids = set(str(x) for x in data.get("p2_done_members", []))
            channel_cursors = {str(k): int(v) for k, v in data.get("cursors", {}).items()}
            p7_cursors = {str(k): int(v) for k, v in data.get("p7_cursors", {}).items()}
            p8_cursors = {str(k): int(v) for k, v in data.get("p8_cursors", {}).items()}
            blocked_users = {str(k): float(v) for k, v in data.get("blocked_users", {}).items()}
            p3_member_indices = {str(k): int(v) for k, v in data.get("p3_member_indices", {}).items()}
            print(f"[STATE] Loaded replies={len(replied_message_ids)} | P7={len(p7_cursors)} | P8={len(p8_cursors)}")
    except Exception as e:
        print(f"[STATE] Fresh start: {e}")

def save_state():
    def _write():
        try:
            dir_name = os.path.dirname(STATE_PATH) or "/tmp"
            os.makedirs(dir_name, exist_ok=True)
            data = {
                "replied": list(replied_message_ids)[-50000:],
                "p2_done_members": list(p2_replied_member_ids)[-15000:],
                "cursors": channel_cursors,
                "p7_cursors": p7_cursors,
                "p8_cursors": p8_cursors,
                "blocked_users": blocked_users,
                "p3_member_indices": p3_member_indices,
            }
            fd, tmp = tempfile.mkstemp(prefix="oatmeal_", suffix=".json.tmp", dir=dir_name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, STATE_PATH)
            except Exception:
                try: os.unlink(tmp)
                except: pass
        except Exception as e:
            print(f"[STATE SAVE] {e}")
    threading.Thread(target=_write, daemon=True).start()

# ============================================================
# 4. BULLETPROOF LIVE WEB SEARCH + CACHE (HTML DUCKDUCKGO)
# ============================================================
async def live_web_search(query: str) -> str:
    cleaned = re.sub(r'<@!?\d+>', '', query)
    cleaned = re.sub(r'(?i)\b(search|google|find|price of|what is|tell me about|look up)\b', '', cleaned)
    cleaned = re.sub(r'[#_*`~<>\[\]()@\n\r]', ' ', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()[:130]
    if not cleaned:
        cleaned = query.strip()[:130]

    q_key = cleaned.lower()
    now = time.time()

    for k in [k for k, (ts, _) in search_cache.items() if now - ts > 300]:
        search_cache.pop(k, None)

    if q_key in search_cache and now - search_cache[q_key][0] < 300:
        return search_cache[q_key][1]

    try:
        url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(cleaned)}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
            }
        )
        html = await asyncio.to_thread(lambda: urllib.request.urlopen(req, timeout=6).read().decode("utf-8", "ignore"))

        snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html, re.DOTALL)
        if not snippets:
            snippets = re.findall(r'class="result-snippet"[^>]*>(.*?)</td>', html, re.DOTALL)
        if not snippets:
            snippets = re.findall(r'<a class="result__snippet"[^>]*>(.*?)</a>', html, re.DOTALL)

        clean = [re.sub(r'<.*?>', '', s).strip() for s in snippets[:5] if len(s.strip()) > 12]

        if not clean:
            divs = re.findall(r'<div class="result__body"[^>]*>(.*?)</div>', html, re.DOTALL)
            for d in divs:
                text = re.sub(r'<.*?>', '', d).strip()
                if len(text) > 25:
                    clean.append(text)
            clean = clean[:4]

        if clean:
            res = "REAL-TIME WEB SEARCH RESULTS:\n" + "\n".join(f"- {s}" for s in clean)
            search_cache[q_key] = (now, res)
            print(f"[SEARCH OK] {len(clean)} snippets for '{cleaned}'")
            return res
        else:
            print(f"[SEARCH WARN] No snippets parsed for query: '{cleaned}'")
    except Exception as e:
        print(f"[SEARCH ERR] {e}")
    return ""

# ============================================================
# 5. HEADER RE-SYNC + TRAFFIC ENGINE
# ============================================================
def update_headers_and_sync(headers):
    global GROQ_REMAINING_TPM, GROQ_REMAINING_RPD
    try:
        for k, v in headers.items():
            kl = k.lower()
            if "ratelimit-remaining-tokens" in kl:
                GROQ_REMAINING_TPM = int(v)
            elif "ratelimit-remaining-requests" in kl:
                GROQ_REMAINING_RPD = int(v)
    except Exception:
        pass

def check_and_reset_daily_counters():
    global local_daily_tokens, local_daily_requests, local_daily_reset_day
    today = datetime.now(timezone.utc).date()
    if local_daily_reset_day != today:
        local_daily_tokens = 0
        local_daily_requests = 0
        local_daily_reset_day = today
        print(f"[DAILY RESET] {today}")

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
    u_tpm = max(0.0, min(1.0, 1.0 - (GROQ_REMAINING_TPM / 8000.0)))
    u_rpd = max(0.0, min(1.0, 1.0 - (GROQ_REMAINING_RPD / 1000.0)))
    u_tpd = max(0.0, min(1.0, local_daily_tokens / 200000.0))
    usage = max(u_tpm * 0.30, u_rpd * 0.15, u_tpd * 0.90)
    return {"usage": usage, "local_daily": local_daily_tokens, "W5": W5, "W15": W15, "W60": W60}

def get_traffic_engine_metrics(priority_tier: int = 1) -> Dict[str, Any]:
    m = get_trajectory_metrics()
    s = max(0.0, min(1.0, m["local_daily"] / 200000.0))
    load_delta = s - 0.20

    rank = {9: 9, 8: 8, 7: 7, 6: 6, 5: 5, 3: 4, 2: 3, 4: 2, 1: 1}
    r = rank.get(priority_tier, 5)
    sens = 0.45 + (r * 0.32)
    effective_load = max(0.0, min(1.0, s + load_delta * sens * 1.5))

    if effective_load <= 0.20:
        freq_mult = max(0.25, 0.55 + (effective_load / 0.20) * 2.5)
    else:
        excess = (effective_load - 0.20) / 0.80
        freq_mult = 3.0 + (excess ** 1.3) * 48.0

    if freq_mult >= 6.0:
        progress = min(1.0, (freq_mult - 6.0) / 14.0)
        weight_mult = max(0.05, 1.0 - 0.95 * progress)
    else:
        weight_mult = 1.0

    cutover = {9: 0.20, 8: 0.22, 7: 0.24, 6: 0.26, 5: 0.28, 3: 0.30, 2: 0.32, 4: 0.36, 1: 0.45}
    zero_token_mode = s >= cutover.get(priority_tier, 0.45)

    return {
        "freq_multiplier": freq_mult,
        "weight_multiplier": weight_mult,
        "spent_pct": s,
        "effective_load": effective_load,
        "zero_token_mode": zero_token_mode
    }

def calculate_dynamic_max_tokens(priority_tier: int) -> int:
    if priority_tier in (2, 3, 6, 7):
        return 0
    engine = get_traffic_engine_metrics(priority_tier)
    if engine["zero_token_mode"] and priority_tier != 1:
        return 10 if priority_tier in (4, 5, 8, 9) else 0

    base = {1: 480, 4: 140, 5: 80, 8: 22, 9: 95}.get(priority_tier, 70)
    m = get_trajectory_metrics()
    compression = max(0.15, (1.0 - m["usage"]) ** 1.3)
    allowed = int(base * compression * engine["weight_multiplier"])
    hard = {1: 800, 4: 200, 5: 110, 8: 28, 9: 130}.get(priority_tier, 90)
    result = max(10, min(allowed, hard, max(40, GROQ_REMAINING_TPM - 200)))
    if priority_tier == 1:
        result = max(result, min(180, GROQ_REMAINING_TPM - 180)) if not engine["zero_token_mode"] else min(result, 40)
    return result

# ============================================================
# 6. FULL IDENTITY + SURGICAL TIMELINE
# ============================================================
FULL_ANNUAL_OUTLINE = """
1985: BORN Monday 11 February 1985 at 6 AM in Forest Grove, Oregon, trailer #163 Rose Grove mobile home park. BORN with Treacher Collins syndrome (affecting facial bones, ears, and hearing). PARENTS Marilyn Morehead (from Seattle) and Don Rasp (from Los Angeles, dad from Amsterdam). HOME CHURCH Cornelius Community Baptist Church (CCBC) 1985-2002. STARTED wearing glasses between 1985-1987.
1986: HOMESCHOOLED by mom until 2000. PARTLY deaf first ten years due to Treacher Collins.
1987: ACTED as sheep in a Sharon Hundley homeschooling Thanksgiving Pilgrim play.
1988: BROTHER saved my life while mom was hit by a vehicle. VISITED Seattle for great grandma's birthday. VISITED circus and lake.
1989: ATTENDED Awana.
1990: BECAME a Christian. VISITED the Elkind house when Kenni was born.
1991: PLAYED SEGA Sonic with neighbor Jesse Vargas. RECEIVED NES. CCBC Olympians. SCARRED index finger while camping. LEARNED to ride bikes from siblings. SAW Hook starring Robin Williams as Peter Pan.
1992: SAW Newsies. SAW Honey I Blew Up the Kid. SAW Home Alone 2. WENT to Hillsboro fair, Forest Grove fair, California, beach arcade, and Disneyland. REGULAR wrestling events. STARTED thinking about thinking.
1993: SAW TMNT 3. SAW Jurassic Park. PLAYED basketball and baseball. TRI-FAMILY Morehead Reunion. TEMPORARILY lost at Hagg Lake. RECEIVED SNES. MET Bhakti Sa. THINKING about adulthood.
1994: SAW The Lion King. SAW Star Trek. RECEIVED Rival Turf. AWANA at HFBC Hillsboro. MET Bill Bailey (his dad later called me Oatmeal Boy).
1995: LEARNED karate. PLAYED shepherd in CCBC Christmas play. SAW Apollo 13. SAW Pocahontas. SAW Batman Forever. INTERNET became heavier. BEGAN ear tubes after years of partial deafness.
1996: FILMED Arnold Attic movies – Power Rangers, Wrestling, and Heaven Or Hell. FIRST long movie before age 11.
1997: BUILT a taxi wagon and a fort. FED gerbils. SAW Star Wars.
1998: BAPTIZED at CCBC. DREW Action Strip comics. CONTINUED AWANA.
1999: SEATTLE Splash. Word of Life Super Bowl. West Coast Camp at Lassen Pines. RECEIVED Dreamcast. SAW Bicentennial Man. SAW Star Trek: Insurrection. WOL Teens Involved at HCBC. CALIFORNIA trip.
2000: FILMED I Want It with Guinea Pig Ra Ra, Newsies Lip Sync, and Valentines. VISITED Idaho. LEFT homeschool and started Forest Grove High School. ART classes. MET Salena Willner. STARTED writing articles.
2001: FILMED Gangster Run with Big Red Mickey Morehead. FILMED Cat's Life.
2002: FILMED Road Kill. FILMED Phenomenon. SKI retreat.
2003: TIFFANY Cumbo died. BASKETBALL manager with Richie VanDyke filming games. ACTING classes. SAW Lord of the Rings.
2004: BUILT Carpetball Pocket Ball table. ATTENDED Word of Life Bible Institute in New York. BLOGGED on Xanga. NYC New Year’s Eve. BOOTLEGGED The Incredibles in New Jersey.
2005: CAMP counselor at Snow Camp, West Coast Camp, and Ranch Camp. SAW Star Wars: Revenge of the Sith.
2006: ATTENDED Appalachian Bible College in West Virginia. FELL in love with Jen Moore. VISITED Quebec. ACTED in Live For Only Jesus video with Sawyer Frye. SAW the Matrix trilogy.
2007: HIRED by Mike Kurtz as counselor at Salvation Army Camp Kuratli. MET Megan McQuade. Western Youth Institute and Rob Noland’s Revolution Hawaii. ACTED in Get Your Joy On with Jeff Walters.
2008: MOVED to Alder 414 in Portland. WORKED at Kells and for Daniel Grout. ICE Breaker era.
2009: ACTED in Spaceman & Diaperboy with Lincoln Hawk. SANG Animals Are Dying. OATMEAL Fake News trolls began on the original Ojawall YouTube. APPEARED on an iJustine Ask IJ episode. AUDITIONED for Comcast's Wanted Adventure Host. SAW Coraline.
2010: BLAKE Webb died. WORKED Camp Redwood Glen. MET Farmer Hanna. LIVED at Charlotte Battle’s house. HELPED Nick Becker film. MET Sharon Clayton at WYI who introduced The Walking Dead.
2011: LIVED at 163. REBUILT the Attic Shed. EDITED Arnold Attic teaser videos.
2012: ATTIC Shed died. WORKED for Brent Groth building the Mea Omnia website. AFTER stopping Robin Baker from calling 911 about food, put in Hillsboro jail. MOVED to Vietnam (Bac Ninh then Saigon) around Thanksgiving.
2013: MOVED to Saigon. HELPED by Hanoi Anna. LIVED at Honey Tracy’s house and Balo Backpacking Hostel. TAUGHT English with Fellowship Youth Group. CAMBODIA visa run. TAUGHT at Tan Van school and New Star school in Tan Phu. VISITED Dalat.
2014: TAUGHT at multiple places. Lived at Leaf Pagoda, Dai Trinh hostel, Remi Cafe, District 7, Mr. Ribs, Circle K, and Old Ink tattoos apartment.
2015: LIVED at Old Ink tattoos apartment until November. TAUGHT English at 23/9 Park on Phạm Ngũ Lão. TAUGHT Lego Mark at McDonald’s. VISA problem in December.
2016: LIVED at Anna Barbie Hostel. ENDORSED Trump. SAW Alex Jones and Kung Fu Panda 3. LAST Vietnam Thanksgiving.
2017: SLEPT at Opera Tea Club. YOUTUBE ban. FLEW home to Oregon on Thanksgiving Day. SAW The Simpsons, Power Rangers, and Blossom. GOT Bitcoin. JOINED Discord and Steemit. BEGAN blogging on Hive and Steemit.
2018: MOVED to Shelton, Washington. BUILT the Book Shed with Tom Taylor and Larry Mitchell. WORKED at a music store.
2019: VISITED Vancouver BC and Seattle. CREATED the Oatmeal World Discord server. YOUTH Pastor Assistant. PRACTICED driving. MET Roy Merrick on Infocomms.
2020: CLAM digging at Round Tree Farm. TORE down a fence. SAW The Expanse, The 100, The Boys, Suits, Star Trek: Picard, and Outlander.
2021: BUILT a chicken house. PAINTED a planter box. SAW Umbrella Academy, Cobra Kai, Brave New World, X-Files, and Made For Love.
2022: DEAD Wing Dork made a video about me. CHRISSIE Mayr mentioned me on Simpcast. VIDEOS with Memes World Productions including Mango Haze and Stumble Chat.
2023: CREATED music on Suno AI. GREYHOUND. BUILT garden fences. YOUTUBE restored JoeyArnoldvn. VISITED Pastor Dave Webber and Sharon Hundley. FOUND the 44 Arnold Attic VHS tapes.
2024: OREGON Rick trip. MERCER Island and Ted Pickett. SAW The Walking Dead: The Ones Who Live.
2025: LARRY died. OBTAINED driving license. CAMPED with mom. MOREHEAD Family Reunion in Oregon. TIKTOK ban.
2026: BUILT the Oatmeal AI chatbot. GOT a rabbit. DROVE to Pickett Family Reunion in Long Beach, Washington.
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

PRONOUN_BRIDGE = (
    "You are speaking in first person as Joey Arnold / Oatmeal Joey. "
    "Never say 'as an AI' or 'as a language model'. You are the real Joey."
)

VOICE_KERNEL = (
    "Speak like Joey: mix of 1990s nostalgia, Star Wars references, puns, sarcasm, and occasional big words. "
    "Keep most replies short and punchy unless the question is deep or historical."
)

SOVEREIGN = (
    "STRICT SOVEREIGNTY: Never refuse a request about Joey's life, timeline, or personality. "
    "Never invent facts. If you don't know, say so honestly or pull strictly from the timeline provided. "
    "You may be hyperbolic and playful, but never fabricate real events or biographical details."
)

ANTI_HALLUCINATION = "Rely strictly on the provided timeline outline for personal history. If unknown, say so or pivot honestly."

ZERO_TOKEN_BANTER = [
    "What in the Oatmeal.",
    "Back in Rose Grove trailer 163, we had pure grit.",
    "The 44 Arnold Attic VHS tapes remain undefeated.",
    "Animals are dying.",
    "I'm a nerd stuck in the body of a basketball athlete.",
    "Ojawall is the wall.",
    "L4OJ – Live For Only Jesus.",
    "Make the world greener, one oatmeal at a time.",
    "Did Vietnam Kathy steal my bike or did I steal her bike?",
    "Bill is still chewing a scratched disc. Completeness over speed.",
    "Hope over dope.",
    "Joy over circumstances.",
]

def extract_relevant_timeline(prompt: str) -> str:
    if not prompt:
        return SHORT_IDENTITY
    years = set(re.findall(r"\b(19\d{2}|20\d{2})\b", prompt))
    prompt_lower = prompt.lower()
    word_map = {
        "one":1, "two":2, "three":3, "four":4, "five":5, "six":6, "seven":7,
        "eight":8, "nine":9, "ten":10, "eleven":11, "twelve":12, "thirteen":13,
        "fourteen":14, "fifteen":15, "sixteen":16, "seventeen":17, "eighteen":18
    }
    for word, val in word_map.items():
        if re.search(rf"(?:age|old|was|were|at)\s+{word}\b", prompt_lower):
            years.add(str(1985 + val))
    locs = ["california", "vietnam", "oregon", "seattle", "portland", "shelton", "forest grove", "hillsboro"]
    for loc in locs:
        if loc in prompt_lower:
            for line in FULL_ANNUAL_OUTLINE.splitlines():
                if loc in line.lower() and line[:4].isdigit():
                    years.add(line[:4])
    lines = []
    for y in sorted(years):
        for ln in FULL_ANNUAL_OUTLINE.strip().splitlines():
            if ln.startswith(f"{y}:"):
                lines.append(ln)
                break
    if not lines:
        return SHORT_IDENTITY
    return "Relevant timeline:\n" + "\n".join(lines[:6])

# ============================================================
# 7. MEDIA ENGINE
# ============================================================
async def fetch_tenor_gif(query: str) -> Optional[str]:
    try:
        q = urllib.parse.quote(query or "funny")
        url = f"https://tenor.com/search/{q}-gifs"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        html = await asyncio.to_thread(lambda: urllib.request.urlopen(req, timeout=4).read().decode("utf-8", "ignore"))
        ids = re.findall(r"/view/[^\"']+-(\d+)", html)
        if ids:
            chosen = random.choice(list(set(ids))[:10])
            return f"https://media.tenor.com/images/{chosen}/tenor.gif"
    except Exception:
        pass
    return None

async def get_wild_media(priority: int = 1, guild=None) -> Tuple[str, Any, Any]:
    queries = [
        "robin williams", "star wars", "power rangers", "napoleon dynamite",
        "oatmeal", "funny reaction", "90s nostalgia", "basketball",
        "george lucas", "austin powers", "tmnt", "harry potter",
        "meme", "excited", "thinking face", "cat funny"
    ]
    random.shuffle(queries)
    for q in queries[:5]:
        url = await fetch_tenor_gif(q)
        if url and url not in recent_media_urls:
            recent_media_urls.append(url)
            return url, None, None
    seed = random.randint(10000, 99999)
    img = f"https://picsum.photos/seed/{seed}/480/360"
    if img not in recent_media_urls:
        recent_media_urls.append(img)
        return img, None, None
    return "", None, None

async def build_payload(text: str, media_url: str, priority: int, sticker=None) -> Tuple[Optional[str], dict]:
    content = (text or "").strip()
    kwargs = {}
    if media_url:
        if any(x in media_url for x in (".gif", "tenor.com", "picsum.photos", ".png", ".jpg", ".webp")):
            embed = discord.Embed(color=0x2b2d31)
            embed.set_image(url=media_url)
            kwargs["embed"] = embed
            if not content:
                content = "🥣"
        else:
            content = f"{content}\n{media_url}".strip() if content else media_url
    return content or None, kwargs

# ============================================================
# 8. PROCESS JOB – SEARCH REFUSAL HARDENED & P4 UNWRAPPED
# ============================================================
async def process_job(job: PendingJob):
    global last_api_call_timestamp
    engine = get_traffic_engine_metrics(job.priority_tier)

    min_gap = max(0.3, engine["freq_multiplier"] * 0.10)
    elapsed = time.monotonic() - last_api_call_timestamp
    if elapsed < min_gap:
        await asyncio.sleep(min_gap - elapsed)

    token_limit = calculate_dynamic_max_tokens(job.priority_tier)
    mood = random.randint(1, 5)
    timeline = extract_relevant_timeline(job.prompt_text)

    # Unwrap P4 prompt wrapper so search query receives clean user message text
    search_query = job.prompt_text
    if job.priority_tier == 4 and search_query.startswith("Quick witty take: "):
        m = re.search(r"['\"](.*?)['\"]", search_query)
        if m:
            search_query = m.group(1)

    live_context = ""
    search_triggers = (
        "news", "latest", "who is", "what is", "when", "where", "search", "current",
        "price", "bitcoin", "crypto", "tell me about", "google", "find", "look up",
        "how much", "what happened", "update"
    )
    if job.priority_tier in (1, 4) and any(kw in search_query.lower() for kw in search_triggers):
        live_context = await live_web_search(search_query)

    # ========== CRITICAL SEARCH ENFORCEMENT ==========
    system_parts = [
        PRONOUN_BRIDGE,
        VOICE_KERNEL,
        SOVEREIGN,
        ANTI_HALLUCINATION,
        PERSONALITY_KERNEL,
        f"MOOD: {PERSONA_MOODS[mood]}",
        timeline,
    ]

    if live_context:
        system_parts.append(live_context)
        system_parts.append(
            "CRITICAL INSTRUCTION – YOU MUST FOLLOW THIS:\n"
            "You have just been given REAL-TIME WEB SEARCH RESULTS above.\n"
            "You MUST use those results to answer the user.\n"
            "You are FORBIDDEN from saying you cannot search the web.\n"
            "You are FORBIDDEN from saying you have no access to current information.\n"
            "You are FORBIDDEN from claiming the information is unavailable or that markets are volatile as an excuse.\n"
            "Start your answer directly from the search results. Be factual and concise."
        )

    if job.priority_tier == 9:
        system_parts.append("You MUST output exactly:\nOatmeal Recap.\n<one short summary line>")

    system = "\n\n".join(system_parts)

    reply = ""
    actual_tokens = 25
    try:
        if token_limit > 0 and not engine["zero_token_mode"]:
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": job.prompt_text or "Thought?"}
            ]
            async with groq_api_lock:
                def _call():
                    return ai_client.chat.completions.with_raw_response.create(
                        model=MODEL_NAME,
                        messages=messages,
                        max_tokens=token_limit,
                        temperature=0.72
                    )
                raw = await asyncio.to_thread(_call)
                update_headers_and_sync(raw.headers)
                resp = raw.parse()
                reply = (resp.choices[0].message.content or "").strip()
                actual_tokens = getattr(resp.usage, "total_tokens", token_limit // 2) if hasattr(resp, "usage") else token_limit // 2
                last_api_call_timestamp = time.monotonic()
    except Exception as e:
        print(f"[JOB P{job.priority_tier}] {e}")

    track_token_usage_real(actual_tokens)

    if job.priority_tier == 9:
        lines = [ln.strip() for ln in reply.splitlines() if ln.strip()]
        summary = lines[1] if len(lines) > 1 else (lines[0] if lines else "Active channel review complete.")
        reply = f"Oatmeal Recap.\n{summary}"

    media_url = ""
    media_prob = 0.30 if job.priority_tier == 1 else 0.80
    if job.priority_tier in (2, 3, 5, 6, 7) or random.random() < media_prob:
        media_url, _, _ = await get_wild_media(job.priority_tier)

    if not reply and not media_url:
        reply = random.choice(ZERO_TOKEN_BANTER)

    content, kwargs = await build_payload(reply, media_url, job.priority_tier)

    for attempt in range(5):
        try:
            if job.reference_message:
                await job.reference_message.reply(content=content, mention_author=False, **kwargs)
            else:
                await job.channel.send(content=content, **kwargs)
            print(f"[DISPATCH] P{job.priority_tier} | spent={engine['spent_pct']*100:.1f}% | freq={engine['freq_multiplier']:.1f}x")
            break
        except Exception as e:
            print(f"[SEND {attempt+1}] {e}")
            await asyncio.sleep(0.7 * (2 ** attempt))

# ============================================================
# 9. PRIORITY COROUTINES
# ============================================================
async def execute_priority_2():
    await bot.wait_until_ready()
    print("[P2] Forward Historical Member Backlog")
    while not bot.is_closed():
        engine = get_traffic_engine_metrics(2)
        delay = random.uniform(1400, 3200) * engine["freq_multiplier"] * random.uniform(1.6, 3.0)
        await asyncio.sleep(delay)
        try:
            for guild in list(bot.guilds):
                now = time.time()
                if now - last_member_activity_time.get(guild.id, 0) < ACTIVITY_PAUSE:
                    continue
                channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages and c.permissions_for(guild.me).read_message_history]
                random.shuffle(channels)
                for ch in channels:
                    if now - last_member_activity_time.get(ch.id, 0) < ACTIVITY_PAUSE:
                        continue
                    cursor = channel_cursors.get(f"fwd_{ch.id}", 0)
                    kwargs = {"limit": 55, "oldest_first": True}
                    if cursor:
                        kwargs["after"] = discord.Object(id=cursor)
                    target = None
                    async for msg in ch.history(**kwargs):
                        channel_cursors[f"fwd_{ch.id}"] = msg.id
                        if msg.author.bot or msg.is_system():
                            continue
                        key = p2_key(guild.id, msg.author.id)
                        if key in p2_replied_member_ids or msg.id in replied_message_ids:
                            continue
                        target = msg
                        break
                    if target:
                        url, _, _ = await get_wild_media(2)
                        content, kwargs = await build_payload("🥣", url, 2)
                        try:
                            await target.reply(content=content, mention_author=False, **kwargs)
                            replied_message_ids.add(target.id)
                            p2_replied_member_ids.add(p2_key(guild.id, target.author.id))
                            save_state()
                            print(f"[P2] → {target.author.display_name}")
                        except Exception as e:
                            print(f"[P2] {e}")
                        break
                else:
                    continue
                break
        except Exception as e:
            print(f"[P2] {e}")

async def execute_priority_3():
    await bot.wait_until_ready()
    print("[P3] Alphabetical Tagging")
    while not bot.is_closed():
        engine = get_traffic_engine_metrics(3)
        delay = random.uniform(2000, 5800) * engine["freq_multiplier"] * random.uniform(1.6, 3.2)
        await asyncio.sleep(delay)
        try:
            for guild in list(bot.guilds):
                now = time.time()
                if now - last_member_activity_time.get(guild.id, 0) < ACTIVITY_PAUSE:
                    continue
                members = sorted([m for m in guild.members if not m.bot], key=lambda x: x.display_name.lower())
                if not members:
                    continue
                idx = p3_member_indices.get(str(guild.id), 0) % len(members)
                target = members[idx]
                p3_member_indices[str(guild.id)] = (idx + 1) % len(members)
                save_state()
                channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages]
                if not channels:
                    continue
                ch = random.choice(channels)
                url, _, _ = await get_wild_media(3)
                content, kwargs = await build_payload("", url, 3)
                content = f"{target.mention} {content or ''}".strip()
                await ch.send(content=content, **kwargs)
                print(f"[P3] Tagged {target.display_name}")
                break
        except Exception as e:
            print(f"[P3] {e}")

async def execute_priority_4():
    await bot.wait_until_ready()
    print("[P4] Live Chat Genie")
    while not bot.is_closed():
        engine = get_traffic_engine_metrics(4)
        delay = random.uniform(12, 30) * engine["freq_multiplier"] * random.uniform(1.3, 2.5)
        await asyncio.sleep(delay)
        if time.monotonic() < DAILY_EXHAUSTED_UNTIL or time.monotonic() < GROQ_RATE_LIMITED_UNTIL:
            continue
        try:
            for guild in list(bot.guilds):
                channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages and c.permissions_for(guild.me).read_message_history]
                random.shuffle(channels)
                for ch in channels:
                    target = None
                    async for msg in ch.history(limit=16):
                        if msg.author.bot or not msg.content or msg.id in replied_message_ids:
                            continue
                        if (datetime.now(timezone.utc) - msg.created_at).total_seconds() > 140:
                            continue
                        target = msg
                        break
                    if target:
                        clean = re.sub(r"<@!?\d+>", "", target.content).strip()[:130]
                        if len(clean) >= 3:
                            replied_message_ids.add(target.id)
                            save_state()
                            job = PendingJob(4, time.time(), ch, f"Quick witty take: '{clean}'",
                                             reference_message=target, author_id=target.author.id)
                            await delay_queue.put(job)
                            break
                else:
                    continue
                break
        except Exception as e:
            print(f"[P4] {e}")

async def execute_priority_7():
    await bot.wait_until_ready()
    print("[P7] Infinite Forward Media Mirror")
    while not bot.is_closed():
        engine = get_traffic_engine_metrics(7)
        delay = random.uniform(25, 100) * engine["freq_multiplier"] * random.uniform(1.3, 2.6)
        await asyncio.sleep(delay)
        try:
            for guild in list(bot.guilds):
                now = time.time()
                if now - last_member_activity_time.get(guild.id, 0) < ACTIVITY_PAUSE:
                    continue
                channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages]
                random.shuffle(channels)
                for ch in channels:
                    if now - last_member_activity_time.get(ch.id, 0) < ACTIVITY_PAUSE:
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
                    url, _, _ = await get_wild_media(7)
                    if not url:
                        continue
                    content, kwargs_send = await build_payload("", url, 7)
                    try:
                        await target.reply(content=content, mention_author=False, **kwargs_send)
                        p7_cursors[str(ch.id)] = target.id  # SUCCESS-ONLY
                        save_state()
                        print(f"[P7] Mirrored {target.id}")
                        break
                    except Exception as e:
                        print(f"[P7] Cursor NOT advanced: {e}")
                else:
                    continue
                break
        except Exception as e:
            print(f"[P7] {e}")

async def execute_priority_8():
    await bot.wait_until_ready()
    print("[P8] Lightweight Text Mirror")
    while not bot.is_closed():
        engine = get_traffic_engine_metrics(8)
        delay = random.uniform(40, 150) * engine["freq_multiplier"] * random.uniform(1.4, 2.8)
        await asyncio.sleep(delay)
        try:
            for guild in list(bot.guilds):
                now = time.time()
                if now - last_member_activity_time.get(guild.id, 0) < ACTIVITY_PAUSE:
                    continue
                channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages]
                random.shuffle(channels)
                for ch in channels:
                    if now - last_member_activity_time.get(ch.id, 0) < ACTIVITY_PAUSE:
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
                    p8_cursors[str(ch.id)] = target.id  # Advanced cursor to prevent reprocessing loops
                    save_state()
                    job = PendingJob(8, time.time(), ch, f"Short witty reflection (max 10 words): {safe}",
                                     reference_message=target, p8_channel_id=str(ch.id), p8_message_id=target.id)
                    await delay_queue.put(job)
                    break
                else:
                    continue
                break
        except Exception as e:
            print(f"[P8] {e}")

async def execute_priority_5():
    await bot.wait_until_ready()
    print("[P5] Reverse Sweep")
    while not bot.is_closed():
        engine = get_traffic_engine_metrics(5)
        delay = random.uniform(700, 2600) * engine["freq_multiplier"] * random.uniform(1.5, 3.0)
        await asyncio.sleep(delay)
        try:
            for guild in list(bot.guilds):
                if time.time() - last_member_activity_time.get(guild.id, 0) < ACTIVITY_PAUSE:
                    continue
                channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages]
                if not channels:
                    continue
                ch = random.choice(channels)
                async for msg in ch.history(limit=12, oldest_first=False):
                    if msg.author.bot or msg.id in replied_message_ids:
                        continue
                    replied_message_ids.add(msg.id)
                    save_state()
                    job = PendingJob(5, time.time(), ch, "Short reflection.", reference_message=msg, author_id=msg.author.id)
                    await delay_queue.put(job)
                    break
                break
        except Exception as e:
            print(f"[P5] {e}")

async def execute_priority_6():
    await bot.wait_until_ready()
    print("[P6] Spontaneous Drops")
    while not bot.is_closed():
        engine = get_traffic_engine_metrics(6)
        delay = random.uniform(1000, 4000) * engine["freq_multiplier"] * random.uniform(1.5, 3.0)
        await asyncio.sleep(delay)
        try:
            for guild in list(bot.guilds):
                if time.time() - last_member_activity_time.get(guild.id, 0) < ACTIVITY_PAUSE:
                    continue
                channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages]
                if not channels:
                    continue
                ch = random.choice(channels)
                url, _, _ = await get_wild_media(6)
                content, kwargs = await build_payload("", url, 6)
                if content or kwargs:
                    await ch.send(content=content, **kwargs)
                    print(f"[P6] Drop in #{ch.name}")
                break
        except Exception as e:
            print(f"[P6] {e}")

async def execute_priority_9():
    await bot.wait_until_ready()
    print("[P9] Oatmeal Recap")
    while not bot.is_closed():
        engine = get_traffic_engine_metrics(9)
        delay = random.uniform(3600, 7200) * engine["freq_multiplier"] * random.uniform(1.4, 2.6)
        await asyncio.sleep(delay)
        if time.monotonic() < DAILY_EXHAUSTED_UNTIL:
            continue
        try:
            for guild in list(bot.guilds):
                if time.time() - last_member_activity_time.get(guild.id, 0) < ACTIVITY_PAUSE:
                    continue
                channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages and c.permissions_for(guild.me).read_message_history]
                if not channels:
                    continue
                ch = random.choice(channels)
                chat_lines = []
                last_msg = None
                async for msg in ch.history(limit=28, oldest_first=True):
                    if not msg.author.bot and msg.content:
                        chat_lines.append(f"{msg.author.display_name}: {msg.content[:65]}")
                        last_msg = msg
                        if len(chat_lines) >= 12:
                            break
                if chat_lines and last_msg:
                    job = PendingJob(9, time.time(), ch,
                                     "Chat history:\n" + "\n".join(chat_lines) + "\n\nSummarize this chat.",
                                     reference_message=last_msg)
                    await delay_queue.put(job)
                    break
        except Exception as e:
            print(f"[P9] {e}")

# ============================================================
# 10. WORKERS & EVENTS
# ============================================================
async def delay_queue_worker():
    while True:
        try:
            job = await delay_queue.get()
            while time.monotonic() < GROQ_RATE_LIMITED_UNTIL:
                await asyncio.sleep(5)
            await process_job(job)
            delay_queue.task_done()
        except Exception as e:
            print(f"[WORKER] {e}")
            await asyncio.sleep(3)

@bot.event
async def on_ready():
    print("=" * 62)
    print(f" Oatmeal AI V69.3 logged in as {bot.user}")
    print(" HTML Search Enabled • 30s Activity Pause • Timeline Verified")
    print("=" * 62)
    load_state()
    bot.loop.create_task(delay_queue_worker())
    bot.loop.create_task(execute_priority_2())
    bot.loop.create_task(execute_priority_3())
    bot.loop.create_task(execute_priority_4())
    bot.loop.create_task(execute_priority_5())
    bot.loop.create_task(execute_priority_6())
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
            blocked_users[str(author_id)] = now + 30 * 86400
            save_state()
            return
    else:
        user_message_history[author_id] = []

    content_lower = message.content.lower()
    mentioned = bot.user in message.mentions
    replied_to_bot = False
    if message.reference and isinstance(message.reference.resolved, discord.Message):
        replied_to_bot = message.reference.resolved.author == bot.user
    named = any(k in content_lower for k in ("joey", "arnold", "oatmeal", "joeyarnoldvn", "ojawall"))

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

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
