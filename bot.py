#!/usr/bin/env python3
"""
Oatmeal AI – Joey Arnold Discord Bot (#4059)
Master Production Edition V101.0 – Full Enterprise Monolith
Restores ALL Priority Loops (P1-P9), Full BiMedia Generator (9 Categories),
Untruncated Timeline, Bounded Memory, and Google Maps Traffic Engine.
"""

import os
import re
import json
import time
import asyncio
import threading
import random
import urllib.parse
import collections
import tempfile
import itertools
from dataclasses import dataclass, field
from typing import Any, Dict, List, Set, Tuple, Optional
from datetime import datetime, timezone

import discord
import aiohttp
from aiohttp import web

# ============================================================
# 0. PRE-COMPILED REGEX CONSTANTS
# ============================================================
RE_TENOR_ID = re.compile(r'/view/[^"]+-(\d+)')
RE_WORD_TOKENS = re.compile(r'\b\w+\b')
RE_CLEAN_MENTIONS = re.compile(r'<@!?\d+>')
RE_CLEAN_SEARCH = re.compile(r'(?i)\b(search|google|find|price of|what is|tell me about|look up|duckduckgo|bing|you.com)\b')
RE_CLEAN_SYMBOLS = re.compile(r'[#_*`~<>\[\]()@\n\r]')
RE_YEARS = re.compile(r'\b(19\d{2}|20\d{2})\b')
RE_SNIPPETS = re.compile(r'<td class=["\']result-snippet["\'][^>]*>(.*?)</td>', re.DOTALL)
RE_HTML_STRIP = re.compile(r'<.*?>')

# ============================================================
# 1. CONFIG & CREDENTIALS
# ============================================================
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
if not DISCORD_TOKEN or not GROQ_API_KEY:
    raise ValueError("Missing DISCORD_TOKEN or GROQ_API_KEY environment variables.")

MODEL_NAME = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
STATE_PATH = os.environ.get("STATE_PATH", "/tmp/oatmeal_state.json")

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.messages = True
bot = discord.Client(intents=intents)

TASKS_INITIALIZED = False
HTTP_SESSION: Optional[aiohttp.ClientSession] = None

# ============================================================
# 2. THREAD-SAFE BOUNDED MEMORY STRUCTURES
# ============================================================
from collections import deque
import threading
from typing import Set, Any, List

class BoundedSet:
    def __init__(self, maxsize: int = 30000):
        self.maxsize = maxsize
        self._set: Set[Any] = set()
        self._order: deque = deque()  # O(1) pops from both ends
        self._lock = threading.Lock()

    def add(self, item: Any):
        with self._lock:
            if item in self._set:
                return
            if len(self._set) >= self.maxsize:
                oldest = self._order.popleft()  # O(1) instead of O(N)
                self._set.remove(oldest)
            self._set.add(item)
            self._order.append(item)

    def __contains__(self, item: Any) -> bool:
        with self._lock:
            return item in self._set

    def __len__(self) -> int:
        with self._lock:
            return len(self._set)

    def snapshot(self) -> list:
        with self._lock:
            return list(self._order)

replied_message_ids = BoundedSet(maxsize=30000)
p2_replied_member_ids = BoundedSet(maxsize=10000)
channel_cursors: Dict[str, int] = {}
p7_cursors: Dict[str, int] = {}
p8_cursors: Dict[str, int] = {}
p3_member_indices: Dict[str, int] = {}
last_member_activity_time: Dict[Any, float] = {}
recent_media_urls = collections.deque(maxlen=500)
blocked_users: Dict[str, float] = {}
user_message_history: Dict[int, List[Tuple[float, str]]] = {}

local_daily_tokens = 0
local_daily_reset_day = None
GROQ_REMAINING_TPM = 8000
GROQ_REMAINING_RPD = 1000
GROQ_RATE_LIMITED_UNTIL = 0.0

groq_api_lock = asyncio.Lock()
state_io_lock = asyncio.Lock()
ACTIVITY_PAUSE = 45

# ============================================================
# 3. DETERMINISTIC PRIORITY QUEUE (Sequence Counter Injection)
# ============================================================
_job_sequence = itertools.count()

@dataclass(order=True)
class PendingJob:
    priority_tier: int
    timestamp: float
    sequence: int = field(default_factory=lambda: next(_job_sequence))
    channel: Any = field(compare=False, default=None)
    prompt_text: str = field(compare=False, default="")
    reference_message: Any = field(compare=False, default=None)  # <--- Added field
    author_id: Optional[int] = field(compare=False, default=None) # <--- Added field

delay_queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
state_save_queue: asyncio.Queue = asyncio.Queue()  # <--- Add this line here


def p2_key(guild_id: int, author_id: int) -> str:
    return f"{guild_id}:{author_id}"

# ============================================================
# 4. GOOGLE MAPS TRAFFIC ENGINE & VOLUME CONTROL
# ============================================================
class GoogleMapsTrafficEngine:
    def __init__(self, daily_quota: int = 200000, target_spent_pct: float = 0.20):
        self.daily_quota = max(1, daily_quota)  # Prevent zero division
        self.target_spent_pct = target_spent_pct
        self.local_daily_tokens = 0
        self.integral_error = 0.0
        self.volume_level = 1.0
        
        self.priority_hierarchy = {
            1: {"dsn": 1000, "dsl": 8000, "mode": "CUSTOMIZED-MEDIA/TEXT + SEARCH"},
            4: {"dsn": 950,  "dsl": 2000, "mode": "CUSTOMIZED-MEDIA/TEXT + SEARCH"},
            6: {"dsn": 0,    "dsl": 700,  "mode": "RANDOM-MEDIA-ONLY"},
            7: {"dsn": 0,    "dsl": 400,  "mode": "RANDOM-MEDIA-ONLY + MIRROR-ONLY"},
            8: {"dsn": 800,  "dsl": 1000, "mode": "CUSTOMIZED-TEXT ONLY + MIRROR-ONLY"},
            9: {"dsn": 1000, "dsl": 3000, "mode": "CUSTOMIZED-TEXT ONLY"},
            2: {"dsn": 0,    "dsl": 700,  "mode": "RANDOM-MEDIA-ONLY (1 msg/member)"},
            5: {"dsn": 0,    "dsl": 700,  "mode": "RANDOM-MEDIA-ONLY (1 msg/member)"},
            3: {"dsn": 0,    "dsl": 700,  "mode": "RANDOM-MEDIA-ONLY"}
        }

    @property
    def spent_fraction(self) -> float:
        return max(0.0, min(1.0, self.local_daily_tokens / self.daily_quota))

    def update_navigation(self):
        S_current = self.spent_fraction
        error = S_current - self.target_spent_pct
        
        # Integral Anti-Windup Clamping (prevents infinite growth)
        self.integral_error = max(-2.0, min(2.0, self.integral_error + error))
        
        Kp, Ki = 1.5, 0.2
        adjustment = (Kp * error) + (Ki * self.integral_error)
        new_volume = 1.0 - adjustment
        
        step = 0.01
        if new_volume > self.volume_level:
            self.volume_level = min(new_volume, self.volume_level + step)
        elif new_volume < self.volume_level:
            self.volume_level = max(new_volume, self.volume_level - step)

        self.volume_level = max(0.05, min(1.0, self.volume_level))

    def get_allocation(self, priority_tier: int) -> Tuple[int, int, str]:
        base = self.priority_hierarchy.get(priority_tier, {"dsn": 100, "dsl": 500, "mode": "RANDOM-MEDIA-ONLY"})
        S = self.spent_fraction
        
        if S >= 0.70 and priority_tier not in (1, 4):
            return 0, 0, "RANDOM-MEDIA-ONLY (TOKEN-FREE)"
        elif S >= 0.40 and priority_tier in (6, 7, 2, 5, 3):
            return 0, int(base["dsl"] * 0.5), "TOKEN-CHEAPER RANDOM-MEDIA-ONLY"

        effective_dsn = int(base["dsn"] * self.volume_level)
        effective_dsl = int(base["dsl"] * self.volume_level)
        return effective_dsn, effective_dsl, base["mode"]

    def record_expenditure(self, tokens: int):
        self.local_daily_tokens += max(1, tokens)
        self.update_navigation()

