#!/usr/bin/env python3
"""
Oatmeal AI – Joey Arnold Discord Bot (#4059)
Master Production Edition V102.0 – Reinvented Enterprise Monolith
Optimized O(1) Bounded State, Resilient Media Engine, and Unified Priority Daemon.
"""

import os
import re
import json
import time
import logging
import asyncio
import threading
import random
import urllib.parse
import collections
import itertools
import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Set, Tuple, Optional
from datetime import datetime, timezone

import discord
import aiohttp
from aiohttp import web

# ============================================================
# 0. STRUCTURED LOGGING SETUP & PRE-COMPILED REGEX CONSTANTS
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (%(name)s) %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("OatmealAI")

RE_TENOR_ID = re.compile(r'/view/[^"]+-(\d+)')
RE_WORD_TOKENS = re.compile(r'\b\w+\b')
RE_CLEAN_MENTIONS = re.compile(r'<@!?\d+>')
RE_CLEAN_SEARCH = re.compile(r'(?i)\b(price of|what is|tell me about|look up|duckduckgo|bing|you.com)\b')
RE_CLEAN_SYMBOLS = re.compile(r'[#_*`~<>\[\]()@\n\r]')
RE_YEARS = re.compile(r'\b(19\d{2}|20\d{2})\b')
RE_SNIPPETS = re.compile(r'<td class=["\']result-snippet["\'][^>]*>(.*?)</td>', re.DOTALL)
RE_HTML_STRIP = re.compile(r'<.*?>')

# ============================================================
# 1. ENVIRONMENT CONFIGURATION & VALIDATOR CLASS (UPGRADED)
# ============================================================
@dataclass(frozen=True)
class BotConfig:
    """Immutable, type-safe configuration container validating environment settings on boot."""
    discord_token: str
    groq_api_key: str
    model_name: str
    state_path: str
    groq_max_tpd: int
    groq_max_tpm: int
    groq_max_rpd: int

    @classmethod
    def load_and_validate(cls) -> "BotConfig":
        def env_int(name: str, default: int, minimum: int = 1, maximum: int = 100_000_000) -> int:
            try:
                val = int(os.environ.get(name, str(default)))
                return max(minimum, min(maximum, val))
            except (TypeError, ValueError):
                logger.warning("Invalid integer for env var %s; defaulting to %d", name, default)
                return default

        d_token = os.environ.get("DISCORD_TOKEN", "").strip()
        g_key = os.environ.get("GROQ_API_KEY", "").strip()
        
        if not d_token or not g_key:
            logger.critical("Missing critical credentials: DISCORD_TOKEN or GROQ_API_KEY environment variables.")
            raise ValueError("Missing DISCORD_TOKEN or GROQ_API_KEY environment variables.")

        logger.info("[CONFIG] Environment configuration successfully validated and loaded.")
        return cls(
            discord_token=d_token,
            groq_api_key=g_key,
            model_name=os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b").strip(),
            state_path=os.environ.get("STATE_PATH", "/tmp/oatmeal_state.json").strip(),
            groq_max_tpd=env_int("GROQ_MAX_TPD", 200000, 1, 100_000_000),
            groq_max_tpm=env_int("GROQ_MAX_TPM", 8000, 1, 1_000_000),
            groq_max_rpd=env_int("GROQ_MAX_RPD", 1000, 1, 1_000_000),
        )

# Initialize validated global configuration container
CONFIG = BotConfig.load_and_validate()

# Module-level convenience aliases to prevent NameError across core subsystems
DISCORD_TOKEN = CONFIG.discord_token
GROQ_API_KEY = CONFIG.groq_api_key
STATE_PATH = CONFIG.state_path
MODEL_NAME = CONFIG.model_name

# Configure Discord Client Intents
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.messages = True
intents.members = True

bot = discord.Client(intents=intents)

TASKS_INITIALIZED = False
HTTP_SESSION: Optional[aiohttp.ClientSession] = None


# ============================================================
# 2. THREAD-SAFE BOUNDED MEMORY STRUCTURES & QUOTA GOVERNOR
# ============================================================
class BoundedSet:
    """
    Thread-safe bounded set with O(1) additions, deletions, 
    and strict capacity management via OrderedDict.
    """
    def __init__(self, maxsize: int = 30000):
        self.maxsize = maxsize
        self._map: collections.OrderedDict[Any, None] = collections.OrderedDict()
        self._lock = threading.Lock()

    def add(self, item: Any) -> None:
        with self._lock:
            if item in self._map:
                self._map.move_to_end(item)
                return
            if len(self._map) >= self.maxsize:
                self._map.popitem(last=False)
            self._map[item] = None

    def discard(self, item: Any) -> None:
        with self._lock:
            self._map.pop(item, None)

    def clear(self) -> None:
        with self._lock:
            self._map.clear()

    def __contains__(self, item: Any) -> bool:
        with self._lock:
            return item in self._map

    def __len__(self) -> int:
        with self._lock:
            return len(self._map)

    def snapshot(self) -> list:
        with self._lock:
            return list(self._map.keys())


# Core Bounded Memory Trackers
replied_message_ids = BoundedSet(maxsize=30000)
pending_message_ids = BoundedSet(maxsize=10000)
p2_replied_member_ids = BoundedSet(maxsize=10000)
p5_replied_member_ids = BoundedSet(maxsize=10000)

# Channel Cursors & State Dictionaries
channel_cursors: Dict[str, int] = {}
p4_cursors: Dict[str, int] = {}
p7_cursors: Dict[str, int] = {}
p8_cursors: Dict[str, int] = {}
p3_member_indices: Dict[str, int] = {}
last_member_activity_time: Dict[Any, float] = {}
recent_media_urls = collections.deque(maxlen=500)
blocked_users: Dict[str, float] = {}

# Rate Limiting & Quota Trackers
local_daily_reset_day: Optional[str] = datetime.now(timezone.utc).strftime("%Y-%m-%d")

GROQ_MAX_TPD = CONFIG.groq_max_tpd
GROQ_MAX_TPM = CONFIG.groq_max_tpm
GROQ_MAX_RPD = CONFIG.groq_max_rpd

GROQ_REMAINING_TPD = GROQ_MAX_TPD
GROQ_REMAINING_TPM = GROQ_MAX_TPM
GROQ_REMAINING_RPD = GROQ_MAX_RPD
GROQ_RATE_LIMITED_UNTIL = 0.0

def check_and_reset_daily_quotas():
    """Resets daily token trackers if the UTC calendar day has advanced."""
    global local_daily_reset_day, GROQ_REMAINING_TPD, GROQ_REMAINING_TPM, GROQ_REMAINING_RPD
    current_utc_day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    if local_daily_reset_day != current_utc_day:
        GROQ_REMAINING_TPD = GROQ_MAX_TPD
        GROQ_REMAINING_TPM = GROQ_MAX_TPM
        GROQ_REMAINING_RPD = GROQ_MAX_RPD
        local_daily_reset_day = current_utc_day
        
        if 'traffic_engine' in globals() and traffic_engine:
            with traffic_engine._lock:
                traffic_engine.local_daily_tokens = 0
                
        logger.info("[QUOTA] Daily token trackers reset for new UTC day: %s", current_utc_day)

groq_api_lock = asyncio.Lock()
state_io_lock = asyncio.Lock()

# ============================================================
# 3. DETERMINISTIC PRIORITY QUEUE & STATE PERSISTENCE STRUCTURES
# ============================================================
_job_sequence = itertools.count()

