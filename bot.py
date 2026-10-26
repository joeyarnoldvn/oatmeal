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
# 0. PRE-COMPILED REGEX CONSTANTS (Upgraded for Intent Preservation)
# ============================================================
RE_TENOR_ID = re.compile(r'/view/[^"]+-(\d+)')
RE_WORD_TOKENS = re.compile(r'\b\w+\b')
RE_CLEAN_MENTIONS = re.compile(r'<@!?\d+>')
# Refined to prevent over-stripping search keywords when explicit intent is active
RE_CLEAN_SEARCH = re.compile(r'(?i)\b(price of|what is|tell me about|look up|duckduckgo|bing|you.com)\b')
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

MODEL_NAME = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b").strip()
STATE_PATH = os.environ.get("STATE_PATH", "/tmp/oatmeal_state.json")

def env_int(name: str, default: int, minimum: int = 1, maximum: int = 10_000_000) -> int:
    """Parse a bounded integer environment variable safely without crashing."""
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        print(f"[CONFIG] Invalid {name}; using default {default}.")
        value = default
    return max(minimum, min(maximum, value))

# Configure Discord Intents
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.messages = True
intents.members = True  # Required for Priority 3 member iteration & fetching

bot = discord.Client(intents=intents)

TASKS_INITIALIZED = False
HTTP_SESSION: Optional[aiohttp.ClientSession] = None

# ============================================================
# 2. THREAD-SAFE BOUNDED MEMORY STRUCTURES & GLOBAL STATE
# ============================================================
class BoundedSet:
    """Thread-safe bounded set with O(1) additions, deletions, and snapshotting."""
    def __init__(self, maxsize: int = 30000):
        self.maxsize = maxsize
        self._set: Set[Any] = set()
        self._order: collections.deque = collections.deque()
        self._lock = threading.Lock()

    def add(self, item: Any):
        with self._lock:
            if item in self._set:
                return
            if len(self._set) >= self.maxsize:
                oldest = self._order.popleft()
                self._set.remove(oldest)
            self._set.add(item)
            self._order.append(item)

    def discard(self, item: Any) -> None:
        """Remove an item if present; safe to call from worker cleanup paths."""
        with self._lock:
            if item not in self._set:
                return
            self._set.remove(item)
            try:
                self._order.remove(item)
            except ValueError:
                pass

    def __contains__(self, item: Any) -> bool:
        with self._lock:
            return item in self._set

    def __len__(self) -> int:
        with self._lock:
            return len(self._set)

    def snapshot(self) -> list:
        with self._lock:
            return list(self._order)

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

# ============================================================
# RATE LIMITING & TOKEN QUOTA TRACKERS (Unified Single Source of Truth)
# ============================================================
local_daily_reset_day: Optional[str] = datetime.now(timezone.utc).strftime("%Y-%m-%d")

GROQ_MAX_TPD = env_int("GROQ_MAX_TPD", 200000, minimum=1, maximum=100_000_000)
GROQ_MAX_TPM = env_int("GROQ_MAX_TPM", 8000, minimum=1, maximum=1_000_000)
GROQ_MAX_RPD = env_int("GROQ_MAX_RPD", 1000, minimum=1, maximum=1_000_000)

GROQ_REMAINING_TPD = GROQ_MAX_TPD
GROQ_REMAINING_TPM = GROQ_MAX_TPM
GROQ_REMAINING_RPD = GROQ_MAX_RPD
GROQ_RATE_LIMITED_UNTIL = 0.0

def check_and_reset_daily_quotas():
    """Resets daily remaining TPD and synchronizes traffic engine token counters if the UTC calendar day has changed."""
    global local_daily_reset_day, GROQ_REMAINING_TPD
    current_utc_day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    if local_daily_reset_day != current_utc_day:
        GROQ_REMAINING_TPD = GROQ_MAX_TPD
        globals()["GROQ_REMAINING_TPM"] = GROQ_MAX_TPM
        globals()["GROQ_REMAINING_RPD"] = GROQ_MAX_RPD
        local_daily_reset_day = current_utc_day
        
        if 'traffic_engine' in globals() and traffic_engine:
            with traffic_engine._lock:
                traffic_engine.local_daily_tokens = 0
                traffic_engine.integral_error = 0.0
                traffic_engine.volume_level = 1.0
                
        print(f"[QUOTA] Daily token trackers and volume controller reset for new UTC day: {current_utc_day}")

# Synchronization Locks & Constants
groq_api_lock = asyncio.Lock()
state_io_lock = asyncio.Lock()
ACTIVITY_PAUSE = 45

# ============================================================
# 3. DETERMINISTIC PRIORITY QUEUE & STATE PERSISTENCE STRUCTURES
# ============================================================
_job_sequence = itertools.count()

@dataclass(order=True)
class PendingJob:
    """Represents a scheduled job with deterministic priority ordering."""
    priority_tier: int
    timestamp: float
    sequence: int = field(default_factory=lambda: next(_job_sequence))
    channel: Any = field(compare=False, default=None)
    prompt_text: str = field(compare=False, default="")
    reference_message: Any = field(compare=False, default=None)
    author_id: Optional[int] = field(compare=False, default=None)

# Asynchronous Queues for Job Execution and State Persistence
delay_queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
state_save_queue: asyncio.Queue = asyncio.Queue()

def p2_key(guild_id: int, author_id: int) -> str:
    """Generates a unique composite key for Priority 2 member tracking."""
    return f"{guild_id}:{author_id}"