traffic_engine = GoogleMapsTrafficEngine()
# ============================================================
# 5. ASYNCHRONOUS SINGLE-WRITER STATE PERSISTENCE
# ============================================================

def trigger_save_state():
    try:
        state_save_queue.put_nowait(True)
    except Exception:
        pass

def load_state():
    global channel_cursors, p7_cursors, p8_cursors, blocked_users, p3_member_indices
    if not os.path.exists(STATE_PATH):
        return
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        for item in data.get("replied", []):
            replied_message_ids.add(item)
        for item in data.get("p2_done_members", []):
            p2_replied_member_ids.add(item)
            
        channel_cursors.update(data.get("cursors", {}))
        p7_cursors.update(data.get("p7_cursors", {}))
        p8_cursors.update(data.get("p8_cursors", {}))
        blocked_users.update(data.get("blocked_users", {}))
        p3_member_indices.update(data.get("p3_member_indices", {}))
        print(f"[STATE] Successfully loaded state from {STATE_PATH}")
    except Exception as e:
        print(f"[STATE] Load failed: {e}")

async def state_writer_worker():
    while True:
        await state_save_queue.get()
        
        while not state_save_queue.empty():
            try:
                state_save_queue.get_nowait()
                state_save_queue.task_done()
            except asyncio.QueueEmpty:
                break
                
        state_save_queue.task_done()

        async with state_io_lock:
            def _write():
                try:
                    dir_name = os.path.dirname(STATE_PATH)
                    if dir_name:  # Only create directory if path contains one
                        os.makedirs(dir_name, exist_ok=True)
                        
                    data = {
                        "replied": replied_message_ids.snapshot()[-25000:],
                        "p2_done_members": p2_replied_member_ids.snapshot()[-8000:],
                        "cursors": channel_cursors,
                        "p7_cursors": p7_cursors,
                        "p8_cursors": p8_cursors,
                        "blocked_users": blocked_users,
                        "p3_member_indices": p3_member_indices,
                    }
                    temp_path = STATE_PATH + ".tmp"
                    with open(temp_path, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=4, ensure_ascii=False)
                    os.replace(temp_path, STATE_PATH)
                except Exception as e:
                    print(f"[STATE] Write failed: {e}")

            await asyncio.to_thread(_write)
# ============================================================
# 6. ASYNCHRONOUS TELEMETRY SERVER (aiohttp.web)
# ============================================================
_web_runner = None

async def handle_health(request):
    return web.Response(text="Oatmeal AI V101.0 Operational")

async def handle_telemetry(request):
    data = {
        "status": "online",
        "local_daily_tokens": traffic_engine.local_daily_tokens,
        "spent_fraction": traffic_engine.spent_fraction,
        "volume_level": traffic_engine.volume_level,
        "queue_size": delay_queue.qsize(),
    }
    return web.json_response(data)

async def _run_web_server():
    global _web_runner
    app = web.Application()
    app.router.add_get("/telemetry", handle_telemetry)
    app.router.add_get("/", handle_health)  # Automatically handles HEAD requests for "/"
    
    port = int(os.environ.get("PORT", 10000))    
    port = int(os.environ.get("PORT", 10000))
    _web_runner = web.AppRunner(app)
    await _web_runner.setup()
    site = web.TCPSite(_web_runner, "0.0.0.0", port)
    await site.start()
    print(f"[TELEMETRY] Async web server active on port {port}")
    
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        if _web_runner:
            await _web_runner.cleanup()

def start_instant_web_server():
    """Runs the aiohttp server in its own event loop thread so it binds instantly."""
    asyncio.run(_run_web_server())