@dataclass(order=True)
class PendingJob:
    """Represents a scheduled job with deterministic priority ordering and sequence tie-breaking."""
    priority_tier: int
    timestamp: float
    sequence: int = field(default_factory=lambda: next(_job_sequence))
    channel: Any = field(compare=False, default=None)
    prompt_text: str = field(compare=False, default="")
    reference_message: Any = field(compare=False, default=None)
    author_id: Optional[int] = field(compare=False, default=None)

delay_queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
state_save_queue: asyncio.Queue = asyncio.Queue()

def p2_key(guild_id: int, author_id: int) -> str:
    """Generates a unique composite key for Priority 2 member tracking."""
    return f"{guild_id}:{author_id}"


# ============================================================
# 4. GOOGLE MAPS TRAFFIC ENGINE & TOKEN GOVERNOR (STREAMLINED)
# ============================================================
class GoogleMapsTrafficEngine:
    """
    Clean, predictable token allocator providing steady, fixed budgets 
    per priority tier without volatile dynamic throttling.
    """
    def __init__(self, daily_quota: int = 200000):
        self.daily_quota = max(1, daily_quota)
        self.local_daily_tokens = 0
        self._lock = threading.RLock()
        
        self.priority_hierarchy = {
            1: {"dsn": 200, "dsl": 250, "mode": "PRIMARY CHAT + MEDIA"},
            4: {"dsn": 150, "dsl": 200, "mode": "LIVE CHAT MONITORING"},
            6: {"dsn": 0,   "dsl": 0,   "mode": "TOKEN-FREE RANDOM MEDIA"},
            7: {"dsn": 0,   "dsl": 0,   "mode": "TOKEN-FREE MEDIA MIRROR"},
            8: {"dsn": 100, "dsl": 150, "mode": "MICRO-TOKEN REFLECTION"},
            9: {"dsn": 150, "dsl": 200, "mode": "MICRO-TOKEN RECAP"},
            2: {"dsn": 0,   "dsl": 0,   "mode": "TOKEN-FREE MEDIA"},
            5: {"dsn": 0,   "dsl": 0,   "mode": "TOKEN-FREE INVERSE MEDIA"},
            3: {"dsn": 0,   "dsl": 0,   "mode": "TOKEN-FREE GREETING"}
        }

    @property
    def spent_fraction(self) -> float:
        with self._lock:
            return max(0.0, min(1.0, self.local_daily_tokens / self.daily_quota))

    def get_daily_tokens(self) -> int:
        with self._lock:
            return self.local_daily_tokens

    def report_congestion(self, severity: float = 0.25):
        """No-op stub to prevent unwanted volume clipping."""
        pass

    def get_allocation(self, priority_tier: int) -> Tuple[int, int, str]:
        base = self.priority_hierarchy.get(priority_tier, {"dsn": 100, "dsl": 150, "mode": "DEFAULT ALLOCATION"})
        return base["dsn"], base["dsl"], base["mode"]

    def record_expenditure(self, tokens: int):
        global GROQ_REMAINING_TPD
        spent = max(0, int(tokens or 0))
        if spent <= 0:
            return
        with self._lock:
            self.local_daily_tokens += spent
            GROQ_REMAINING_TPD = max(0, GROQ_REMAINING_TPD - spent)

traffic_engine = GoogleMapsTrafficEngine(daily_quota=GROQ_MAX_TPD)

# ============================================================
# 5. ASYNCHRONOUS SINGLE-WRITER STATE PERSISTENCE (STREAMLINED)
# ============================================================
def trigger_save_state():
    """Non-blocking trigger to request a coalesced state save operation."""
    try:
        state_save_queue.put_nowait(True)
    except Exception:
        pass

def load_state():
    """
    Loads persistent state safely from the primary path or fallback backup,
    with integer key normalization.
    """
    global channel_cursors, p4_cursors, p7_cursors, p8_cursors, blocked_users, p3_member_indices
    state_path_str = str(STATE_PATH)
    backup_path_str = state_path_str + ".bak"
    
    target_path = None
    if os.path.exists(state_path_str):
        target_path = state_path_str
    elif os.path.exists(backup_path_str):
        target_path = backup_path_str
        
    if not target_path:
        logger.info("[STATE] No existing state file found at %s. Starting fresh.", state_path_str)
        return

    def _to_int_keys(d):
        normalized = {}
        for k, v in d.items():
            try:
                normalized[int(k)] = v
            except ValueError:
                normalized[k] = v
        return normalized

    try:
        with open(target_path, "r", encoding="utf-8") as f:
            container = json.load(f)
            
        loaded_data = container.get("payload", container) if isinstance(container, dict) else None
        if not loaded_data:
            return

        for item in loaded_data.get("replied", []):
            replied_message_ids.add(item)
        for item in loaded_data.get("p2_done_members", []):
            p2_replied_member_ids.add(item)
        for item in loaded_data.get("p5_done_members", []):
            p5_replied_member_ids.add(item)
            
        channel_cursors.update(_to_int_keys(loaded_data.get("cursors", {})))
        p4_cursors.update(_to_int_keys(loaded_data.get("p4_cursors", {})))
        p7_cursors.update(_to_int_keys(loaded_data.get("p7_cursors", {})))
        p8_cursors.update(_to_int_keys(loaded_data.get("p8_cursors", {})))
        blocked_users.update(_to_int_keys(loaded_data.get("blocked_users", {})))
        p3_member_indices.update(_to_int_keys(loaded_data.get("p3_member_indices", {})))
        
        logger.info("[STATE] Successfully loaded state from %s", target_path)
    except Exception as err:
        logger.error("[STATE ERROR] Failed to load state: %s", err)

async def state_writer_worker():
    """
    Single-writer background worker featuring debounced batching
    and atomic file replacement for safe state persistence.
    """
    state_path_str = str(STATE_PATH)
    backup_path_str = state_path_str + ".bak"
    
    while True:
        await state_save_queue.get()
        await asyncio.sleep(1.5)  # Debounce delay to coalesce rapid-fire updates
        drained_count = 1
        try:
            while True:
                state_save_queue.get_nowait()
                drained_count += 1
        except asyncio.QueueEmpty:
            pass

        try:
            async with state_io_lock:
                payload = {
                    "replied": replied_message_ids.snapshot()[-25000:],
                    "p2_done_members": p2_replied_member_ids.snapshot()[-8000:],
                    "p5_done_members": p5_replied_member_ids.snapshot()[-8000:],
                    "cursors": dict(channel_cursors),
                    "p4_cursors": dict(p4_cursors),
                    "p7_cursors": dict(p7_cursors),
                    "p8_cursors": dict(p8_cursors),
                    "blocked_users": dict(blocked_users),
                    "p3_member_indices": dict(p3_member_indices),
                }
                
                container = {
                    "version": 2,
                    "payload": payload
                }

                def _write():
                    temp_path = state_path_str + ".tmp"
                    try:
                        directory = os.path.dirname(state_path_str)
                        if directory:
                            os.makedirs(directory, exist_ok=True)
                            
                        with open(temp_path, "w", encoding="utf-8") as f:
                            json.dump(container, f, indent=2, ensure_ascii=False)
                            f.flush()
                            os.fsync(f.fileno())
                        
                        if os.path.exists(state_path_str):
                            if os.path.exists(backup_path_str):
                                os.remove(backup_path_str)
                            os.replace(state_path_str, backup_path_str)
                            
                        os.replace(temp_path, state_path_str)
                    except Exception as e:
                        try:
                            if os.path.exists(temp_path):
                                os.remove(temp_path)
                        except OSError:
                            pass
                        logger.error("[STATE] Atomic write failed: %s", e)

                await asyncio.to_thread(_write)

        except Exception as worker_err:
            logger.error("[STATE WORKER ERROR] %s", worker_err)
        finally:
            for _ in range(drained_count):
                try:
                    state_save_queue.task_done()
                except ValueError:
                    pass