# ============================================================
# 4. GOOGLE MAPS TRAFFIC ENGINE & VOLUME CONTROL
# ============================================================
class GoogleMapsTrafficEngine:
    """Controls token expenditure and dynamic volume scaling with absolute priority protection for search and media operations."""
    def __init__(self, daily_quota: int = 200000, target_spent_pct: float = 0.20):
        self.daily_quota = max(1, daily_quota)  # Prevent zero division
        self.target_spent_pct = target_spent_pct
        self.local_daily_tokens = 0
        self.integral_error = 0.0
        self.volume_level = 1.0
        self._lock = threading.RLock()
        
        # Priorities 1 and 4 are explicitly prioritized for search, media, and Remember Templates
        self.priority_hierarchy = {
            1: {"dsn": 1500, "dsl": 10000, "mode": "ABSOLUTE-OVERRIDE: SEARCH/REMEMBER + MEDIA"},
            4: {"dsn": 1200, "dsl": 5000,  "mode": "ABSOLUTE-OVERRIDE: LIVE CHAT SEARCH + MEDIA"},
            6: {"dsn": 0,    "dsl": 700,   "mode": "RANDOM-MEDIA-ONLY"},
            7: {"dsn": 0,    "dsl": 400,   "mode": "CUSTOMIZED-MEDIA + BOT-MIRROR"},
            8: {"dsn": 800,  "dsl": 1000,  "mode": "CUSTOMIZED-TEXT ONLY + BOT-MIRROR"},
            9: {"dsn": 1000, "dsl": 3000,  "mode": "CUSTOMIZED-MEDIA/TEXT"},
            2: {"dsn": 0,    "dsl": 700,   "mode": "CUSTOMIZED-MEDIA-ONLY (1 per member)"},
            5: {"dsn": 0,    "dsl": 700,   "mode": "RANDOM-MEDIA-ONLY (1 per member)"},
            3: {"dsn": 0,    "dsl": 700,   "mode": "RANDOM-MEDIA-ONLY"}
        }

    @property
    def spent_fraction(self) -> float:
        with self._lock:
            return max(0.0, min(1.0, self.local_daily_tokens / self.daily_quota))

    def get_daily_tokens(self) -> int:
        """Thread-safe accessor for the traffic engine's daily token count."""
        with self._lock:
            return self.local_daily_tokens

    def update_navigation(self):
        """Calculates volume level adjustments using a smooth proportional controller with a hard 0.22 floor."""
        with self._lock:
            s = max(0.0, min(1.0, self.local_daily_tokens / self.daily_quota))
            if s < 0.25:
                target = 1.0
            elif s < 0.55:
                target = 1.0 - (s - 0.25) * 1.3
            else:
                target = max(0.22, 0.55 - (s - 0.55) * 1.8)

            self.volume_level += (target - self.volume_level) * 0.15
            self.volume_level = max(0.22, min(1.0, self.volume_level))

    def get_allocation(self, priority_tier: int) -> Tuple[int, int, str]:
        base = self.priority_hierarchy.get(priority_tier, {"dsn": 100, "dsl": 500, "mode": "RANDOM-MEDIA-ONLY"})
        S = self.spent_fraction
        
        # ABSOLUTE OVERRIDE: Priorities 1 and 4 bypass quota lockdowns completely
        if priority_tier in (1, 4):
            with self._lock:
                current_volume = max(0.5, self.volume_level)  # Ensure minimum operational volume for search/media
            return int(base["dsn"] * current_volume), int(base["dsl"] * current_volume), base["mode"]

        # Strict Quota Zone Enforcement for background / lower-level tiers only
        if S >= 0.75:
            return 0, 0, "RANDOM-MEDIA-ONLY (QUOTA LOCKDOWN)"
        elif S >= 0.40 and priority_tier in (6, 7, 2, 5, 3):
            return 0, int(base["dsl"] * 0.4), "TOKEN-CHEAPER CONSERVING MODE"

        if base["dsn"] == 0:
            with self._lock:
                current_volume = self.volume_level
            effective_dsl = int(base["dsl"] * current_volume) if S < 0.40 else int(base["dsl"] * 0.2)
            return 0, effective_dsl, base["mode"]

        with self._lock:
            current_volume = self.volume_level

        effective_dsn = int(base["dsn"] * current_volume)
        effective_dsl = int(base["dsl"] * current_volume)
        return max(1, effective_dsn), max(1, effective_dsl), base["mode"]

    def record_expenditure(self, tokens: int):
        """Atomically records token expenditure, updates navigation control, and synchronizes global TPD tracking."""
        global GROQ_REMAINING_TPD
        spent = max(0, int(tokens or 0))
        if spent <= 0:
            return
        with self._lock:
            self.local_daily_tokens += spent
            GROQ_REMAINING_TPD = max(0, GROQ_REMAINING_TPD - spent)
        self.update_navigation()

# Initialize traffic engine synchronized with GROQ_MAX_TPD from Section 2
traffic_engine = GoogleMapsTrafficEngine(daily_quota=GROQ_MAX_TPD)

# ============================================================
# 5. ASYNCHRONOUS SINGLE-WRITER STATE PERSISTENCE & BACKUP ROTATION
# ============================================================
def trigger_save_state():
    """Non-blocking trigger to request a state save operation."""
    try:
        state_save_queue.put_nowait(True)
    except Exception:
        pass