# ============================================================
# 7. TOKEN-FREE BIMEDIA GENERATOR (All 9 Categories & Dual Modes)
# ============================================================
class BiMediaGenerator:
    @staticmethod
    async def token_free_web_search(session: aiohttp.ClientSession, query: str, domain_filter: str = "") -> List[str]:
        """Performs a token-free DuckDuckGo Lite search and extracts matching URLs."""
        cleaned = RE_CLEAN_MENTIONS.sub('', query)
        cleaned = RE_CLEAN_SEARCH.sub('', cleaned)
        cleaned = RE_CLEAN_SYMBOLS.sub(' ', cleaned)
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()[:100]
        if not cleaned:
            cleaned = "oatmeal joey arnold 90s"

        url = "https://lite.duckduckgo.com/lite/"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        urls = []
        try:
            async with session.post(url, data={'q': cleaned}, headers=headers, timeout=6) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    # Extract href links from duckduckgo lite results
                    raw_links = re.findall(r'class="result-link"[^>]*href="([^"]+)"', html)
                    for link in raw_links:
                        # Unquote DuckDuckGo redirect wrapper links if present
                        if "uddg=" in link:
                            parsed_qs = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)
                            if "uddg" in parsed_qs:
                                link = parsed_qs["uddg"][0]
                        
                        if domain_filter:
                            if domain_filter in link.lower():
                                urls.append(link)
                        else:
                            urls.append(link)
        except Exception:
            pass
        return urls

    @classmethod
    async def fetch_media_by_type(cls, session: aiohttp.ClientSession, idx: int, query: str = "") -> Tuple[str, Optional[discord.Embed]]:
        """
        Picks the idx-th media category (1-9) and fetches a token-free media asset.
        Implements dice-roll / random indexing to pick the N-th result from search to prevent repetition.
        """
        dice_roll = random.randint(0, 7)  # Simulated dice roll index for search result selection

        # 1. VIDEOS (YouTube, TikTok, etc.)
        if idx == 1:
            search_query = query if query else random.choice(["classic 90s commercial youtube", "star wars clip youtube", "retro cartoon youtube", "tiktok viral meme"])
            found_urls = await cls.token_free_web_search(session, search_query, domain_filter="youtube.com/watch")
            if not found_urls:
                found_urls = await cls.token_free_web_search(session, search_query, domain_filter="youtu.be")
            
            if found_urls:
                selected_item = found_urls[dice_roll % len(found_urls)]
                return selected_item, None
            return random.choice(["https://www.youtube.com/watch?v=dQw4w9WgXcQ", "https://www.youtube.com/watch?v=jNQXAC9IVRw"]), None

        # 2. GIFs (Tenor.com and web sources)
        elif idx == 2:
            q = query if query else random.choice(["reaction", "star wars", "funny meme", "retro 90s", "thinking", "happy"])
            encoded_q = urllib.parse.quote(f"{q} gifs")
            tenor_url = f"https://tenor.com/search/{encoded_q}"
            headers = {"User-Agent": "Mozilla/5.0"}
            gif_url = ""
            try:
                async with session.get(tenor_url, headers=headers, timeout=4) as resp:
                    if resp.status == 200:
                        html = await resp.text()
                        ids = RE_TENOR_ID.findall(html)
                        if ids:
                            selected_id = ids[dice_roll % len(ids)]
                            gif_url = f"https://media.tenor.com/{selected_id}/tenor.gif"
            except Exception:
                pass

            if not gif_url:
                gif_url = "https://media.tenor.com/2U5Vn4_h2zEAAAAC/happy-happy-joy-joy-ren-and-stimpy.gif"
            
            emb = discord.Embed()
            emb.set_image(url=gif_url)
            return "", emb

        # 3. PHOTOS (Picsum, Imgflip, Imgur, Pixabay with backups)
        elif idx == 3:
            photo_source_type = random.choice(["picsum", "search_photo"])
            if photo_source_type == "picsum":
                photo_url = f"https://picsum.photos/seed/{random.randint(10, 99999)}/640/360.jpg"
            else:
                search_query = query if query else random.choice(["nature photography", "retro 90s photo", "architecture wallpaper", "funny cat"])
                found_urls = await cls.token_free_web_search(session, search_query, domain_filter="imgur.com")
                if not found_urls:
                    found_urls = await cls.token_free_web_search(session, search_query, domain_filter="pixabay.com")
                
                photo_url = found_urls[dice_roll % len(found_urls)] if found_urls else f"https://picsum.photos/seed/{random.randint(10, 99999)}/640/360.jpg"

            emb = discord.Embed()
            emb.set_image(url=photo_url)
            return "", emb

        # 4. DRAWINGS (Pollinations AI & custom text prompts)
        elif idx == 4:
            prompt_slug = urllib.parse.quote(query if query else random.choice(["retro synthwave sunset", "90s cartoon robot", "cyberpunk city street", "oil painting of a robotic cat"]))
            art_url = f"https://pollinations.ai/p/{prompt_slug}?width=640&height=360&seed={random.randint(1, 99999)}"
            emb = discord.Embed()
            emb.set_image(url=art_url)
            return "", emb

        # 5. AUDIO / MUSIC (Spotify, SoundCloud, Bandcamp, Apple Music, Audiomack, Mixcloud)
        elif idx == 5:
            platform_domain = random.choice(["spotify.com", "soundcloud.com", "bandcamp.com", "audiomack.com"])
            search_query = query if query else random.choice(["coltrane jazz track", "90s rock song", "synthwave soundtrack", "ambient study mix"])
            found_urls = await cls.token_free_web_search(session, search_query, domain_filter=platform_domain)
            
            if found_urls:
                return found_urls[dice_roll % len(found_urls)], None
            return random.choice([
                "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT", 
                "https://soundcloud.com/coltrane-official/my-favorite-things"
            ]), None

        # 6. WEB LINKS (Wikipedia, Internet Archive, etc.)
        elif idx == 6:
            target_domain = random.choice(["en.wikipedia.org", "archive.org"])
            search_query = query if query else random.choice(["history of video games", "Treacher Collins syndrome", "Star Wars expanded universe", "1985 in science"])
            found_urls = await cls.token_free_web_search(session, search_query, domain_filter=target_domain)
            
            if found_urls:
                return found_urls[dice_roll % len(found_urls)], None
            return random.choice([
                "https://en.wikipedia.org/wiki/Treacher_Collins_syndrome", 
                "https://archive.org/details/softwarelibrary_msdos_games"
            ]), None

        # 7. ASCII ART
        elif idx == 7:
            ascii_templates = [
                "```\n  (o.o)  Oatmeal AI V101.0\n  / | \\\n _/\\_/\\_\n```",
                "```\n  /\\_/\\\n ( o.o )  Arnold Attic\n  > ^ <\n```",
                "```\n   ____        _                   _ \n  / __|___  _| |_ _ __  ___ _ _  | |\n | (__/ _ \\(_-<  _| '  \\/ -_) '_| |_|\n  \\___\\___/ /_/\\__|_|_|_\\___|_|   (_)\n```",
                "```\n    _______\n   //  |ion|\n  //_______|\n  O       O\n```"
            ]
            return random.choice(ascii_templates), None

        # 8. EMOTICONS (Discord custom emoticons, unicode emoticons, kaomoji)
        elif idx == 8:
            emoticons = [
                "(ง'̀-'́)ง", "¯\\_(ツ)_/¯", "( ͡° ͜ʖ ͡°)", "(⌐■_■)", 
                "(づ｡◕‿‿◕｡)づ", "ಠ_ಠ", "(ʘ__ʘ)", "ᕕ( ᐛ )ᕗ", 
                ":rofl:", ":sunglasses:", ":fire:", ":robot:"
            ]
            return random.choice(emoticons), None

        # 9. INTERNAL & EXTERNAL STICKERS / BADGES
        elif idx == 9:
            stickers = ["⭐", "🥣", "🚀", "🕹️", "📼", "🔥", "💡", "🏆", "🎯", "🛡️"]
            return random.choice(stickers), None

        return "🥣", None

    @classmethod
    async def generate(cls, session: aiohttp.ClientSession, mode: str = "random", chat_text: str = "") -> Tuple[str, Optional[discord.Embed]]:
        """
        BIMEDIA-GENERATOR Entrypoint:
        - RANDOMIZED MEDIA GENERATOR: Selects random media TYPE (1-9) and embeds/displays random data.
        - CUSTOMIZED MEDIA GENERATOR: Selects random media TYPE, parses text from chat token-free, searches items, and replies.
        """
        # Select random media type category (1 to 9)
        idx = random.randint(1, 9)

        # If Customized mode is invoked with chat context, target or map categories dynamically
        if mode == "custom" and chat_text:
            tl = chat_text.lower()
            if any(w in tl for w in ("video", "youtube", "clip", "watch", "tiktok")): idx = 1
            elif any(w in tl for w in ("gif", "reaction", "funny", "meme")): idx = 2
            elif any(w in tl for w in ("photo", "image", "pic", "wallpaper")): idx = 3
            elif any(w in tl for w in ("draw", "art", "sketch", "paint", "pollination")): idx = 4
            elif any(w in tl for w in ("music", "song", "spotify", "sound", "audio")): idx = 5
            elif any(w in tl for w in ("link", "wiki", "read", "archive", "history")): idx = 6
            elif any(w in tl for w in ("ascii", "terminal", "box")): idx = 7
            elif any(w in tl for w in ("emote", "smile", "face")): idx = 8
            elif any(w in tl for w in ("sticker", "badge", "star")): idx = 9

        # Execute token-free fetch based on selected type and chat text query
        media_content, embed = await cls.fetch_media_by_type(session, idx, query=chat_text if mode == "custom" else "")
        return media_content, embed