# ============================================================
# 6. ASYNCHRONOUS TELEMETRY SERVER & METRICS DASHBOARD (STREAMLINED)
# ============================================================
_web_runner = None
_web_site = None
_bot_start_time = time.time()
_telemetry_loop = None
_telemetry_task = None
_web_thread = None

async def handle_health(request):
    """Lightweight health check endpoint for container lifecycle probes."""
    return web.Response(text="Oatmeal AI V102.0 Operational")

async def handle_telemetry(request):
    """Detailed telemetry metrics endpoint returning uptime, token quotas, queue stats, and cache health."""
    uptime_seconds = int(time.time() - _bot_start_time)
    hours, remainder = divmod(uptime_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    uptime_formatted = f"{hours}h {minutes}m {seconds}s"

    try:
        local_tokens = traffic_engine.get_daily_tokens() if 'traffic_engine' in globals() and traffic_engine else 0
        spent_frac = traffic_engine.spent_fraction if 'traffic_engine' in globals() and traffic_engine else 0.0
    except Exception:
        local_tokens, spent_frac = 0, 0.0

    try:
        rem_tpd = GROQ_REMAINING_TPD if 'GROQ_REMAINING_TPD' in globals() else 0
    except Exception:
        rem_tpd = 0

    try:
        d_q_size = delay_queue.qsize() if 'delay_queue' in globals() and delay_queue else 0
        s_q_size = state_save_queue.qsize() if 'state_save_queue' in globals() and state_save_queue else 0
        rep_tracked = len(replied_message_ids) if 'replied_message_ids' in globals() else 0
    except Exception:
        d_q_size, s_q_size, rep_tracked = 0, 0, 0

    data = {
        "status": "online",
        "uptime": uptime_formatted,
        "uptime_seconds": uptime_seconds,
        "token_metrics": {
            "local_daily_tokens": local_tokens,
            "groq_remaining_tpd": rem_tpd,
            "spent_fraction": spent_frac
        },
        "queues_and_memory": {
            "delay_queue_size": d_q_size,
            "state_save_queue_size": s_q_size,
            "replied_messages_tracked": rep_tracked
        }
    }
    return web.json_response(data)

async def _run_web_server():
    """Initializes and runs the aiohttp telemetry and health check web server."""
    global _web_runner, _web_site, _telemetry_loop, _telemetry_task
    _telemetry_loop = asyncio.get_running_loop()
    _telemetry_task = asyncio.current_task()
    
    app = web.Application()
    app.router.add_get("/telemetry", handle_telemetry)
    app.router.add_get("/", handle_health)
    
    port = int(os.environ.get("PORT", 10000))
    try:
        _web_runner = web.AppRunner(app)
        await _web_runner.setup()
        _web_site = web.TCPSite(_web_runner, "0.0.0.0", port)
        await _web_site.start()
        logger.info("[TELEMETRY] Async web server and metrics dashboard active on port %d", port)
        
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        logger.info("[TELEMETRY] Shutdown signal received. Cleaning up web server resources...")
    except Exception as e:
        logger.error("[TELEMETRY RUNTIME ERROR] %s", e)
    finally:
        try:
            if _web_site:
                await _web_site.stop()
            if _web_runner:
                await _web_runner.cleanup()
            logger.info("[TELEMETRY] Web server successfully stopped.")
        except Exception as cleanup_err:
            logger.error("[TELEMETRY CLEANUP ERROR] %s", cleanup_err)

def start_instant_web_server():
    """Start telemetry in one tracked daemon thread."""
    global _web_thread
    if _web_thread and _web_thread.is_alive():
        return
    def _thread_target():
        try:
            asyncio.run(_run_web_server())
        except Exception as e:
            logger.error("[TELEMETRY SERVER EXCEPTION] %s", e)
    _web_thread = threading.Thread(target=_thread_target, daemon=True, name="TelemetryServerThread")
    _web_thread.start()

def stop_telemetry_server():
    """Cancel telemetry server task and wait briefly for its thread to exit."""
    global _telemetry_loop, _telemetry_task, _web_thread
    loop, task = _telemetry_loop, _telemetry_task
    if loop and loop.is_running() and task and not task.done():
        try:
            loop.call_soon_threadsafe(task.cancel)
        except RuntimeError:
            pass
    if _web_thread and _web_thread.is_alive() and threading.current_thread() is not _web_thread:
        _web_thread.join(timeout=4.0)
    _telemetry_loop = None
    _telemetry_task = None

# ============================================================
# 7. RESILIENT FAST-PATH ROTATING BIMEDIA GENERATOR (OPTIMIZED)
# ============================================================
class BiMediaGenerator:
    """
    High-performance rotating media engine optimized for instant resolution
    without artificial throttling or backoff delays.
    """
    
    _media_lock = asyncio.Lock()
    _served_history: collections.deque = collections.deque(maxlen=300)

    @classmethod
    async def _try_youtube(cls, session: aiohttp.ClientSession, query: str) -> Tuple[str, Optional[discord.Embed]]:
        cleaned = re.sub(r'\s+', ' ', RE_CLEAN_SYMBOLS.sub(' ', RE_CLEAN_SEARCH.sub('', RE_CLEAN_MENTIONS.sub('', query)))).strip()
        q = urllib.parse.quote(cleaned[:100] or "90s retro commercial")
        url = f"https://pipedapi.kavin.rocks/search?q={q}&filter=videos"
        async with session.get(url, headers={"User-Agent": "OatmealBot/102.0"}, timeout=2) as resp:
            if resp.status == 200:
                data = await resp.json()
                urls = [f"https://www.youtube.com/watch?v={item['url'].split('=')[-1]}" for item in data.get("items", []) if "url" in item]
                if urls:
                    return cls._select_random_asset(urls), None
        raise ValueError("YouTube method returned no assets.")

    @classmethod
    async def _try_tenor(cls, session: aiohttp.ClientSession, query: str) -> Tuple[str, Optional[discord.Embed]]:
        cleaned = re.sub(r'\s+', ' ', RE_CLEAN_SYMBOLS.sub(' ', RE_CLEAN_SEARCH.sub('', RE_CLEAN_MENTIONS.sub('', query)))).strip()
        q = urllib.parse.quote(cleaned[:80] or "retro reaction")
        url = f"https://tenor.googleapis.com/v2/search?q={q}&key=LIVDSRZULELA&limit=20&media_filter=gif"
        async with session.get(url, timeout=2) as resp:
            if resp.status == 200:
                data = await resp.json()
                urls = [item["media_formats"]["gif"]["url"] for item in data.get("results", []) if "media_formats" in item and "gif" in item["media_formats"]]
                if urls:
                    emb = discord.Embed()
                    emb.set_image(url=cls._select_random_asset(urls))
                    return "", emb
        raise ValueError("Tenor GIF method returned no assets.")

    @classmethod
    async def _try_photos(cls, session: aiohttp.ClientSession, query: str) -> Tuple[str, Optional[discord.Embed]]:
        seed_val = urllib.parse.quote(query if query else str(random.randint(100, 999999)))
        photo_url = f"https://picsum.photos/seed/{seed_val}/640/360.jpg"
        emb = discord.Embed()
        emb.set_image(url=photo_url)
        return "", emb

    @classmethod
    async def _try_ai_art(cls, session: aiohttp.ClientSession, query: str) -> Tuple[str, Optional[discord.Embed]]:
        art_prompt = f"{query} 90s retro masterpiece" if query else "synthwave robot artwork"
        art_url = f"https://pollinations.ai/p/{urllib.parse.quote(art_prompt)}?width=640&height=360&seed={random.randint(1, 999999)}"
        emb = discord.Embed()
        emb.set_image(url=art_url)
        return "", emb

    @classmethod
    async def _try_wikipedia(cls, session: aiohttp.ClientSession, query: str) -> Tuple[str, Optional[discord.Embed]]:
        cleaned = RE_CLEAN_MENTIONS.sub('', query)
        query_str = urllib.parse.quote(re.sub(r'\s+', ' ', cleaned).strip()[:60] or "1990s")
        url = f"https://en.wikipedia.org/w/api.php?action=opensearch&search={query_str}&limit=10&namespace=0&format=json"
        async with session.get(url, headers={"User-Agent": "OatmealBot/102.0"}, timeout=2) as resp:
            if resp.status == 200:
                data = await resp.json()
                if isinstance(data, list) and len(data) > 3 and isinstance(data[3], list) and data[3]:
                    return cls._select_random_asset(data[3]), None
        raise ValueError("Wikipedia method returned no assets.")

    @classmethod
    async def _try_ascii(cls, session: aiohttp.ClientSession, query: str) -> Tuple[str, Optional[discord.Embed]]:
        ascii_templates = [
            "```\n  (o.o)  Oatmeal AI V102.0\n  / | \\\n _/\\_/\\_\n```",
            "```\n  /\\_/\\\n ( o.o )  Arnold Attic\n  > ^ <\n```",
            "```\n    ____         _                  _ \n  / __|___   _| |_ _ __  ___ _ _  | |\n | (__/ _ \\(_-<  _| '  \\/ -_) '_| |_|\n  \\___\\___/ /_/\\__|_|_|_\\___|_|     (_)\n```"
        ]
        return random.choice(ascii_templates), None

    @classmethod
    async def _try_emoticons(cls, session: aiohttp.ClientSession, query: str) -> Tuple[str, Optional[discord.Embed]]:
        return random.choice(["(ง'̀-'́)ง", "¯\\_(ツ)_/¯", "( ͡° ͜ʖ ͡°)", "(⌐■_■)", "(づ｡◕‿‿◕｡)づ", "ಠ_ಠ", "(ʘ__ʘ)"]), None

    @classmethod
    async def _try_stickers(cls, session: aiohttp.ClientSession, query: str) -> Tuple[str, Optional[discord.Embed]]:
        return random.choice(["⭐", "🥣", "🚀", "🕹️", "📼", "🔥", "💡", "🏆", "🎯"]), None

    @classmethod
    def _select_random_asset(cls, pool: List[str]) -> str:
        if not pool:
            return ""
        unseen = [item for item in pool if item not in cls._served_history]
        chosen = random.choice(unseen) if unseen else random.choice(pool)
        cls._served_history.append(chosen)
        return chosen

    @classmethod
    async def generate(cls, session: aiohttp.ClientSession, mode: str = "random", chat_text: str = "") -> Tuple[str, Optional[discord.Embed]]:
        """
        Executes media generation methods sequentially with instant failover
        and zero artificial delay padding.
        """
        async with cls._media_lock:
            methods = [
                cls._try_youtube,
                cls._try_tenor,
                cls._try_photos,
                cls._try_ai_art,
                cls._try_wikipedia,
                cls._try_ascii,
                cls._try_emoticons,
                cls._try_stickers
            ]
            
            start_idx = random.randint(0, len(methods) - 1)
            ordered_methods = methods[start_idx:] + methods[:start_idx]

            for method in ordered_methods:
                try:
                    url, emb = await method(session, chat_text)
                    if url or emb is not None:
                        return url, emb
                except Exception:
                    continue  # Instantly try the next method without sleeping

            return "https://www.youtube.com/watch?v=dQw4w9WgXcQ", None

# ============================================================
# 8. THREAD-SAFE ASYNC SEARCH CACHE & INTENT PARSER (STREAMLINED)
# ============================================================
class AsyncSearchCache:
    """
    Thread-safe async search cache with clean in-flight request handling
    for web search queries.
    """
    def __init__(self, ttl: int = 3600, maxsize: int = 500):
        self.ttl = ttl
        self.maxsize = maxsize
        self._cache: Dict[str, Tuple[float, str]] = {}
        self._inflight: Dict[str, asyncio.Lock] = {}
        self._lock = asyncio.Lock()

    async def get_or_fetch(self, query: str, session: aiohttp.ClientSession) -> str:
        q_lower = query.lower().strip()
        now = time.time()

        async with self._lock:
            if q_lower in self._cache:
                timestamp, result = self._cache[q_lower]
                if now - timestamp < self.ttl:
                    return result
                else:
                    del self._cache[q_lower]

            if q_lower not in self._inflight:
                self._inflight[q_lower] = asyncio.Lock()
            inflight_lock = self._inflight[q_lower]

        async with inflight_lock:
            async with self._lock:
                if q_lower in self._cache:
                    timestamp, result = self._cache[q_lower]
                    if now - timestamp < self.ttl:
                        return result

            result_text = await self._perform_search(q_lower, session)

            async with self._lock:
                if len(self._cache) >= self.maxsize:
                    oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k][0])
                    del self._cache[oldest_key]
                self._cache[q_lower] = (time.time(), result_text)
                self._inflight.pop(q_lower, None)

            return result_text

    async def _perform_search(self, query: str, session: aiohttp.ClientSession) -> str:
        encoded = urllib.parse.quote(query)
        url = f"https://html.duckduckgo.com/html/?q={encoded}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) OatmealBot/102.0"}
        try:
            async with session.get(url, headers=headers, timeout=5) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    snippets = RE_SNIPPETS.findall(html)
                    cleaned_snippets = [RE_HTML_STRIP.sub('', s).strip() for s in snippets if s.strip()]
                    if cleaned_snippets:
                        return "\n".join(cleaned_snippets[:3])
        except Exception as e:
            logger.debug("[SEARCH CACHE] Fetch error for '%s': %s", query, e)
        return f"No verified archive records found for '{query}'."