def load_state():
    """Loads persistent state from JSON storage into memory structures with intelligent primary/backup recovery and integer key normalization."""
    global channel_cursors, p4_cursors, p7_cursors, p8_cursors, blocked_users, p3_member_indices
    state_path_str = str(STATE_PATH)
    bak_path = state_path_str + ".bak"
    
    target_path = None
    if os.path.exists(state_path_str):
        target_path = state_path_str
    elif os.path.exists(bak_path):
        print(f"[STATE] Primary state file missing (interrupted write detected). Falling back to backup {bak_path}...")
        target_path = bak_path
    else:
        print(f"[STATE] No existing state file or backup found at {state_path_str}. Starting fresh.")
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
            data = json.load(f)
            
        for item in data.get("replied", []):
            replied_message_ids.add(item)
        for item in data.get("p2_done_members", []):
            p2_replied_member_ids.add(item)
        for item in data.get("p5_done_members", []):
            p5_replied_member_ids.add(item)
            
        channel_cursors.update(_to_int_keys(data.get("cursors", {})))
        p4_cursors.update(_to_int_keys(data.get("p4_cursors", {})))
        p7_cursors.update(_to_int_keys(data.get("p7_cursors", {})))
        p8_cursors.update(_to_int_keys(data.get("p8_cursors", {})))
        blocked_users.update(_to_int_keys(data.get("blocked_users", {})))
        p3_member_indices.update(_to_int_keys(data.get("p3_member_indices", {})))
        
        print(f"[STATE] Successfully loaded state from {target_path}")
        
    except Exception as e:
        print(f"[STATE] Load failed from {target_path} ({e}).")
        if target_path == state_path_str and os.path.exists(bak_path):
            print(f"[STATE] Attempting emergency fallback to backup {bak_path}...")
            try:
                with open(bak_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    raise ValueError("backup state root must be a JSON object")
                for item in data.get("replied", []):
                    replied_message_ids.add(item)
                for item in data.get("p2_done_members", []):
                    p2_replied_member_ids.add(item)
                for item in data.get("p5_done_members", []):
                    p5_replied_member_ids.add(item)
                    
                channel_cursors.update(_to_int_keys(data.get("cursors", {})))
                p4_cursors.update(_to_int_keys(data.get("p4_cursors", {})))
                p7_cursors.update(_to_int_keys(data.get("p7_cursors", {})))
                p8_cursors.update(_to_int_keys(data.get("p8_cursors", {})))
                blocked_users.update(_to_int_keys(data.get("blocked_users", {})))
                p3_member_indices.update(_to_int_keys(data.get("p3_member_indices", {})))
                print(f"[STATE] Successfully recovered state from backup {bak_path}")
            except Exception as bak_e:
                print(f"[STATE] Emergency backup recovery also failed: {bak_e}")
        else:
            print(f"[STATE] No valid recovery path remaining. Starting fresh.")

async def state_writer_worker():
    """Single-writer background worker that coalesces state changes, rotates backups, and persists safely with guaranteed task accounting."""
    state_path_str = str(STATE_PATH)
    while True:
        await state_save_queue.get()
        await asyncio.sleep(1.0)
        drained_count = 1
        try:
            while True:
                state_save_queue.get_nowait()
                drained_count += 1
        except asyncio.QueueEmpty:
            pass

        try:
            async with state_io_lock:
                data = {
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
                def _write():
                    temp_path = state_path_str + ".tmp"
                    bak_path = state_path_str + ".bak"
                    try:
                        directory = os.path.dirname(state_path_str)
                        if directory:
                            os.makedirs(directory, exist_ok=True)
                        with open(temp_path, "w", encoding="utf-8") as f:
                            json.dump(data, f, indent=2, ensure_ascii=False)
                            f.flush()
                            os.fsync(f.fileno())
                        if os.path.exists(state_path_str):
                            os.replace(state_path_str, bak_path)
                        os.replace(temp_path, state_path_str)
                    except Exception as e:
                        try:
                            if os.path.exists(temp_path):
                                os.remove(temp_path)
                        except OSError:
                            pass
                        print(f"[STATE] Write operation failed: {e}")
                await asyncio.to_thread(_write)

        except Exception as worker_err:
            print(f"[STATE WORKER ERROR] {worker_err}")
        finally:
            for _ in range(drained_count):
                try:
                    state_save_queue.task_done()
                except ValueError:
                    pass

# ============================================================
# 6. ASYNCHRONOUS TELEMETRY SERVER & METRICS DASHBOARD
# ============================================================
_web_runner = None
_web_site = None
_bot_start_time = time.time()
_telemetry_loop = None
_telemetry_task = None
_web_thread = None

async def handle_health(request):
    """Lightweight health check endpoint for container lifecycle probes."""
    return web.Response(text="Oatmeal AI V101.0 Operational")

async def handle_telemetry(request):
    """Detailed telemetry metrics endpoint returning uptime, token quotas, queue stats, and cache health with defensive error handling."""
    uptime_seconds = int(time.time() - _bot_start_time)
    hours, remainder = divmod(uptime_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    uptime_formatted = f"{hours}h {minutes}m {seconds}s"

    try:
        local_tokens = traffic_engine.get_daily_tokens() if 'traffic_engine' in globals() and traffic_engine else 0
        spent_frac = traffic_engine.spent_fraction if 'traffic_engine' in globals() and traffic_engine else 0.0
        vol_level = traffic_engine.volume_level if 'traffic_engine' in globals() and traffic_engine else 1.0
    except Exception:
        local_tokens, spent_frac, vol_level = 0, 0.0, 1.0

    try:
        rem_tpd = GROQ_REMAINING_TPD if 'GROQ_REMAINING_TPD' in globals() else 0
    except Exception:
        rem_tpd = 0

    try:
        d_q_size = delay_queue.qsize() if 'delay_queue' in globals() and delay_queue else 0
        s_q_size = state_save_queue.qsize() if 'state_save_queue' in globals() and state_save_queue else 0
        rep_tracked = len(replied_message_ids) if 'replied_message_ids' in globals() else 0
        p2_tracked = len(p2_replied_member_ids) if 'p2_replied_member_ids' in globals() else 0
    except Exception:
        d_q_size, s_q_size, rep_tracked, p2_tracked = 0, 0, 0, 0

    try:
        cache_entries = len(search_cache._cache) if 'search_cache' in globals() and search_cache else 0
        cache_inflight = len(search_cache._inflight) if 'search_cache' in globals() and search_cache else 0
    except Exception:
        cache_entries, cache_inflight = 0, 0

    data = {
        "status": "online",
        "uptime": uptime_formatted,
        "uptime_seconds": uptime_seconds,
        "token_metrics": {
            "local_daily_tokens": local_tokens,
            "groq_remaining_tpd": rem_tpd,
            "spent_fraction": spent_frac,
            "volume_level": vol_level
        },
        "queues_and_memory": {
            "delay_queue_size": d_q_size,
            "state_save_queue_size": s_q_size,
            "replied_messages_tracked": rep_tracked,
            "p2_done_members_tracked": p2_tracked
        },
        "subsystems": {
            "search_cache_entries": cache_entries,
            "search_cache_inflight": cache_inflight,
        }
    }
    return web.json_response(data)

async def _run_web_server():
    """Initializes and runs the aiohttp telemetry and health check web server with full lifecycle control."""
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
        print(f"[TELEMETRY] Async web server and metrics dashboard active on port {port}")
        
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        print("[TELEMETRY] Shutdown signal received. Cleaning up web server resources...")
    except Exception as e:
        print(f"[TELEMETRY RUNTIME ERROR] {e}")
    finally:
        try:
            if _web_site:
                await _web_site.stop()
            if _web_runner:
                await _web_runner.cleanup()
            print("[TELEMETRY] Web server successfully stopped and port released.")
        except Exception as cleanup_err:
            print(f"[TELEMETRY CLEANUP ERROR] {cleanup_err}")

def start_instant_web_server():
    """Start telemetry in one tracked daemon thread; safe to call once."""
    global _web_thread
    if _web_thread and _web_thread.is_alive():
        return
    def _thread_target():
        try:
            asyncio.run(_run_web_server())
        except Exception as e:
            print(f"[TELEMETRY SERVER EXCEPTION] {e}")
    _web_thread = threading.Thread(target=_thread_target, daemon=True, name="TelemetryServerThread")
    _web_thread.start()

def stop_telemetry_server():
    """Cancel only the telemetry server task and wait briefly for its thread to exit."""
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
# 7. PLATFORM-NATIVE BIMEDIA GENERATOR (Direct Platform Scraping & Pools)
# ============================================================
class BiMediaGenerator:
    # Diverse fallback pools to guarantee variety and prevent single-asset looping
    FALLBACK_VIDEOS = [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=9bZkp7q19f0",
        "https://www.youtube.com/watch?v=hTWKbfoikeg",
        "https://www.youtube.com/watch?v=3JWTaaS7LWo",
        "https://www.youtube.com/watch?v=astJSke0iv8",
        "https://www.youtube.com/watch?v=y6120QOlsfU"
    ]
    FALLBACK_GIFS = [
        "https://media.tenor.com/C7K9w4K4q1wAAAAC/oatmeal-cat.gif",
        "https://media.tenor.com/images/9a8b1a7f36c64b58e6587424c55d0a69/tenor.gif",
        "https://media.tenor.com/v2/g_preview?itemref=17562854",
        "https://media.tenor.com/images/5d326117565155f9e710d0a2f7c0a1b2/tenor.gif",
        "https://media.tenor.com/gUiu1zyw-AYAAAAC/cat-vibing.gif"
    ]
    FALLBACK_AUDIOS = [
        "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT",
        "https://open.spotify.com/track/0bYg9bo50gSsH3bdXe2SQn",
        "https://open.spotify.com/track/13WO20StGPTGXvUWs4KQQQ"
    ]
    FALLBACK_LINKS = [
        "https://en.wikipedia.org/wiki/1990s",
        "https://en.wikipedia.org/wiki/Retro_style",
        "https://en.wikipedia.org/wiki/History_of_video_games",
        "https://en.wikipedia.org/wiki/Compact_disc",
        "https://en.wikipedia.org/wiki/Internet_archive"
    ]

    @staticmethod
    def extract_keywords(text: str) -> str:
        """Extracts significant keywords/nouns from user prompt text for targeted platform searches."""
        if not text:
            return ""
        stop_words = {
            "the", "a", "an", "in", "on", "at", "to", "for", "is", "are", 
            "about", "you", "me", "it", "this", "that", "what", "how", "why", 
            "who", "where", "when", "oatmeal", "joey", "arnold", "bot", "hey", "please", "can"
        }
        tokens = [
            w.strip("?.,!:") 
            for w in text.split() 
            if w.lower().strip("?.,!:") not in stop_words and len(w.strip("?.,!:")) > 2
        ]
        return " ".join(tokens[:3]) if tokens else text[:30]

    @staticmethod
    async def search_youtube_native(session: aiohttp.ClientSession, query: str) -> List[str]:
        """Scrapes YouTube search results natively to extract valid video IDs."""
        cleaned = RE_CLEAN_MENTIONS.sub('', query)
        cleaned = RE_CLEAN_SEARCH.sub('', cleaned)
        cleaned = RE_CLEAN_SYMBOLS.sub(' ', cleaned)
        query_str = urllib.parse.quote(re.sub(r'\s+', ' ', cleaned).strip()[:100] or "90s retro commercial")
        
        url = f"https://www.youtube.com/results?search_query={query_str}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9"
        }
        video_urls = []
        try:
            async with session.get(url, headers=headers, timeout=6) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    # Extract video IDs using YouTube's native JSON/HTML structure
                    raw_ids = re.findall(r'"videoId":"([a-zA-Z0-9_-]{11})"', html)
                    for vid_id in raw_ids:
                        video_urls.append(f"https://www.youtube.com/watch?v={vid_id}")
        except Exception:
            pass
        return list(dict.fromkeys(video_urls))

    @staticmethod
    async def search_tenor_native(session: aiohttp.ClientSession, query: str) -> List[str]:
        """Scrapes Tenor search results natively for reaction GIFs."""
        cleaned = RE_CLEAN_MENTIONS.sub('', query)
        cleaned = RE_CLEAN_SEARCH.sub('', cleaned)
        cleaned = RE_CLEAN_SYMBOLS.sub(' ', cleaned)
        query_str = urllib.parse.quote(re.sub(r'\s+', ' ', cleaned).strip()[:80] or "funny reaction")
        
        url = f"https://tenor.com/search/{query_str}-gifs"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        gif_urls = []
        try:
            async with session.get(url, headers=headers, timeout=6) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    # Extract direct image or view links from Tenor HTML
                    raw_links = re.findall(r'href="(https://tenor.com/view/[^"]+)"', html)
                    for link in raw_links:
                        gif_urls.append(link)
                    
                    raw_imgs = re.findall(r'src="(https://media\.tenor\.com/[^"]+\.gif)"', html)
                    for img in raw_imgs:
                        gif_urls.append(img)
        except Exception:
            pass
        return list(dict.fromkeys(gif_urls))

    @staticmethod
    async def search_wikipedia_native(session: aiohttp.ClientSession, query: str) -> List[str]:
        """Searches Wikipedia natively via its open search API."""
        cleaned = RE_CLEAN_MENTIONS.sub('', query)
        query_str = urllib.parse.quote(re.sub(r'\s+', ' ', cleaned).strip()[:60] or "History")
        url = f"https://en.wikipedia.org/w/api.php?action=opensearch&search={query_str}&limit=5&namespace=0&format=json"
        headers = {"User-Agent": "OatmealBot/101.0"}
        wiki_urls = []
        try:
            async with session.get(url, headers=headers, timeout=5) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, list) and len(data) > 3 and isinstance(data[3], list):
                        wiki_urls.extend(data[3])
        except Exception:
            pass
        return wiki_urls

    @classmethod
    async def fetch_media_by_type(cls, session: aiohttp.ClientSession, idx: int, query: str = "") -> Tuple[str, Optional[discord.Embed]]:
        """Picks the idx-th media category (1-9) and searches within the platform's native interface."""
        base_query = cls.extract_keywords(query) if query else ""

        # 1. VIDEOS (YouTube Native Search)
        if idx == 1:
            found_urls = await cls.search_youtube_native(session, base_query or "classic 90s commercial")
            if found_urls:
                return random.choice(found_urls[:15]), None
            return random.choice(cls.FALLBACK_VIDEOS), None

        # 2. GIFs (Tenor Native Search)
        elif idx == 2:
            found_urls = await cls.search_tenor_native(session, base_query or "retro reaction")
            gif_url = ""
            if found_urls:
                gif_url = random.choice(found_urls[:12])
            
            if not gif_url:
                gif_url = random.choice(cls.FALLBACK_GIFS)

            emb = discord.Embed()
            emb.set_image(url=gif_url)
            return "", emb

        # 3. PHOTOS (Imgur / Picsum / Unsplash)
        elif idx == 3:
            seed = urllib.parse.quote(base_query if base_query else str(random.randint(10, 99999)))
            photo_url = f"https://picsum.photos/seed/{seed}/640/360.jpg"
            emb = discord.Embed()
            emb.set_image(url=photo_url)
            return "", emb

        # 4. DRAWINGS / AI ART (Pollinations AI Native Generator)
        elif idx == 4:
            art_prompt = f"{base_query} 90s cartoon retro art style masterwork" if base_query else "retro synthwave sunset robot artwork"
            prompt_slug = urllib.parse.quote(art_prompt)
            art_url = f"https://pollinations.ai/p/{prompt_slug}?width=640&height=360&seed={random.randint(1, 99999)}"
            
            emb = discord.Embed()
            emb.set_image(url=art_url)
            return "", emb

        # 5. AUDIO / MUSIC (Spotify / SoundCloud Pools)
        elif idx == 5:
            return random.choice(cls.FALLBACK_AUDIOS), None

        # 6. WEB LINKS (Wikipedia Native Search)
        elif idx == 6:
            found_urls = await cls.search_wikipedia_native(session, base_query or "1990s")
            if found_urls:
                return random.choice(found_urls), None
            return random.choice(cls.FALLBACK_LINKS), None

        # 7. ASCII ART
        elif idx == 7:
            ascii_templates = [
                "```\n  (o.o)  Oatmeal AI V101.0\n  / | \\\n _/\\_/\\_\n```",
                "```\n  /\\_/\\\n ( o.o )  Arnold Attic\n  > ^ <\n```",
                "```\n   ____        _                   _ \n  / __|___  _| |_ _ __  ___ _ _  | |\n | (__/ _ \\(_-<  _| '  \\/ -_) '_| |_|\n  \\___\\___/ /_/\\__|_|_|_\\___|_|   (_)\n```"
            ]
            return random.choice(ascii_templates), None

        # 8. EMOTICONS
        elif idx == 8:
            emoticons = ["(ง'̀-'́)ง", "¯\\_(ツ)_/¯", "( ͡° ͜ʖ ͡°)", "(⌐■_■)", "(づ｡◕‿‿◕｡)づ", "ಠ_ಠ", "(ʘ__ʘ)"]
            return random.choice(emoticons), None

        # 9. STICKERS / BADGES
        elif idx == 9:
            stickers = ["⭐", "🥣", "🚀", "🕹️", "📼", "🔥", "💡", "🏆", "🎯"]
            return random.choice(stickers), None

        return "🥣", None

    @classmethod
    async def generate(cls, session: aiohttp.ClientSession, mode: str = "random", chat_text: str = "") -> Tuple[str, Optional[discord.Embed]]:
        """BIMEDIA-GENERATOR Entrypoint using weighted probability favoritism and platform-native searching."""
        weighted_categories = [
            1, 1, 1,     # Videos (Weight 3)
            2, 2, 2, 2,  # GIFs (Weight 4 - Favored)
            3, 3, 3,     # Photos (Weight 3)
            4, 4,        # Drawings (Weight 2)
            5,           # Audio (Weight 1)
            6,           # Links (Weight 1)
            7,           # ASCII (Weight 1)
            8,           # Emoticons (Weight 1)
            9            # Stickers (Weight 1)
        ]
        
        idx = random.choice(weighted_categories)

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

        media_content, embed = await cls.fetch_media_by_type(session, idx, query=chat_text if mode == "custom" else "")
        return media_content, embed


# ============================================================
# 8. SEARCH SUBSYSTEM & TOKEN-FREE REMEMBER TEMPLATE ENGINE
# ============================================================
_background_tasks: List[asyncio.Task] = []

class AsyncSearchCache:
    """Thread-safe asynchronous search cache with atomic single-flight request coalescing and safe error fallback."""
    def __init__(self, ttl: float = 300.0, max_size: int = 200):
        self.ttl = ttl
        self.max_size = max_size
        self._cache: Dict[str, Tuple[float, str]] = {}
        self._order: collections.deque = collections.deque()
        self._inflight: Dict[str, asyncio.Future] = {}
        self._lock = asyncio.Lock()

    async def get_or_fetch(self, query: str, session: aiohttp.ClientSession) -> str:
        q_key = query.lower().strip()
        now = time.monotonic()

        future = None
        is_creator = False

        async with self._lock:
            if q_key in self._cache:
                ts, res = self._cache[q_key]
                if now - ts < self.ttl:
                    return res
                else:
                    self._cache.pop(q_key, None)
                    try:
                        self._order.remove(q_key)
                    except ValueError:
                        pass

            if q_key in self._inflight:
                future = self._inflight[q_key]
                is_creator = False
            else:
                loop = asyncio.get_running_loop()
                future = loop.create_future()
                self._inflight[q_key] = future
                is_creator = True

        if not is_creator:
            return await future

        res = ""
        try:
            cleaned = RE_CLEAN_MENTIONS.sub('', query)
            cleaned = RE_CLEAN_SEARCH.sub('', cleaned)
            cleaned = RE_CLEAN_SYMBOLS.sub(' ', cleaned)
            cleaned = re.sub(r'\s+', ' ', cleaned).strip()[:120]

            url = "https://lite.duckduckgo.com/lite/"
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            async with session.post(url, data={'q': cleaned}, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    clean_s = []
                    
                    try:
                        from bs4 import BeautifulSoup
                        soup = BeautifulSoup(html, 'html.parser')
                        snippet_tags = soup.find_all(class_=re.compile(r'snippet', re.I))
                        if not snippet_tags:
                            snippet_tags = soup.find_all('td', class_=re.compile(r'result', re.I))
                        
                        for tag in snippet_tags[:4]:
                            text = tag.get_text(strip=True)
                            if len(text) > 15:
                                clean_s.append(text)
                    except Exception as bs_err:
                        print(f"[SEARCH BS4 PARSE WARNING] {bs_err}")

                    if not clean_s:
                        snippets = RE_SNIPPETS.findall(html)
                        if not snippets:
                            snippets = re.findall(r'<td[^>]*class=["\']result-snippet["\'][^>]*>(.*?)</td>', html, re.DOTALL)
                        if not snippets:
                            snippets = re.findall(r'<div[^>]*class=["\']result__snippet["\'][^>]*>(.*?)</div>', html, re.DOTALL)

                        for s in snippets[:4]:
                            stripped = RE_HTML_STRIP.sub('', s).strip()
                            stripped = stripped.replace("&amp;", "&").replace("&quot;", "\"").replace("&#39;", "'").replace("&lt;", "<").replace("&gt;", ">")
                            if len(stripped) > 15:
                                clean_s.append(stripped)

                    res = "REAL-TIME SEARCH RESULTS:\n" + "\n".join(f"- {s}" for s in clean_s) if clean_s else ""
                else:
                    res = ""

            async with self._lock:
                if len(self._cache) >= self.max_size and self._order:
                    oldest = self._order.popleft()
                    self._cache.pop(oldest, None)
                
                if q_key in self._cache:
                    try:
                        self._order.remove(q_key)
                    except ValueError:
                        pass
                        
                self._cache[q_key] = (time.monotonic(), res)
                self._order.append(q_key)
                self._inflight.pop(q_key, None)

            if not future.done():
                future.set_result(res)
            return res

        except Exception as e:
            print(f"[SEARCH ERROR] {e}")
            async with self._lock:
                self._inflight.pop(q_key, None)
            if not future.done():
                future.set_result("")
            return ""

search_cache = AsyncSearchCache()


async def get_remember_template_reply(channel: Any, prompt_text: str, sender: Any, session: aiohttp.ClientSession) -> Tuple[str, str, Optional[discord.Embed]]:
    """Token-Free Multi-Source Remember Template Engine adhering strictly to all 14 criteria."""
    if not prompt_text or "remember" not in prompt_text.lower():
        return "", "", None

    words = [w.strip("?.,!:") for w in prompt_text.split()]
    keyword = None
    
    try:
        lower_words = [w.lower() for w in words]
        if "remember" in lower_words:
            idx = lower_words.index("remember")
            candidates = words[idx+1:]
            stop_words = {
                "the", "a", "an", "in", "on", "at", "to", "for", "is", "are", 
                "about", "you", "any", "some", "me", "it", "this", "when", 
                "what", "where", "who", "how", "did", "do", "was", "we", "that"
            }
            significant = [w for w in candidates if w.lower() not in stop_words and len(w) > 1]
            if not significant:
                significant = [w for w in words[:idx] if w.lower() not in stop_words and len(w) > 1]
            if significant:
                keyword = significant[0]
    except Exception:
        pass

    sender_name = "User"
    if sender:
        if hasattr(sender, "display_name"):
            sender_name = sender.display_name
        elif isinstance(sender, str):
            sender_name = sender

    if not keyword:
        return f"@{sender_name}, I don't remember.", "", None

    # 1. Source 1: Discord History Search (Oldest first)
    matched_msg = None
    try:
        async for msg in channel.history(oldest_first=True, limit=300):
            if not msg.author.bot and msg.content:
                if re.search(r'\b' + re.escape(keyword.lower()) + r'\b', msg.content.lower()):
                    matched_msg = msg
                    break
    except Exception as hist_err:
        print(f"[REMEMBER ENGINE ERROR] Channel history fetch failed: {hist_err}")

    if not matched_msg:
        return f"@{sender_name}, I don't remember.", "", None

    op_name = matched_msg.author.display_name
    discord_year = matched_msg.created_at.year
    content = matched_msg.content.strip()
    
    sentences = re.split(r'(?<=[.!?])\s+', content)
    first_sentence = sentences[0] if sentences else content
    first_sentence = first_sentence.strip('"\'')

    # 2. Source 2: Timeline Match
    timeline_year = "1993"
    for line in FULL_ANNUAL_OUTLINE.strip().splitlines():
        if re.search(r'\b' + re.escape(keyword.lower()) + r'\b', line.lower()):
            years_in_line = RE_YEARS.findall(line)
            if years_in_line:
                timeline_year = years_in_line[0]
                break

    # 3. Source 3: Internet Quote Search via DuckDuckGo Lite
    internet_year = "2020"
    internet_quote = f"The {keyword.lower()} were widely discussed online."
    try:
        search_res = await search_cache.get_or_fetch(f"{keyword} history quote", session)
        if search_res:
            lines = [ln.strip("- ").strip() for ln in search_res.splitlines() if len(ln) > 20]
            if lines:
                internet_quote = lines[0][:120]
                found_years = RE_YEARS.findall(internet_quote)
                if found_years:
                    internet_year = found_years[0]
    except Exception as net_search_err:
        print(f"[REMEMBER INTERNET SEARCH ERROR] {net_search_err}")

    # 4. Source 4: Keyword-Targeted Media Generation
    media_url, embed = "", None
    try:
        media_url, embed = await BiMediaGenerator.generate(session, "custom", keyword)
    except Exception as media_err:
        print(f"[REMEMBER MEDIA ERROR] {media_err}")

    template_1 = (
        f"@{sender_name}, in {discord_year}: @{op_name} said: \"{first_sentence}\"\n"
        f"From my timeline: {keyword.lower()}: {timeline_year}.\n"
        f"From the Internet: {internet_year}: \"{internet_quote}\""
    )

    return template_1, media_url, embed
# ============================================================
# 9. PAYLOAD BUILDER
# ============================================================
async def build_payload(content_str: str, media_url: str = "", embed: Optional[discord.Embed] = None) -> Tuple[str, dict]:
    """
    Formats text content, direct media URLs, and rich embeds for Discord dispatch, 
    ensuring robust multi-asset pairing and preventing empty payload exceptions.
    """
    kwargs = {}
    final_content = content_str.strip() if content_str else ""
    
    # Append media URL to text content if present and no conflicting embed exists
    if media_url:
        if final_content:
            final_content += f"\n{media_url}"
        else:
            final_content = media_url
            
    # Attach rich embed if generated
    if embed:
        kwargs["embed"] = embed
        
    # Absolute fallback to prevent Discord API empty message exceptions
    if not final_content and not embed:
        final_content = "🥣 Oatmeal AI operational."
        
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
    Primary Remember Template Search Engine.
    Unifies historical Discord scanning, timeline extraction, internet search quotes, 
    and BiMedia generation into a single non-overlapping primary pathway.
    """
    if not prompt_text:
        return "", "", None

    words = [w.strip("?.,!:") for w in prompt_text.split()]
    keyword = None
    
    try:
        lower_words = [w.lower() for w in words]
        if "remember" in lower_words:
            idx = lower_words.index("remember")
            candidates = words[idx+1:]
        else:
            candidates = words

        stop_words = {
            "the", "a", "an", "in", "on", "at", "to", "for", "is", "are", 
            "about", "you", "any", "some", "me", "it", "this", "when", 
            "what", "where", "who", "how", "did", "do", "was", "we", "that", "remember"
        }
        significant = [w for w in candidates if w.lower() not in stop_words and len(w) > 1]
        if not significant and "remember" in lower_words:
            significant = [w for w in words[:idx] if w.lower() not in stop_words and len(w) > 1]
        if significant:
            keyword = significant[0]
    except Exception:
        pass
        
    sender_name = sender.display_name if sender and hasattr(sender, "display_name") else "User"

    if not keyword:
        return f"@{sender_name}, I don't remember.", "", None

    # 1. Source 1: Discord History Search (Oldest First)
    matched_msg = None
    try:
        async for msg in channel.history(oldest_first=True, limit=300):
            if not msg.author.bot and msg.content:
                if re.search(r'\b' + re.escape(keyword.lower()) + r'\b', msg.content.lower()):
                    matched_msg = msg
                    break
    except Exception as hist_err:
        print(f"[REMEMBER ENGINE ERROR] History fetch failed: {hist_err}")

    if not matched_msg:
        return f"@{sender_name}, I don't remember.", "", None

    op_name = matched_msg.author.display_name
    discord_year = matched_msg.created_at.year
    content = matched_msg.content.strip()
    
    sentences = re.split(r'(?<=[.!?])\s+', content)
    first_sentence = sentences[0] if sentences else content
    first_sentence = first_sentence.strip('"\'')

    # 2. Source 2: Timeline Match
    timeline_year = "1993"
    for line in FULL_ANNUAL_OUTLINE.strip().splitlines():
        if re.search(r'\b' + re.escape(keyword.lower()) + r'\b', line.lower()):
            years_in_line = RE_YEARS.findall(line)
            if years_in_line:
                timeline_year = years_in_line[0]
                break

    # 3. Source 3: Internet Quote via DuckDuckGo Lite Search Cache
    internet_year = "2020"
    internet_quote = f"The {keyword.lower()} were widely discussed online."
    try:
        search_res = await search_cache.get_or_fetch(f"{keyword} history quote", session)
        if search_res:
            lines = [ln.strip("- ").strip() for ln in search_res.splitlines() if len(ln) > 20]
            if lines:
                internet_quote = lines[0][:120]
                found_years = RE_YEARS.findall(internet_quote)
                if found_years:
                    internet_year = found_years[0]
    except Exception as net_search_err:
        print(f"[REMEMBER SEARCH ERROR] {net_search_err}")

    # 4. Source 4: Keyword-Targeted Media Generation
    media_url, embed = "", None
    try:
        media_url, embed = await BiMediaGenerator.generate(session, "custom", keyword)
    except Exception as media_err:
        print(f"[REMEMBER MEDIA ERROR] {media_err}")

    template_1 = (
        f"@{sender_name}, in {discord_year}: @{op_name} said: \"{first_sentence}\"\n"
        f"From my timeline: {keyword.lower()}: {timeline_year}.\n"
        f"From the Internet: {internet_year}: \"{internet_quote}\""
    )

    return template_1, media_url, embed
# ============================================================
# 12. STREAMLINED JOB EXECUTION ENGINE & PRECEDENCE OVERRIDES
# ============================================================

async def process_job(job: PendingJob, session: aiohttp.ClientSession):
    """
    Processes queued jobs with clean, unambiguous routing:
    1. Remember Template Engine (Absolute Precedence for historical/remember queries).
    2. Real-Time Web Search (Absolute Precedence for live search queries).
    3. Persona LLM Fallback with Timeline Context.
    """
    global GROQ_RATE_LIMITED_UNTIL, GROQ_REMAINING_TPD
    
    check_and_reset_daily_quotas()
    if session is None or session.closed:
        print("[JOB] HTTP session unavailable; releasing pending message claim.")
        if job.reference_message:
            pending_message_ids.discard(getattr(job.reference_message, "id", None))
        return

    dsn, dsl, mode = traffic_engine.get_allocation(job.priority_tier)
    
    remember_template_reply = ""
    remember_media_url = ""
    remember_embed = None
    search_context = ""

    prompt_text = job.prompt_text or ""
    prompt_lower = prompt_text.lower()
    sender_obj = job.reference_message.author if job.reference_message else None

    # ==========================================================
    # ABSOLUTE ROUTE 1: THE REMEMBER TEMPLATE ENGINE
    # ==========================================================
    if job.priority_tier in (1, 4) and ("remember" in prompt_lower or len(prompt_text.split()) <= 4):
        remember_template_reply, remember_media_url, remember_embed = await get_remember_template_reply(
            job.channel, prompt_text, sender_obj, session
        )

    # ==========================================================
    # ABSOLUTE ROUTE 2: REAL-TIME WEB SEARCH
    # ==========================================================
    if not remember_template_reply and job.priority_tier in (1, 4):
        if any(w in prompt_lower for w in ("search", "find", "price", "google", "what", "look", "turtles")):
            search_context = await search_cache.get_or_fetch(prompt_text, session)

    # Build Persona System Prompt & Messages for LLM Fallback
    timeline = extract_relevant_timeline(prompt_text)
    system = f"You are Joey Arnold (Oatmeal Joey Arnold, @JoeyArnoldVN). Speak in first person. Voice: rapid, playful, nostalgic 90s.\n\n{timeline}"
    if search_context:
        system += f"\n\nSearch Results:\n{search_context}"

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt_text or "Thought?"}
    ]
    
    max_tokens = min(dsl, max(1, dsn)) if dsn > 0 else 0
    reply = "🥣 Oatmeal AI online and operational!"
    actual_tokens = 0

    if remember_template_reply:
        reply = remember_template_reply
        max_tokens = 0
    elif search_context:
        max_tokens = min(dsl, 300)
    elif GROQ_REMAINING_TPD <= 0 or time.monotonic() < GROQ_RATE_LIMITED_UNTIL:
        max_tokens = 0

    # Execute Groq API Call if permitted
    if max_tokens > 0 and not remember_template_reply:
        max_tokens = min(max_tokens, GROQ_REMAINING_TPD)
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
                    GROQ_RATE_LIMITED_UNTIL = time.monotonic() + 60.0
                    reply = "🥣 Oatmeal AI systems are experiencing high traffic right now."
                else:
                    reply = "🥣 Oatmeal AI systems are experiencing high traffic right now."
        except Exception:
            reply = "🥣 Oatmeal AI operational."

    # Handle Media Generation
    media_url, embed = "", None
    if remember_template_reply:
        media_url = remember_media_url
        embed = remember_embed
    else:
        media_tiers = {1, 2, 3, 4, 5, 6, 7, 9}
        should_attach_media = job.priority_tier in media_tiers and not search_context
        if should_attach_media:
            try:
                media_mode = "custom" if job.priority_tier in (1, 2, 4, 7, 9) and prompt_text else "random"
                media_url, embed = await BiMediaGenerator.generate(session, media_mode, prompt_text)
            except Exception as media_err:
                print(f"[BIMEDIA ERROR] {media_err}")

    # Build Payload and Dispatch
    content, kwargs = await build_payload(reply, media_url, embed)
    if not content and not kwargs.get("embed"):
        content = "🥣"

    dispatch_success = False
    for attempt in range(3):
        try:
            if job.reference_message:
                await job.reference_message.reply(content=content, mention_author=False, **kwargs)
            else:
                await job.channel.send(content=content, **kwargs)
            dispatch_success = True
            break
        except discord.HTTPException as http_err:
            if http_err.status == 429:
                await asyncio.sleep(2.0 * (attempt + 1))
            else:
                break
        except Exception:
            await asyncio.sleep(0.5 * (2 ** attempt))
            
    if dispatch_success:
        if job.reference_message and hasattr(job.reference_message, 'id'):
            replied_message_ids.add(job.reference_message.id)
            pending_message_ids.discard(job.reference_message.id)
        if job.priority_tier == 4 and job.reference_message:
            p4_cursors[str(job.channel.id)] = job.reference_message.id
        trigger_save_state()
    else:
        if job.reference_message and hasattr(job.reference_message, "id"):
            pending_message_ids.discard(job.reference_message.id)

# ============================================================
# 13. ALL 9 BACKGROUND PRIORITY COROUTINES (P2 - P9)
# ============================================================

async def execute_priority_2():
    """Background priority 2: Periodic customized media replies to active members."""
    await bot.wait_until_ready()
    while not bot.is_closed():
        await asyncio.sleep(random.uniform(900, 2200))
        try:
            for guild in list(bot.guilds):
                channels = [c for c in guild.text_channels if guild.me and c.permissions_for(guild.me).send_messages]
                if channels:
                    ch = random.choice(channels)
                    async for msg in ch.history(limit=20):
                        if not msg.author.bot and p2_key(guild.id, msg.author.id) not in p2_replied_member_ids:
                            url, embed = await BiMediaGenerator.generate(HTTP_SESSION, "custom", msg.content or "")
                            content, kwargs = await build_payload("🥣", url, embed)
                            await msg.reply(content=content, mention_author=False, **kwargs)
                            p2_replied_member_ids.add(p2_key(guild.id, msg.author.id))
                            trigger_save_state()
                            break
                    break
        except Exception as e:
            print(f"[P2 ERROR] {e}")


async def execute_priority_3():
    """Background priority 3: Periodic personalized greetings to guild members."""
    await bot.wait_until_ready()
    while not bot.is_closed():
        await asyncio.sleep(random.uniform(1600, 4200))
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
                    
                members = sorted(members, key=lambda x: x.name.lower())
                idx = p3_member_indices.get(str(guild.id), 0) % len(members)
                target = members[idx]
                
                channels = [c for c in guild.text_channels if guild.me and c.permissions_for(guild.me).send_messages]
                if channels:
                    ch = random.choice(channels)
                    url, embed = await BiMediaGenerator.generate(HTTP_SESSION, "random")
                    content, kwargs = await build_payload(f"@{target.name} Salutations from the Arnold Attic.", url, embed)
                    await ch.send(content=content, **kwargs)
                    p3_member_indices[str(guild.id)] = (idx + 1) % len(members)
                    trigger_save_state()
                break
        except Exception as e:
            print(f"[P3 ERROR] {e}")


async def execute_priority_4():
    """Background priority 4: Live reaction to new chat messages with search & media precedence."""
    await bot.wait_until_ready()
    while not bot.is_closed():
        spent = traffic_engine.spent_fraction
        base_min = 15 + int(spent * 60)
        base_max = 35 + int(spent * 120)
        await asyncio.sleep(random.uniform(base_min, base_max))
        
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
                        is_eligible = (
                            not msg.author.bot 
                            and bool(msg.content)
                            and msg.id not in replied_message_ids 
                            and msg.id not in pending_message_ids
                        )
                        
                        if is_eligible:
                            pending_message_ids.add(msg.id)
                            clean_content = RE_CLEAN_MENTIONS.sub('', msg.content).strip()
                            if not clean_content:
                                clean_content = msg.content.strip()

                            lower_content = clean_content.lower()
                            has_intent = any(w in lower_content for w in ("remember", "search", "google", "find", "look", "turtles"))
                            
                            prompt_payload = clean_content if has_intent else f"Quick witty take: '{clean_content[:110]}'"

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
            print(f"[P4 ERROR] {e}")


async def execute_priority_5():
    """Background priority 5: Token-free inverse of Priority 2."""
    await bot.wait_until_ready()
    while not bot.is_closed():
        await asyncio.sleep(random.uniform(900, 2200))
        try:
            for guild in list(bot.guilds):
                channels = [c for c in guild.text_channels if guild.me and c.permissions_for(guild.me).send_messages]
                if channels:
                    ch = random.choice(channels)
                    async for msg in ch.history(limit=30, oldest_first=True):
                        if not msg.author.bot:
                            member_key = p2_key(guild.id, msg.author.id)
                            if member_key not in p5_replied_member_ids:
                                try:
                                    url, embed = await BiMediaGenerator.generate(HTTP_SESSION, "custom", msg.content or "")
                                    content, kwargs = await build_payload("🥣", url, embed)
                                    await msg.reply(content=content, mention_author=False, **kwargs)
                                    p5_replied_member_ids.add(member_key)
                                    trigger_save_state()
                                    break
                                except discord.HTTPException as http_err:
                                    if http_err.status in (404, 403):
                                        p5_replied_member_ids.add(member_key)
                                        trigger_save_state()
                                    break
                                except Exception:
                                    break
                    break
        except Exception as e:
            print(f"[P5 ERROR] {e}")


async def execute_priority_6():
    """Background priority 6: Standalone token-free random media broadcasting."""
    await bot.wait_until_ready()
    while not bot.is_closed():
        await asyncio.sleep(random.uniform(1000, 3200))
        try:
            for guild in list(bot.guilds):
                channels = [c for c in guild.text_channels if guild.me and c.permissions_for(guild.me).send_messages]
                if channels:
                    ch = random.choice(channels)
                    url, embed = await BiMediaGenerator.generate(HTTP_SESSION, "random")
                    content, kwargs = await build_payload("", url, embed)
                    await ch.send(content=content, **kwargs)
                    break
        except Exception as e:
            print(f"[P6 ERROR] {e}")


async def execute_priority_7():
    """Background priority 7: Cursor-based history scanning and customized media mirroring."""
    await bot.wait_until_ready()
    while not bot.is_closed():
        await asyncio.sleep(random.uniform(35, 120))
        try:
            for guild in list(bot.guilds):
                channels = [c for c in guild.text_channels if guild.me and c.permissions_for(guild.me).send_messages]
                for ch in channels:
                    ch_id_str = str(ch.id)
                    cursor = p7_cursors.get(ch_id_str, 0)
                    kwargs = {"limit": 30, "oldest_first": True}
                    if cursor > 0:
                        kwargs["after"] = discord.Object(id=cursor)
                    
                    target = None
                    async for msg in ch.history(**kwargs):
                        if msg.author == bot.user:
                            target = msg
                            break
                            
                    if target is None:
                        continue
                        
                    url, embed = await BiMediaGenerator.generate(HTTP_SESSION, "custom", target.content or "")
                    content, kwargs_payload = await build_payload("", url, embed)
                    try:
                        await target.reply(content=content, mention_author=False, **kwargs_payload)
                        p7_cursors[ch_id_str] = target.id
                        trigger_save_state()
                        break
                    except Exception:
                        continue
        except Exception as e:
            print(f"[P7 ERROR] {e}")


async def execute_priority_8():
    """Background priority 8: Cursor-based reflection jobs on historical bot messages."""
    await bot.wait_until_ready()
    while not bot.is_closed():
        await asyncio.sleep(random.uniform(50, 160))
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
                        if msg.author == bot.user:
                            target = msg
                            break
                            
                    if target is None:
                        continue
                    
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
    """Background priority 9: Channel recap generation from recent chat context."""
    await bot.wait_until_ready()
    while not bot.is_closed():
        await asyncio.sleep(random.uniform(2800, 5800))
        try:
            for guild in list(bot.guilds):
                channels = [c for c in guild.text_channels if guild.me and c.permissions_for(guild.me).send_messages and c.permissions_for(guild.me).read_message_history]
                if channels:
                    ch = random.choice(channels)
                    chat_lines = []
                    last_msg = None
                    async for msg in ch.history(limit=15):
                        if not msg.author.bot and msg.content:
                            chat_lines.append(f"{msg.author.display_name}: {msg.content[:60]}")
                            last_msg = msg
                    if chat_lines and last_msg:
                        job = PendingJob(
                            priority_tier=9,
                            timestamp=time.time(),
                            channel=ch,
                            prompt_text="Summarize this recent channel chat as an Oatmeal Recap.",
                            reference_message=last_msg
                        )
                        await delay_queue.put(job)
                        break
        except Exception as e:
            print(f"[P9 ERROR] {e}")
# ============================================================
# 14. WORKERS & BOT EVENTS
# ============================================================
async def delay_queue_worker():
    """Asynchronous worker that pulls and processes jobs from the delay queue with guaranteed task accounting."""
    global HTTP_SESSION
    while True:
        job = None
        try:
            job = await delay_queue.get()
            while time.monotonic() < GROQ_RATE_LIMITED_UNTIL:
                await asyncio.sleep(2)
            await process_job(job, HTTP_SESSION)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[WORKER ERROR] {e}")
            if job is not None and job.reference_message:
                pending_message_ids.discard(getattr(job.reference_message, "id", None))
            await asyncio.sleep(1)
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
        print("[SYSTEM] Reconnected to Discord Gateway. Existing background workers active.")
        return

    TASKS_INITIALIZED = True
    print("=" * 64)
    print(f" Oatmeal AI V101.0 Full Enterprise Monolith logged in as {bot.user}")
    print("=" * 64)
    load_state()

    # Initialize all background loops and atomic state persistence workers
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
async def on_message(message):
    """Event triggered on incoming channel messages, routing direct interactions to Priority 1."""
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
    print("[SYSTEM] Cleaned up HTTP session and background workers.")


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
        print("[SYSTEM] Shutdown requested by operator.")