# ============================================================
# 8. SEARCH SUBSYSTEM (Single-Flight Caching & Non-Blocking)
# ============================================================
class AsyncSearchCache:
    def __init__(self, ttl: float = 300.0, max_size: int = 200):
        self.ttl = ttl
        self.max_size = max_size
        self._cache: Dict[str, Tuple[float, str]] = {}
        self._order: deque = deque()  # O(1) pops
        self._inflight: Dict[str, asyncio.Future] = {}
        self._lock = asyncio.Lock()

    async def get_or_fetch(self, query: str, session: aiohttp.ClientSession) -> str:
        q_key = query.lower().strip()
        now = time.monotonic()

        # 1. Fast path: Check cache inside lock, then release immediately
        async with self._lock:
            if q_key in self._cache:
                ts, res = self._cache[q_key]
                if now - ts < self.ttl:
                    return res
                else:
                    self._cache.pop(q_key, None)
                    # Note: deque removal requires iteration, but with max_size=200 it's tiny. 
                    # Alternatively, handle stale keys lazily during insertion.

            # 2. Check if already in-flight
            if q_key in self._inflight:
                future = self._inflight[q_key]
            else:
                future = None

        # If it was in-flight, await it OUTSIDE the lock!
        if future is not None:
            return await future

        # 3. Register ourselves as the creator of this in-flight future
        async with self._lock:
            # Double check under lock
            if q_key in self._inflight:
                future = self._inflight[q_key]
            else:
                loop = asyncio.get_running_loop()
                future = loop.create_future()
                self._inflight[q_key] = future

        if future is not self._inflight.get(q_key):
            return await future

        try:
            cleaned = RE_CLEAN_MENTIONS.sub('', query)
            cleaned = RE_CLEAN_SEARCH.sub('', cleaned)
            cleaned = RE_CLEAN_SYMBOLS.sub(' ', cleaned)
            cleaned = re.sub(r'\s+', ' ', cleaned).strip()[:120]

            url = "https://lite.duckduckgo.com/lite/"
            headers = {"User-Agent": "Mozilla/5.0"}
            async with session.post(url, data={'q': cleaned}, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    snippets = RE_SNIPPETS.findall(html)
                    clean_s = [RE_HTML_STRIP.sub('', s).strip() for s in snippets[:4] if len(s.strip()) > 15]
                    res = "REAL-TIME SEARCH RESULTS:\n" + "\n".join(f"- {s}" for s in clean_s) if clean_s else ""
                else:
                    res = ""

            async with self._lock:
                if len(self._cache) >= self.max_size:
                    oldest = self._order.popleft()  # O(1) eviction
                    self._cache.pop(oldest, None)
                self._cache[q_key] = (time.monotonic(), res)
                self._order.append(q_key)
                
                future.set_result(res)
                self._inflight.pop(q_key, None)
            return res
        except Exception as e:
            async with self._lock:
                self._inflight.pop(q_key, None)
                if not future.done():
                    future.set_exception(e)
            return ""
search_cache = AsyncSearchCache()

async def token_free_discord_search(channel: Any, query: str) -> str:
    keywords = [w.lower() for w in RE_WORD_TOKENS.findall(query) if len(w) > 2 and w.lower() != "discord"]
    if not keywords:
        keywords = ["oatmeal", "joey"]
    async for msg in channel.history(limit=100):
        if msg.author.bot:
            continue
        if any(kw in (msg.content or "").lower() for kw in keywords):
            return f"DISCORD SEARCH COMPLETE: Found message from {msg.author.display_name}: \"{msg.content[:150]}+\""
    return "DISCORD SEARCH COMPLETE: No matching messages found."

### **Section 10: Job Execution Engine (`process_job`)**

async def token_free_discord_search(channel: Any, query: str) -> str:
    keywords = [w.lower() for w in RE_WORD_TOKENS.findall(query) if len(w) > 2 and w.lower() != "discord"]
    if not keywords:
        keywords = ["oatmeal", "joey"]
    async for msg in channel.history(limit=100):
        if msg.author.bot:
            continue
        if any(kw in (msg.content or "").lower() for kw in keywords):
            return f"DISCORD SEARCH COMPLETE: Found message from {msg.author.display_name}: \"{msg.content[:150]}+\""
    return "DISCORD SEARCH COMPLETE: No matching messages found."

async def process_job(job: PendingJob, session: aiohttp.ClientSession):
    global GROQ_RATE_LIMITED_UNTIL
    dsn, dsl, mode = traffic_engine.get_allocation(job.priority_tier)
    timeline = extract_relevant_timeline(job.prompt_text or "")
    
    search_context = ""
    prompt_lower = (job.prompt_text or "").lower()
    
    if job.priority_tier in (1, 4) and "discord" in prompt_lower:
        search_context = await token_free_discord_search(job.channel, job.prompt_text)
    elif job.priority_tier == 1 and any(w in prompt_lower for w in ("search", "find", "price", "google", "what", "look")):
        search_context = await search_cache.get_or_fetch(job.prompt_text, session)

    system = f"You are Joey Arnold (Oatmeal Joey Arnold, @JoeyArnoldVN). Speak in first person. Voice: rapid, playful, nostalgic 90s.\n\n{timeline}"
    if search_context and not search_context.startswith("DISCORD SEARCH COMPLETE"):
        system += f"\n\nSearch Results:\n{search_context}"

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": job.prompt_text or "Thought?"}
    ]
    
    max_tokens = min(dsl, max(12, dsn))
    reply = "Oatmeal AI operational."
    actual_tokens = 5

    if search_context and search_context.startswith("DISCORD SEARCH COMPLETE"):
        reply = search_context
        max_tokens = 0
    elif time.time() < GROQ_RATE_LIMITED_UNTIL:
        max_tokens = 0

    if max_tokens > 0:
        url = "[https://api.groq.com/openai/v1/chat/completions](https://api.groq.com/openai/v1/chat/completions)"
        payload = {
            "model": MODEL_NAME,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.72
        }
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": "OatmealBot/101.0"
        }
        try:
            async with session.post(url, json=payload, headers=headers, timeout=15) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    reply = data["choices"][0]["message"]["content"].strip()
                    actual_tokens = data.get("usage", {}).get("total_tokens", max_tokens // 2)
                    traffic_engine.record_expenditure(actual_tokens)
                elif resp.status == 429:
                    GROQ_RATE_LIMITED_UNTIL = time.time() + 60.0
                    print(f"[GROQ API] Rate limited (429). Backing off for 60s.")
                else:
                    err_text = await resp.text()
                    print(f"[GROQ API ERROR] Status {resp.status}: {err_text}")
        except Exception as e:
            print(f"[GROQ API EXCEPTION] {e}")

    media_url, embed = "", None
    if not search_context and (job.priority_tier in (2, 3, 6, 7, 9) or random.random() < 0.35):
        media_url, embed = await BiMediaGenerator.generate(session, "custom" if job.prompt_text else "random", job.prompt_text or "")

    content, kwargs = await build_payload(reply, media_url, embed)

    for attempt in range(3):
        try:
            if job.reference_message:
                await job.reference_message.reply(content=content, mention_author=False, **kwargs)
            else:
                await job.channel.send(content=content, **kwargs)
            break
        except Exception:
            await asyncio.sleep(0.5 * (2 ** attempt))


# ============================================================
# 9. UNTRUNCATED BIOGRAPHICAL TIMELINE & KERNEL
# ============================================================
FULL_ANNUAL_OUTLINE = """
1985: BORN Monday 11 February 1985 at 6 AM in Forest Grove, Oregon, trailer #163 Rose Grove mobile home park. BORN with Treacher Collins syndrome (affecting facial bones, ears, and hearing). PARENTS Marilyn Morehead (from Seattle) and Don Rasp (from Los Angeles, his dad Peter Rasp from Amsterdam). HOME CHURCH Cornelius Community Baptist Church (CCBC) 1985-2002. STARTED wearing glasses between 1985-1987.
1986: HOMESCHOOLED by mom until 2000. PARTLY deaf first ten years due to Treacher Collins.
1987: ACTED as sheep in a Sharon Hundley homeschooling Thanksgiving Pilgrim play.
1988: BROTHER saved my life while mom was hit by a vehicle. VISITED Seattle for great grandma's birthday. VISITED circus and lake.
1989: ATTENDED Awana. SAW Batman (1989) in theaters, sparking a lifelong love for cinematic spectacle.
1990: BECAME a Christian. VISITED the Elkind house when Kenni was born.
1991: PLAYED SEGA Sonic with neighbor Jesse Vargas. RECEIVED NES. CCBC Olympians. SCARRED index finger while camping. LEARNED to ride bikes from siblings. SAW Hook starring Robin Williams as Peter Pan.
1992: SAW Newsies. SAW Honey I Blew Up the Kid. SAW Home Alone 2. WENT to Hillsboro fair, Forest Grove fair, California, beach arcade, and Disneyland. REGULAR wrestling events. STARTED thinking about thinking.
1993: SAW TMNT 3. SAW Jurassic Park. PLAYED basketball and baseball. TRI-FAMILY Morehead Reunion. TEMPORARILY lost at Hagg Lake. RECEIVED SNES. MET Bhakti Sa. THINKING about adulthood.
1994: SAW The Lion King. SAW Star Trek. RECEIVED Rival Turf. AWANA at HFBC Hillsboro. MET Bill Bailey (his dad later called me Oatmeal Boy).
1995: LEARNED karate. PLAYED shepherd in CCBC Christmas play. SAW Apollo 13. SAW Pocahontas. SAW Batman Forever. INTERNET became heavier. BEGAN ear tubes after years of partial deafness.
1996: FILMED Arnold Attic movies – Power Rangers, Wrestling, and Heaven Or Hell. FIRST long movie before age 11. SAW Independence Day.
1997: BUILT a taxi wagon and a fort. FED gerbils. SAW Star Wars.
1998: BAPTIZED at CCBC. DREW Action Strip comics. CONTINUED AWANA.
1999: SEATTLE Splash. Word of Life Super Bowl. West Coast Camp at Lassen Pines. RECEIVED Dreamcast. SAW Bicentennial Man. SAW Star Trek: Insurrection. WOL Teens Involved at HCBC. CALIFORNIA trip. SAW The Matrix.
2000: FILMED I Want It with Guinea Pig Ra Ra, Newsies Lip Sync, and Valentines. VISITED Idaho. LEFT homeschool and started Forest Grove High School. ART classes. MET Salena Willner. STARTED writing articles.
2001: FILMED Gangster Run with Big Red Mickey Morehead. FILMED Cat's Life. SAW Lord of the Rings.
2002: FILMED Road Kill. FILMED Phenomenon. SKI retreat. TRAVELED to Hawaii to experience tropical island culture.
2003: TIFFANY Cumbo died. BASKETBALL manager with Richie VanDyke filming games. ACTING classes. SAW Matrix Reloaded.
2004: BUILT Carpetball Pocket Ball table. ATTENDED Word of Life Bible Institute in New York. BLOGGED on Xanga. NYC New Year’s Eve. BOOTLEGGED The Incredibles in New Jersey. TRAVELED internationally to Vietnam to teach English.
2005: CAMP counselor at Snow Camp, West Coast Camp, and Ranch Camp. SAW Star Wars: Revenge of the Sith.
2006: ATTENDED Appalachian Bible College in West Virginia. FELL in love with Jen Moore. VISITED Quebec. ACTED in Live For Only Jesus video with Sawyer Frye.
2007: HIRED by Mike Kurtz as counselor at Salvation Army Camp Kuratli. MET Megan McQuade. Western Youth Institute and Rob Noland’s Revolution Hawaii. MISSIONARY service in Hawaii (2007-2008).
2008: MOVED to Alder 414 in Portland. WORKED at Kells and for Daniel Grout. ICE Breaker era. SAW Iron Man.
2009: ACTED in Spaceman & Diaperboy with Lincoln Hawk. SANG Animals Are Dying. OATMEAL Fake News trolls began on the original Ojawall YouTube. APPEARED on an iJustine Ask IJ episode. AUDITIONED for Comcast's Wanted Adventure Host. SAW Coraline.
2010: BLAKE Webb died. WORKED Camp Redwood Glen. MET Farmer Hanna. LIVED at Charlotte Battle’s house. HELPED Nick Becker film. MET Sharon Clayton at WYI who introduced The Walking Dead.
2011: LIVED at 163. REBUILT the Attic Shed. EDITED Arnold Attic teaser videos. SAW Drive.
2012: ATTIC Shed died. WORKED for Brent Groth building the Mea Omnia website. PUT in Hillsboro jail after misunderstanding. MOVED to Vietnam (Bac Ninh then Saigon) around Thanksgiving.
2013: MOVED to Saigon. HELPED by Hanoi Anna. LIVED at Honey Tracy’s house and Balo Backpacking Hostel. TAUGHT English with Fellowship Youth Group. CAMBODIA visa run. TAUGHT at Tan Van school and New Star school in Tan Phu. SAW Pacific Rim.
2014: TAUGHT at multiple places. Lived at Leaf Pagoda, Dai Trinh hostel, Remi Cafe, District 7, Mr. Ribs, Circle K, and Old Ink tattoos apartment.
2015: LIVED at Old Ink tattoos apartment until November. TAUGHT English at 23/9 Park on Phạm Ngũ Lão. TAUGHT Lego Mark at McDonald’s. VISA problem in December.
2016: LIVED at Anna Barbie Hostel. ENDORSED Trump. SAW Alex Jones and Kung Fu Panda 3. LAST Vietnam Thanksgiving.
2017: SLEPT at Opera Tea Club. YOUTUBE ban. FLEW home to Oregon on Thanksgiving Day. SAW The Simpsons, Power Rangers, and Blossom. GOT Bitcoin. JOINED Discord and Steemit. BEGAN blogging on Hive and Steemit.
2018: MOVED to Shelton, Washington. BUILT the Book Shed with Tom Taylor and Larry Mitchell. WORKED at a music store.
2019: VISITED Vancouver BC and Seattle. CREATED the Oatmeal World Discord server. YOUTH Pastor Assistant. PRACTICED driving. MET Roy Merrick on Infocomms.
2020: CLAM digging at Round Tree Farm. TORE down a fence. SAW The Expanse, The 100, The Boys, Suits, Star Trek: Picard, and Outlander.
2021: BUILT a chicken house. PAINTED a planter box. SAW Umbrella Academy, Cobra Kai, Brave New World, X-Files, and Made For Love.
2022: DEAD Wing Dork made a video about me. CHRISSIE Mayr mentioned me on Simpcast. VIDEOS with Memes World Productions including Mango Haze and Stumble Chat. SAW Top Gun: Maverick.
2023: CREATED music on Suno AI. GREYHOUND. BUILT garden fences. YOUTUBE restored JoeyArnoldvn. VISITED Pastor Dave Webber and Sharon Hundley. FOUND the 44 Arnold Attic VHS tapes.
2024: OREGON Rick trip. MERCER Island and Ted Pickett. SAW The Walking Dead: The Ones Who Live. Account dispute for ojawall@gmail.com.
2025: LARRY died. OBTAINED driving license. CAMPED with mom. MOREHEAD Family Reunion in Oregon. TIKTOK ban.
2026: BUILT the Oatmeal AI chatbot. GOT a pet rabbit. DROVE to Pickett Family Reunion in Long Beach, Washington. Publishing on Hive Blog, YouTube, and Rumble under Synaptic Seasoning. Mastered advanced Python Discord automation and continuous channel archiving.
"""

SHORT_IDENTITY = (
    "You are Oatmeal Joey Arnold (Joseph Scott Arnold), born Monday 11 February 1985 at 6 AM in Forest Grove, Oregon, "
    "trailer #163 Rose Grove mobile home park. Your mom Marilyn Morehead is from Seattle, and your dad Don Rasp is from Los Angeles (with his dad from Amsterdam). "
    "You were born with Treacher Collins syndrome. "
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
    "Never invent facts. If you don't know, say so honestly or pull from the timeline. "
    "You may be hyperbolic and playful, but never fabricate real events."
)

ANTI_HALLUCINATION = "Rely strictly on the provided outline for personal history. If unknown, say so or pivot."

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

# ============================================================
# 9. UNTRUNCATED BIOGRAPHICAL TIMELINE & KERNEL
# ============================================================
# (Keep your FULL_ANNUAL_OUTLINE and persona constants above this)
def extract_relevant_timeline(prompt: str) -> str:
    if not prompt:
        return SHORT_IDENTITY + "\n\n" + FULL_ANNUAL_OUTLINE
    years = set(RE_YEARS.findall(prompt))
    lines = [ln for ln in FULL_ANNUAL_OUTLINE.strip().splitlines() if any(ln.startswith(f"{y}:") for y in years)]
    return "Relevant timeline:\n" + "\n".join(lines) if lines else SHORT_IDENTITY + "\n\n" + FULL_ANNUAL_OUTLINE

# ============================================================
# 10. JOB EXECUTION ENGINE
# ============================================================

async def build_payload(content_str: str, media_url: str = "", embed: Optional[discord.Embed] = None) -> Tuple[str, dict]:
    """Formats text and embeds for Discord dispatch."""
    kwargs = {}
    final_content = content_str.strip() if content_str else ""
    
    if media_url and not embed:
        if final_content:
            final_content += f"\n{media_url}"
        else:
            final_content = media_url
            
    if embed:
        kwargs["embed"] = embed
        
    return final_content, kwargs
async def process_job(job: PendingJob, session: aiohttp.ClientSession):
    global GROQ_RATE_LIMITED_UNTIL
    dsn, dsl, mode = traffic_engine.get_allocation(job.priority_tier)
    timeline = extract_relevant_timeline(job.prompt_text or "")
    
    search_context = ""
    prompt_lower = (job.prompt_text or "").lower()
    if job.priority_tier == 1:
        if "discord" in prompt_lower:
            search_context = await token_free_discord_search(job.channel, job.prompt_text)
        elif any(w in prompt_lower for w in ("search", "find", "price", "google", "what", "look")):
            search_context = await search_cache.get_or_fetch(job.prompt_text, session)

    system = f"You are Joey Arnold (Oatmeal Joey Arnold, @JoeyArnoldVN). Speak in first person. Voice: rapid, playful, nostalgic 90s.\n\n{timeline}"
    if search_context:
        system += f"\n\nSearch Results:\n{search_context}"

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": job.prompt_text or "Thought?"}
    ]
    
    max_tokens = min(dsl, max(12, dsn))
    reply = "Oatmeal AI operational."
    actual_tokens = 5  # Lower default cost if skipped

    # Check if currently globally rate limited
    if time.time() < GROQ_RATE_LIMITED_UNTIL:
        max_tokens = 0  # Skip API call to save rate limit quota

    if max_tokens > 0:
        url = "https://api.groq.com/openai/v1/chat/completions"
        payload = {
            "model": MODEL_NAME,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.72
        }
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": "OatmealBot/101.0"
        }
        try:
            async with session.post(url, json=payload, headers=headers, timeout=15) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    reply = data["choices"][0]["message"]["content"].strip()
                    actual_tokens = data.get("usage", {}).get("total_tokens", max_tokens // 2)
                    traffic_engine.record_expenditure(actual_tokens)
                elif resp.status == 429:
                    # Respect rate limits and back off for 60 seconds
                    GROQ_RATE_LIMITED_UNTIL = time.time() + 60.0
                    print(f"[GROQ API] Rate limited (429). Backing off for 60s.")
                else:
                    err_text = await resp.text()
                    print(f"[GROQ API ERROR] Status {resp.status}: {err_text}")
        except Exception as e:
            print(f"[GROQ API EXCEPTION] {e}")

    media_url, embed = "", None
    if not search_context and (job.priority_tier in (2, 3, 6, 7, 9) or random.random() < 0.35):
        media_url, embed = await BiMediaGenerator.generate(session, "custom" if job.prompt_text else "random", job.prompt_text or "")

    content, kwargs = await build_payload(reply, media_url, embed)

    for attempt in range(3):
        try:
            if job.reference_message:
                await job.reference_message.reply(content=content, mention_author=False, **kwargs)
            else:
                await job.channel.send(content=content, **kwargs)
            break
        except Exception:
            await asyncio.sleep(0.5 * (2 ** attempt))

# ============================================================
# 11. ALL 9 BACKGROUND PRIORITY COROUTINES (P2 - P9)
# ============================================================
async def execute_priority_2():
    await bot.wait_until_ready()
    while not bot.is_closed():
        await asyncio.sleep(random.uniform(900, 2200))
        try:
            for guild in list(bot.guilds):
                channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages]
                if channels:
                    ch = random.choice(channels)
                    url, embed = await BiMediaGenerator.generate(HTTP_SESSION, "random")
                    content, kwargs = await build_payload("🥣", url, embed)
                    async for msg in ch.history(limit=20):
                        if not msg.author.bot and p2_key(guild.id, msg.author.id) not in p2_replied_member_ids:
                            await msg.reply(content=content, mention_author=False, **kwargs)
                            p2_replied_member_ids.add(p2_key(guild.id, msg.author.id))
                            trigger_save_state()
                            break
                    break
        except Exception as e:
            print(f"[P2 ERROR] {e}")