search_cache = AsyncSearchCache()

def parse_intent_and_keyword(prompt: str) -> Tuple[str, str]:
    """
    Direct intent parser routing explicit commands cleanly 
    while keeping normal conversation flowing.
    """
    lower = prompt.lower().strip()
    if lower.startswith(("remember ", "recall ", "what did you say about ")):
        return "remember", prompt
    if lower.startswith(("search ", "lookup ", "price of ")):
        clean_kw = RE_CLEAN_SEARCH.sub('', prompt).strip()
        return "search", (clean_kw or prompt)
    return "chat", prompt
# ============================================================
# 9. PURE PAYLOAD BUILDER (STREAMLINED)
# ============================================================
async def build_payload(content_str: str, media_url: str = "", embed: Optional[discord.Embed] = None) -> Tuple[str, Dict[str, Any]]:
    """
    Formats text content, direct media URLs, and rich embeds for Discord dispatch cleanly.
    """
    kwargs: Dict[str, Any] = {}
    final_content = content_str.strip() if content_str else ""
    sanitized_media = media_url.strip() if media_url else ""

    if sanitized_media:
        if final_content:
            final_content += f"\n{sanitized_media}"
        else:
            final_content = sanitized_media

    if embed is not None:
        kwargs["embed"] = embed

    return final_content, kwargs