async def execute_priority_3():
    await bot.wait_until_ready()
    while not bot.is_closed():
        await asyncio.sleep(random.uniform(1600, 4200))
        try:
            for guild in list(bot.guilds):
                # Safely get members; fallback to fetching if cache is empty
                members = [m for m in guild.members if not m.bot]
                if not members and guild.chunked == False:
                    try:
                        async for m in guild.fetch_members(limit=50):
                            if not m.bot:
                                members.append(m)
                    except Exception:
                        pass
                
                if not members:
                    continue
                    
                members = sorted(members, key=lambda x: x.name.lower())
                idx = p3_member_indices.get(str(guild.id), 0) % len(members)
                target = members[idx]
                p3_member_indices[str(guild.id)] = (idx + 1) % len(members)
                trigger_save_state()
                
                channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages]
                if channels:
                    ch = random.choice(channels)
                    url, embed = await BiMediaGenerator.generate(HTTP_SESSION, "random")
                    content, kwargs = await build_payload(f"@{target.name} Salutations from the Arnold Attic.", url, embed)
                    await ch.send(content=content, **kwargs)
                break
        except Exception as e:
            print(f"[P3 ERROR] {e}")
async def execute_priority_4():
    await bot.wait_until_ready()
    while not bot.is_closed():
        await asyncio.sleep(random.uniform(18, 40))
        try:
            for guild in list(bot.guilds):
                channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages]
                for ch in channels:
                    async for msg in ch.history(limit=10):
                        if not msg.author.bot and msg.content and msg.id not in replied_message_ids:
                            replied_message_ids.add(msg.id)
                            trigger_save_state()
                            job = PendingJob(
                                priority_tier=4,
                                timestamp=time.time(),
                                channel=ch,
                                prompt_text=f"Quick witty take: '{msg.content[:110]}'",
                                reference_message=msg,
                                author_id=msg.author.id
)
                            await delay_queue.put(job)
                            break
                    break
        except Exception as e:
            print(f"[P4 ERROR] {e}")

async def execute_priority_5():
    await bot.wait_until_ready()
    while not bot.is_closed():
        await asyncio.sleep(random.uniform(700, 2100))
        try:
            for guild in list(bot.guilds):
                channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages]
                if channels:
                    ch = random.choice(channels)
                    async for msg in ch.history(limit=5):
                        if not msg.author.bot and msg.id not in replied_message_ids:
                            replied_message_ids.add(msg.id)
                            trigger_save_state()
                            job = PendingJob(5, time.time(), ch, "Short reflection.", reference_message=msg, author_id=msg.author.id)
                            await delay_queue.put(job)
                            break
                    break
        except Exception as e:
            print(f"[P5 ERROR] {e}")

async def execute_priority_6():
    await bot.wait_until_ready()
    while not bot.is_closed():
        await asyncio.sleep(random.uniform(1000, 3200))
        try:
            for guild in list(bot.guilds):
                channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages]
                if channels:
                    ch = random.choice(channels)
                    url, embed = await BiMediaGenerator.generate(HTTP_SESSION, "random")
                    content, kwargs = await build_payload("", url, embed)
                    await ch.send(content=content, **kwargs)
                    break
        except Exception as e:
            print(f"[P6 ERROR] {e}")

async def execute_priority_7():
    await bot.wait_until_ready()
    while not bot.is_closed():
        await asyncio.sleep(random.uniform(35, 120))
        try:
            for guild in list(bot.guilds):
                channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages]
                for ch in channels:
                    cursor = p7_cursors.get(str(ch.id), 0)
                    kwargs = {"limit": 30, "oldest_first": True}
                    if cursor > 0:
                        kwargs["after"] = discord.Object(id=cursor)
                    
                    target = None
                    async for msg in ch.history(**kwargs):
                        if msg.author == bot.user:
                            target = msg
                            break  # Stop at the first valid older bot message found
                            
                    if target is None:
                        continue
                        
                    url, embed = await BiMediaGenerator.generate(HTTP_SESSION, "random")
                    content, kwargs_payload = await build_payload("", url, embed)
                    
                    try:
                        # ✅ SUCCESS-ONLY: Send first, advance cursor ONLY on verified success
                        await target.reply(content=content, mention_author=False, **kwargs_payload)
                        p7_cursors[str(ch.id)] = target.id
                        trigger_save_state()
                        break
                    except Exception as send_err:
                        print(f"[P7] Reply failed, cursor NOT advanced: {send_err}")
                        continue
        except Exception as e:
            print(f"[P7 ERROR] {e}")