# ============================================================
# 10. UNTRUNCATED BIOGRAPHICAL TIMELINE & KERNEL
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
# 10.5 ASYNCHRONOUS GROQ LLM API CLIENT (STREAMLINED)
# ============================================================
async def query_groq_llm(session: aiohttp.ClientSession, prompt: str, max_tokens: int = 50) -> str:
    """
    Asynchronous Groq LLM query function with thread-safe locking, 
    clean 429 rate-limit handling, and direct token accounting.
    """
    global GROQ_RATE_LIMITED_UNTIL, GROQ_REMAINING_TPD, GROQ_REMAINING_TPM
    
    if not GROQ_API_KEY or max_tokens <= 0:
        return ""

    if time.monotonic() < GROQ_RATE_LIMITED_UNTIL:
        return ""

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
        "User-Agent": "OatmealAI/102.0"
    }
    
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {
                "role": "system",
                "content": SHORT_IDENTITY + "\n\n" + PERSONALITY_KERNEL + "\n\n" + VOICE_KERNEL + "\n\n" + SOVEREIGN
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        "max_tokens": max_tokens,
        "temperature": 0.7
    }

    async with groq_api_lock:
        try:
            async with session.post(url, headers=headers, json=payload, timeout=10) as resp:
                if resp.status == 429:
                    retry_after = float(resp.headers.get("Retry-After", 5.0))
                    GROQ_RATE_LIMITED_UNTIL = time.monotonic() + retry_after
                    logger.warning("[GROQ API] Rate limited (HTTP 429). Pausing requests for %.1fs", retry_after)
                    return ""
                
                if resp.status != 200:
                    err_text = await resp.text()
                    logger.error("[GROQ API ERROR] Status %d: %s", resp.status, err_text[:200])
                    return ""

                data = await resp.json()
                choices = data.get("choices", [])
                if choices:
                    message = choices[0].get("message", {})
                    content = message.get("content", "").strip()
                    
                    usage = data.get("usage", {})
                    total_tokens = usage.get("total_tokens", max_tokens)
                    if 'traffic_engine' in globals() and traffic_engine:
                        traffic_engine.record_expenditure(total_tokens)
                        
                    return content
        except Exception as e:
            logger.error("[GROQ API EXCEPTION] %s", e)
                
        return ""
# ============================================================
# 11. STREAMLINED TIMELINE & REMEMBER TEMPLATE ENGINE
# ============================================================
def extract_relevant_timeline(prompt: str) -> str:
    """Extracts relevant biographical timeline lines matching mentioned years or returns the full outline."""
    if not prompt:
        return SHORT_IDENTITY + "\n\n" + FULL_ANNUAL_OUTLINE
    years = set(RE_YEARS.findall(prompt))
    if not years:
        return SHORT_IDENTITY + "\n\n" + FULL_ANNUAL_OUTLINE
    lines = [ln for ln in FULL_ANNUAL_OUTLINE.strip().splitlines() if any(ln.startswith(f"{y}:") for y in years)]
    return "Relevant timeline:\n" + "\n".join(lines) if lines else SHORT_IDENTITY + "\n\n" + FULL_ANNUAL_OUTLINE


async def get_remember_template_reply(channel: Any, prompt_text: str, sender: Any, session: aiohttp.ClientSession) -> Tuple[str, str, Optional[discord.Embed]]:
    """
    Phrase-Based Remember Template Engine:
    Performs clean async lookups across Discord history, biographical timeline outlines,
    and internet search caches.
    """
    if not prompt_text:
        return "", "", None

    cleaned_phrase = re.sub(
        r'^(please\s+)?(can you\s+)?(do you\s+)?(remember|recall|tell me about|what did you say about|what did|what was)\s*', 
        '', 
        prompt_text.strip(), 
        flags=re.IGNORECASE
    )
    search_phrase = cleaned_phrase.strip("?.,!:") if cleaned_phrase else prompt_text.strip("?.,!:")
    if not search_phrase or len(search_phrase) < 2:
        search_phrase = "oatmeal"

    sender_name = sender.display_name if sender and hasattr(sender, "display_name") else "User"

    async def fetch_history():
        matched_msg = None
        try:
            phrase_lower = search_phrase.lower()
            async for msg in channel.history(oldest_first=True, limit=300):
                if not msg.author.bot and msg.content and phrase_lower in msg.content.lower():
                    matched_msg = msg
                    break
        except Exception as hist_err:
            logger.error("[REMEMBER HISTORY ERROR] %s", hist_err)
        return matched_msg

    async def fetch_internet_quote():
        try:
            search_res = await search_cache.get_or_fetch(f"{search_phrase} history quote", session)
            if search_res:
                lines = [ln.strip("- ").strip() for ln in search_res.splitlines() if len(ln) > 15]
                if lines:
                    q = lines[0][:120]
                    yrs = RE_YEARS.findall(q)
                    return q, (yrs[0] if yrs else "2020")
        except Exception as net_err:
            logger.error("[REMEMBER SEARCH ERROR] %s", net_err)
        return f"The archive record concerning '{search_phrase}' remains part of history.", "1993"

    async def fetch_media():
        try:
            return await BiMediaGenerator.generate(session, "custom", search_phrase)
        except Exception as media_err:
            logger.error("[REMEMBER MEDIA ERROR] %s", media_err)
            return "", None

    matched_msg, (internet_quote, internet_year), (media_url, embed) = await asyncio.gather(
        fetch_history(),
        fetch_internet_quote(),
        fetch_media()
    )

    timeline_line = f"1985: Record for '{search_phrase}'."
    timeline_year = "1985"
    for line in FULL_ANNUAL_OUTLINE.strip().splitlines():
        if search_phrase.lower() in line.lower():
            timeline_line = line.strip()
            years_in_line = RE_YEARS.findall(line)
            if years_in_line:
                timeline_year = years_in_line[0]
                break

    if matched_msg:
        op_name = matched_msg.author.display_name
        discord_year = matched_msg.created_at.year
        content = matched_msg.content.strip()
        sentences = re.split(r'(?<=[.!?])\s+', content)
        first_sentence = (sentences[0] if sentences else content).strip('"\'')
        
        template_reply = (
            f"@{sender_name}, in {discord_year}: @{op_name} said: \"{first_sentence}\"\n"
            f"Timeline record [{timeline_year}]: {timeline_line}\n"
            f"Internet archive [{internet_year}]: \"{internet_quote}\""
        )
    else:
        template_reply = (
            f"@{sender_name}, searching archives for '{search_phrase}':\n"
            f"Timeline record [{timeline_year}]: {timeline_line}\n"
            f"Internet archive [{internet_year}]: \"{internet_quote}\""
        )

    return template_reply, media_url, embed


# ============================================================
# 11X. HELPER: SINGLE TIMELINE LINE EXTRACTOR
# ============================================================
def extract_single_timeline_line(keyword: str) -> str:
    """Extracts a single matching line from the annual outline or returns identity summary."""
    if not keyword:
        return SHORT_IDENTITY
    for line in FULL_ANNUAL_OUTLINE.strip().splitlines():
        if keyword.lower() in line.lower():
            return line.strip()
    return f"1985: Record for '{keyword}' in the Arnold Attic archive."