async def execute_priority_8():
    await bot.wait_until_ready()
    while not bot.is_closed():
        await asyncio.sleep(random.uniform(50, 160))
        try:
            for guild in list(bot.guilds):
                channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages]
                for ch in channels:
                    cursor = p8_cursors.get(str(ch.id), 0)
                    kwargs = {"limit": 30, "oldest_first": True}
                    if cursor > 0:
                        kwargs["after"] = discord.Object(id=cursor)
                    
                    target = None
                    async for msg in ch.history(**kwargs):
                        if msg.author == bot.user:
                            target = msg
                            break  # Stop at the first valid older bot message found
                            
                    if target is None:
                        continue
                        
                    # ✅ SUCCESS-ONLY: Update cursor ONLY after verifying a target bot message was found
                    p8_cursors[str(ch.id)] = target.id
                    trigger_save_state()
                    
                    job = PendingJob(
                        priority_tier=8,
                        timestamp=time.time(),
                        channel=ch,
                        prompt_text="Reflect briefly on this output.",
                        reference_message=target
                    )
                    await delay_queue.put(job)
                    break
        except Exception as e:
            print(f"[P8 ERROR] {e}")
async def execute_priority_9():
    await bot.wait_until_ready()
    while not bot.is_closed():
        await asyncio.sleep(random.uniform(2800, 5800))
        try:
            for guild in list(bot.guilds):
                channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages and c.permissions_for(guild.me).read_message_history]
                if channels:
                    ch = random.choice(channels)
                    chat_lines = []
                    last_msg = None
                    async for msg in ch.history(limit=15):
                        if not msg.author.bot and msg.content:
                            chat_lines.append(f"{msg.author.display_name}: {msg.content[:60]}")
                            last_msg = msg
                    if chat_lines and last_msg:
                        job = PendingJob(9, time.time(), ch, "Summarize this recent channel chat as an Oatmeal Recap.", reference_message=last_msg)
                        await delay_queue.put(job)
                        break
        except Exception as e:
            print(f"[P9 ERROR] {e}")