# ============================================================
# 11.5 WORD MACHINE & PIPELINE
# ============================================================
class WordMachine:
    """
    Keyword extraction engine filtering out stop words
    and surfacing high-signal tokens to target media generation.
    """
    STOP_WORDS = {
        "the", "a", "an", "in", "on", "at", "to", "for", "is", "are", 
        "about", "you", "any", "some", "me", "it", "this", "when", 
        "what", "where", "who", "how", "did", "do", "was", "we", "that",
        "remember", "think", "tell", "show", "find", "search", "lookup",
        "and", "or", "but", "if", "so", "with", "from", "by", "as", "of"
    }

    @classmethod
    def flushed_keywords(cls, text: str, max_words: int = 4) -> str:
        if not text:
            return "retro archive"
        words = RE_WORD_TOKENS.findall(text)
        filtered = [w for w in words if len(w) > 2 and w.lower() not in cls.STOP_WORDS]
        if not filtered:
            filtered = [w for w in words if len(w) > 1]
        if not filtered:
            return "retro archive"
        selected = filtered[:max_words]
        return " ".join(selected)

# ============================================================
# 12. CENTRALIZED JOB DISPATCH & ASSEMBLY LINE PIPELINE (STREAMLINED)
# ============================================================
async def process_job(job: PendingJob, session: aiohttp.ClientSession):
    """
    Centralized assembly line processing jobs from the delay queue:
    - Resolves intent (remember, search, chat).
    - Executes concurrent LLM token generation and media rotation.
    - Strictly enforces text-free, pure media execution for Priority 2.
    - Handles Discord 404/10008 reference message fallbacks cleanly.
    """
    global GROQ_REMAINING_TPD
    check_and_reset_daily_quotas()

    channel = job.channel
    if channel is None:
        return

    prompt = job.prompt_text or "Hello Oatmeal."
    tier = job.priority_tier
    ref_msg = job.reference_message

    try:
        # 1. Intent Parsing & Subsystem Routing
        intent, keyword = parse_intent_and_keyword(prompt)
        
        reply_text = ""
        media_url = ""
        embed: Optional[discord.Embed] = None

        if intent == "remember":
            reply_text, media_url, embed = await get_remember_template_reply(channel, prompt, ref_msg.author if ref_msg else None, session)
        elif intent == "search":
            search_results = await search_cache.get_or_fetch(keyword, session)
            reply_text = f"Search results for '{keyword}':\n{search_results}" if search_results else f"No live search results found for '{keyword}'."
            media_url, embed = await BiMediaGenerator.generate(session, "custom", keyword)
        else:
            # Standard LLM + Media concurrent pipeline for interactive tiers
            dsn, dsl, mode = traffic_engine.get_allocation(tier)
            
            # Ensure interactive tiers (Priority 1) always have a healthy token minimum
            if tier == 1 and dsn < 150:
                dsn = 200
                dsl = 250

            media_query = WordMachine.flushed_keywords(prompt, max_words=4)

            # Concurrent LLM generation and media rotation
            llm_task = asyncio.create_task(
                query_groq_llm(session, prompt, max_tokens=dsn)
            ) if dsn > 0 else asyncio.sleep(0)

            media_task = asyncio.create_task(
                BiMediaGenerator.generate(session, "random", media_query)
            )

            results = await asyncio.gather(llm_task, media_task, return_exceptions=True)
            
            if not isinstance(results[0], Exception) and dsn > 0:
                reply_text = results[0] or ""
            
            # Fallback voice response if Groq returns empty for Priority 1
            if tier == 1 and not reply_text:
                reply_text = "Back in Rose Grove trailer 163, we didn't miss a beat. Let's talk about that again!"

            if not isinstance(results[1], Exception):
                media_url, embed = results[1]

        # Priority 2 Strict Purge: Ensure zero text and clean embed formatting
        if tier == 2:
            reply_text = ""
            if media_url and not embed:
                embed = discord.Embed()
                embed.set_image(url=media_url)
                media_url = ""

        # 2. Build Clean Payload
        content_payload, kwargs = await build_payload(reply_text, media_url, embed)

        # 3. Dispatch to Discord Channel with 404/10008 Reference Handling
        if ref_msg:
            try:
                await ref_msg.reply(content_payload, **kwargs, mention_author=False)
            except discord.HTTPException as he:
                if he.code in (10008, 404):  # Unknown Message or Not Found
                    logger.warning("[DISPATCH] Reference message %s expired/deleted. Falling back to channel send.", getattr(ref_msg, 'id', 'unknown'))
                    await channel.send(content_payload, **kwargs)
                else:
                    raise
        else:
            await channel.send(content_payload, **kwargs)

        # Track successfully replied message IDs
        if ref_msg and hasattr(ref_msg, 'id'):
            replied_message_ids.add(ref_msg.id)
            trigger_save_state()

    except Exception as e:
        logger.error("[PIPELINE ERROR] Tier %d job execution failed: %s", tier, e)
    finally:
        if ref_msg and hasattr(ref_msg, 'id'):
            pending_message_ids.discard(ref_msg.id)
# ============================================================
# 13. UNIFIED BACKGROUND PRIORITY SCHEDULER (STREAMLINED)
# ============================================================
async def background_priority_scheduler():
    """
    Unified background orchestrator managing priorities P2 through P9,
    pushing lightweight jobs onto the delay queue for sequential execution.
    """
    await bot.wait_until_ready()
    
    last_runs = {
        2: time.monotonic(), 3: time.monotonic(), 4: time.monotonic(), 
        5: time.monotonic(), 6: time.monotonic(), 7: time.monotonic(), 
        8: time.monotonic(), 9: time.monotonic()
    }
    
    intervals = {
        2: random.uniform(900, 1800), 
        3: random.uniform(1400, 3000), 
        4: random.uniform(20, 45), 
        5: random.uniform(900, 1800), 
        6: random.uniform(800, 2000), 
        7: random.uniform(45, 90), 
        8: random.uniform(120, 300), 
        9: random.uniform(1800, 3600)
    }

    while not bot.is_closed():
        await asyncio.sleep(10.0)
        now = time.monotonic()

        # --------------------------------------------------------
        # PRIORITY 4: Precision Live Chat Monitoring
        # --------------------------------------------------------
        if now - last_runs[4] >= intervals[4]:
            last_runs[4] = now
            intervals[4] = random.uniform(20, 45)
            try:
                for guild in list(bot.guilds):
                    channels = [c for c in guild.text_channels if guild.me and c.permissions_for(guild.me).send_messages]
                    for ch in channels:
                        ch_id_str = str(ch.id)
                        cursor = p4_cursors.get(ch_id_str, 0)
                        
                        kwargs = {"limit": 10, "oldest_first": True}
                        if cursor > 0:
                            kwargs["after"] = discord.Object(id=cursor)
                        else:
                            latest_msgs = [m async for m in ch.history(limit=1)]
                            if latest_msgs:
                                p4_cursors[ch_id_str] = latest_msgs[0].id
                                trigger_save_state()
                            continue
                            
                        async for msg in ch.history(**kwargs):
                            mentioned = bot.user in msg.mentions if guild.me else False
                            named = any(nick in msg.content.lower() for nick in ("oatmeal", "joey", "arnold"))
                            replied_to_bot = False
                            if msg.reference:
                                if msg.reference.resolved and getattr(msg.reference.resolved, 'author', None) == bot.user:
                                    replied_to_bot = True
                                elif msg.reference.cached_message and msg.reference.cached_message.author == bot.user:
                                    replied_to_bot = True

                            intent, keyword = parse_intent_and_keyword(msg.content)
                            has_explicit_intent = intent in ("remember", "think", "search")

                            is_eligible = (
                                not msg.author.bot 
                                and bool(msg.content)
                                and msg.id not in replied_message_ids 
                                and msg.id not in pending_message_ids
                                and (mentioned or named or replied_to_bot or has_explicit_intent)
                            )
                            
                            if is_eligible:
                                pending_message_ids.add(msg.id)
                                clean_content = RE_CLEAN_MENTIONS.sub('', msg.content).strip()
                                if not clean_content:
                                    clean_content = msg.content.strip()

                                prompt_payload = clean_content if intent in ("remember", "think") else f"Brief take: '{clean_content[:80]}'"

                                job = PendingJob(
                                    priority_tier=4,
                                    timestamp=time.time(),
                                    channel=ch,
                                    prompt_text=prompt_payload,
                                    reference_message=msg,
                                    author_id=msg.author.id
                                )
                                try:
                                    await delay_queue.put(job)
                                except Exception:
                                    pending_message_ids.discard(msg.id)
                                    raise
                                break
                            else:
                                p4_cursors[ch_id_str] = msg.id
                                trigger_save_state()
                        break
            except Exception as e:
                logger.error("[P4 ERROR] %s", e)

        # --------------------------------------------------------
        # PRIORITY 7: Bot Media Mirror
        # --------------------------------------------------------
        if now - last_runs[7] >= intervals[7]:
            last_runs[7] = now
            intervals[7] = random.uniform(45, 90)
            try:
                for guild in list(bot.guilds):
                    channels = [c for c in guild.text_channels if guild.me and c.permissions_for(guild.me).send_messages]
                    for ch in channels:
                        ch_id_str = str(ch.id)
                        cursor = p7_cursors.get(ch_id_str, 0)
                        
                        kwargs = {"limit": 30, "oldest_first": True}
                        if cursor > 0:
                            kwargs["after"] = discord.Object(id=cursor)

                        target_msg = None
                        async for msg in ch.history(**kwargs):
                            if msg.author == bot.user:
                                target_msg = msg
                                break
                        
                        if target_msg is None:
                            continue
                            
                        metadata_parts = [target_msg.content or ""]
                        for embed in target_msg.embeds:
                            if embed.title:
                                metadata_parts.append(embed.title)
                            if embed.description:
                                metadata_parts.append(embed.description)
                            if embed.image and embed.image.url:
                                parsed_path = urllib.parse.urlparse(embed.image.url)
                                metadata_parts.append(os.path.basename(parsed_path.path))
                        
                        raw_metadata = " ".join(metadata_parts)
                        extracted_keywords = WordMachine.flushed_keywords(raw_metadata, max_words=4)
                        if not extracted_keywords:
                            extracted_keywords = "retro archive mirror"

                        job = PendingJob(
                            priority_tier=7,
                            timestamp=time.time(),
                            channel=ch,
                            prompt_text=extracted_keywords,
                            reference_message=target_msg,
                            author_id=None
                        )
                        
                        await delay_queue.put(job)
                        p7_cursors[ch_id_str] = target_msg.id
                        trigger_save_state()
                        break
            except Exception as e:
                logger.error("[P7 ERROR] %s", e)

        # --------------------------------------------------------
        # PRIORITY 8: Historical Reflections
        # --------------------------------------------------------
        if now - last_runs[8] >= intervals[8]:
            last_runs[8] = now
            intervals[8] = random.uniform(120, 300)
            try:
                for guild in list(bot.guilds):
                    channels = [c for c in guild.text_channels if guild.me and c.permissions_for(guild.me).send_messages]
                    for ch in channels:
                        ch_id_str = str(ch.id)
                        cursor = p8_cursors.get(ch_id_str, 0)
                        kwargs = {"limit": 30, "oldest_first": True}
                        if cursor > 0:
                            kwargs["after"] = discord.Object(id=cursor)
                        
                        target = None
                        async for msg in ch.history(**kwargs):
                            if msg.author == bot.user and msg.content:
                                target = msg
                                break
                                
                        if target is None:
                            continue
                        
                        job = PendingJob(
                            priority_tier=8,
                            timestamp=time.time(),
                            channel=ch,
                            prompt_text=target.content,
                            reference_message=target
                        )
                        await delay_queue.put(job)
                        p8_cursors[ch_id_str] = target.id
                        trigger_save_state()
                        break
            except Exception as e:
                logger.error("[P8 ERROR] %s", e)

        # --------------------------------------------------------
        # PRIORITY 2: Periodic Member Media
        # --------------------------------------------------------
        if now - last_runs[2] >= intervals[2]:
            last_runs[2] = now
            intervals[2] = random.uniform(900, 1800)
            try:
                for guild in list(bot.guilds):
                    channels = [c for c in guild.text_channels if guild.me and c.permissions_for(guild.me).send_messages]
                    if channels:
                        ch = random.choice(channels)
                        async for msg in ch.history(limit=25):
                            if not msg.author.bot and p2_key(guild.id, msg.author.id) not in p2_replied_member_ids and msg.content:
                                job = PendingJob(
                                    priority_tier=2,
                                    timestamp=time.time(),
                                    channel=ch,
                                    prompt_text=WordMachine.flushed_keywords(msg.content, max_words=4),
                                    reference_message=msg,
                                    author_id=msg.author.id
                                )
                                await delay_queue.put(job)
                                p2_replied_member_ids.add(p2_key(guild.id, msg.author.id))
                                trigger_save_state()
                                break
                        break
            except Exception as e:
                logger.error("[P2 ERROR] %s", e)

        # --------------------------------------------------------
        # PRIORITY 3: Randomized Member Media & Mention
        # --------------------------------------------------------
        if now - last_runs[3] >= intervals[3]:
            last_runs[3] = now
            intervals[3] = random.uniform(1400, 3000)
            try:
                for guild in list(bot.guilds):
                    members = [m for m in guild.members if not m.bot]
                    if not members and not guild.chunked:
                        try:
                            async for m in guild.fetch_members(limit=50):
                                if not m.bot:
                                    members.append(m)
                        except Exception:
                            pass
                    
                    if not members:
                        continue
                        
                    target = random.choice(members)
                    channels = [c for c in guild.text_channels if guild.me and c.permissions_for(guild.me).send_messages]
                    if channels:
                        ch = random.choice(channels)
                        job = PendingJob(
                            priority_tier=3,
                            timestamp=time.time(),
                            channel=ch,
                            prompt_text=f"{target.name} {target.display_name}",
                            author_id=target.id
                        )
                        await delay_queue.put(job)
                        trigger_save_state()
                    break
            except Exception as e:
                logger.error("[P3 ERROR] %s", e)

        # --------------------------------------------------------
        # PRIORITY 5: Inverse Member Media
        # --------------------------------------------------------
        if now - last_runs[5] >= intervals[5]:
            last_runs[5] = now
            intervals[5] = random.uniform(900, 1800)
            try:
                for guild in list(bot.guilds):
                    channels = [c for c in guild.text_channels if guild.me and c.permissions_for(guild.me).send_messages]
                    if channels:
                        ch = random.choice(channels)
                        async for msg in ch.history(limit=30, oldest_first=True):
                            if not msg.author.bot:
                                member_key = p2_key(guild.id, msg.author.id)
                                if member_key not in p5_replied_member_ids:
                                    job = PendingJob(
                                        priority_tier=5,
                                        timestamp=time.time(),
                                        channel=ch,
                                        prompt_text=WordMachine.flushed_keywords(msg.content or "", max_words=4),
                                        reference_message=msg,
                                        author_id=msg.author.id
                                    )
                                    await delay_queue.put(job)
                                    p5_replied_member_ids.add(member_key)
                                    trigger_save_state()
                                    break
                        break
            except Exception as e:
                logger.error("[P5 ERROR] %s", e)

        # --------------------------------------------------------
        # PRIORITY 6: Token-Free Word-Driven Media
        # --------------------------------------------------------
        if now - last_runs[6] >= intervals[6]:
            last_runs[6] = now
            intervals[6] = random.uniform(800, 2000)
            try:
                for guild in list(bot.guilds):
                    channels = [
                        c for c in guild.text_channels 
                        if guild.me 
                        and c.permissions_for(guild.me).send_messages 
                        and c.permissions_for(guild.me).read_message_history
                    ]
                    if channels:
                        ch = random.choice(channels)
                        collected_words = []
                        
                        async for msg in ch.history(limit=100):
                            if not msg.author.bot and msg.content:
                                words = RE_WORD_TOKENS.findall(msg.content)
                                for w in words:
                                    if len(w) > 2 and w.lower() not in WordMachine.STOP_WORDS:
                                        collected_words.append(w)
                        
                        selected_query = ""
                        if collected_words:
                            sample_size = min(5, len(collected_words))
                            chosen_words = random.sample(collected_words, sample_size)
                            selected_query = " ".join(chosen_words)
                        else:
                            selected_query = "retro archive"

                        job = PendingJob(
                            priority_tier=6,
                            timestamp=time.time(),
                            channel=ch,
                            prompt_text=selected_query
                        )
                        await delay_queue.put(job)
                        break
            except Exception as e:
                logger.error("[P6 ERROR] %s", e)

        # --------------------------------------------------------
        # PRIORITY 9: Deep Historical Recaps
        # --------------------------------------------------------
        if now - last_runs[9] >= intervals[9]:
            last_runs[9] = now
            intervals[9] = random.uniform(1800, 3600)
            try:
                for guild in list(bot.guilds):
                    channels = [
                        c for c in guild.text_channels 
                        if guild.me 
                        and c.permissions_for(guild.me).send_messages 
                        and c.permissions_for(guild.me).read_message_history
                    ]
                    if channels:
                        ch = random.choice(channels)
                        historical_lines = []
                        last_msg = None
                        
                        async for msg in ch.history(limit=50, oldest_first=True):
                            if not msg.author.bot and msg.content:
                                historical_lines.append(f"[{msg.created_at.strftime('%Y-%m-%d')}] {msg.author.display_name}: {msg.content[:40]}")
                                last_msg = msg
                                
                        if historical_lines and last_msg:
                            sample_context = "\n".join(historical_lines[-15:])
                            job = PendingJob(
                                priority_tier=9,
                                timestamp=time.time(),
                                channel=ch,
                                prompt_text=sample_context,
                                reference_message=last_msg
                            )
                            await delay_queue.put(job)
                            break
            except Exception as e:
                logger.error("[P9 ERROR] %s", e)