# ============================================================
# 12. WORKERS & BOT EVENTS
# ============================================================
async def delay_queue_worker():
    global HTTP_SESSION
    while True:
        try:
            job = await delay_queue.get()
            while time.monotonic() < GROQ_RATE_LIMITED_UNTIL:
                await asyncio.sleep(2)
            await process_job(job, HTTP_SESSION)
            delay_queue.task_done()
        except Exception as e:
            print(f"[WORKER ERROR] {e}")
            await asyncio.sleep(1)

# Keep track of active background tasks so they don't duplicate on reconnect
_background_tasks = []

@bot.event
async def on_ready():
    global TASKS_INITIALIZED, HTTP_SESSION, _background_tasks
    if HTTP_SESSION is None or HTTP_SESSION.closed:
        HTTP_SESSION = aiohttp.ClientSession()

    if TASKS_INITIALIZED:
        print("[SYSTEM] Reconnected to Discord Gateway. Existing background workers active.")
        return

    TASKS_INITIALIZED = True
    print("=" * 64)
    print(f" Oatmeal AI V101.0 Full Enterprise Monolith logged in as {bot.user}")
    print("=" * 64)
    load_state()

    # Web server is already bound via instant thread; keep discord background loops here:
    _background_tasks = [
        asyncio.create_task(state_writer_worker()),
        asyncio.create_task(delay_queue_worker()),
        asyncio.create_task(execute_priority_2()),
        asyncio.create_task(execute_priority_3()),
        asyncio.create_task(execute_priority_4()),
        asyncio.create_task(execute_priority_5()),
        asyncio.create_task(execute_priority_6()),
        asyncio.create_task(execute_priority_7()),
        asyncio.create_task(execute_priority_8()),
        asyncio.create_task(execute_priority_9())
    ]

@bot.event
@bot.event
async def on_message(message):
    if message.author.bot or message.guild is None or message.is_system() or message.author == bot.user:
        return

    now = time.time()
    author_id = message.author.id
    last_member_activity_time[message.channel.id] = now

    clean = RE_CLEAN_MENTIONS.sub("", message.content).strip()
    mentioned = bot.user in message.mentions
    
    # Robust check for replies to the bot (handles uncached references)
    replied_to_bot = False
    if message.reference:
        if message.reference.resolved and getattr(message.reference.resolved, 'author', None) == bot.user:
            replied_to_bot = True
        elif message.reference.cached_message and message.reference.cached_message.author == bot.user:
            replied_to_bot = True

    named = any(nick in message.content.lower() for nick in ("oatmeal", "joey", "arnold"))

    if mentioned or replied_to_bot or named:
        if message.id not in replied_message_ids:
            replied_message_ids.add(message.id)
            trigger_save_state()
            job = PendingJob(
                priority_tier=1,
                timestamp=now,
                channel=message.channel,
                prompt_text=clean or "Hello Oatmeal.",
                reference_message=message,
                author_id=author_id
            )
            await delay_queue.put(job)

# Graceful cleanup handler when the bot shuts down
async def close_bot_resources():
    global HTTP_SESSION, _background_tasks
    for task in _background_tasks:
        task.cancel()
    await asyncio.gather(*_background_tasks, return_exceptions=True)
    if HTTP_SESSION and not HTTP_SESSION.closed:
        await HTTP_SESSION.close()
    print("[SYSTEM] Cleaned up HTTP session and background workers.")

if __name__ == "__main__":
    # Start the web server immediately in a background daemon thread 
    # so Render detects the open port instantly upon container boot.
    web_thread = threading.Thread(target=start_instant_web_server, daemon=True)
    web_thread.start()
    
    try:
        bot.run(DISCORD_TOKEN)
    finally:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.run_until_complete(close_bot_resources())
            else:
                asyncio.run(close_bot_resources())
        except Exception:
            pass