# ============================================================
# 14. WORKERS & BOT EVENTS (STREAMLINED)
# ============================================================
async def delay_queue_worker():
    """
    Asynchronous worker that pulls and processes jobs from the delay queue,
    routing them securely through the centralized pipeline without stalls.
    """
    global HTTP_SESSION
    while True:
        job = None
        try:
            job = await delay_queue.get()
            
            # Execute through the job processor and assembly line
            await process_job(job, HTTP_SESSION)
            
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("[WORKER ERROR] Unhandled exception in delay_queue_worker: %s", e)
            if job is not None and job.reference_message:
                pending_message_ids.discard(getattr(job.reference_message, "id", None))
            await asyncio.sleep(1.0)
        finally:
            if job is not None:
                try:
                    delay_queue.task_done()
                except ValueError:
                    pass

# Keep track of active background tasks so they don't duplicate on reconnect
_background_tasks: List[asyncio.Task] = []

@bot.event
async def on_ready():
    """Event triggered when the bot successfully connects to the Discord gateway."""
    global TASKS_INITIALIZED, HTTP_SESSION, _background_tasks
    if HTTP_SESSION is None or HTTP_SESSION.closed:
        HTTP_SESSION = aiohttp.ClientSession()

    if TASKS_INITIALIZED:
        logger.info("[SYSTEM] Reconnected to Discord Gateway. Existing background workers active.")
        return

    TASKS_INITIALIZED = True
    logger.info("=" * 64)
    logger.info(" Oatmeal AI V102.0 Master Production Edition logged in as %s", bot.user)
    logger.info("=" * 64)
    load_state()

    # Initialize the unified background scheduler, state persistence worker, and queue worker cleanly
    _background_tasks = [
        asyncio.create_task(state_writer_worker()),
        asyncio.create_task(delay_queue_worker()),
        asyncio.create_task(background_priority_scheduler())
    ]


@bot.event
async def on_message(message):
    """Event triggered on incoming channel messages, routing direct interactions to Priority 1 via the pipeline."""
    if message.author.bot or message.guild is None or message.is_system() or message.author == bot.user:
        return

    now = time.time()
    author_id = message.author.id
    last_member_activity_time[message.channel.id] = now

    clean = RE_CLEAN_MENTIONS.sub("", message.content).strip()
    mentioned = bot.user in message.mentions
    
    replied_to_bot = False
    if message.reference:
        if message.reference.resolved and getattr(message.reference.resolved, 'author', None) == bot.user:
            replied_to_bot = True
        elif message.reference.cached_message and message.reference.cached_message.author == bot.user:
            replied_to_bot = True

    named = any(nick in message.content.lower() for nick in ("oatmeal", "joey", "arnold"))

    if mentioned or replied_to_bot or named:
        if message.id not in replied_message_ids and message.id not in pending_message_ids:
            pending_message_ids.add(message.id)
            job = PendingJob(
                priority_tier=1,
                timestamp=now,
                channel=message.channel,
                prompt_text=clean or "Hello Oatmeal.",
                reference_message=message,
                author_id=author_id
            )
            try:
                await delay_queue.put(job)
            except Exception:
                pending_message_ids.discard(message.id)
                raise


async def close_bot_resources():
    """Graceful cleanup handler for background workers and HTTP sessions upon shutdown."""
    global HTTP_SESSION, _background_tasks
    for task in _background_tasks:
        task.cancel()
    await asyncio.gather(*_background_tasks, return_exceptions=True)
    if HTTP_SESSION and not HTTP_SESSION.closed:
        await HTTP_SESSION.close()
    logger.info("[SYSTEM] Cleaned up HTTP session and background workers.")


async def main():
    """Run the Discord client with explicit async resource cleanup."""
    start_instant_web_server()
    try:
        async with bot:
            await bot.start(DISCORD_TOKEN)
    finally:
        try:
            await close_bot_resources()
        finally:
            stop_telemetry_server()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("[SYSTEM] Shutdown requested by operator.")
