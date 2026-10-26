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
# 2. THREAD-SAFE BOUNDED MEMORY STRUCTURES & QUOTA GOVERNOR (UPGRADED)
# ============================================================
class BoundedSet:
    """
    Thread-safe bounded set with O(1) additions, deletions, snapshotting, 
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
        """Remove an item in O(1) time if present."""
        with self._lock:
            self._map.pop(item, None)

    def clear(self) -> None:
        """Clear all items safely under lock."""
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

    def __repr__(self) -> str:
        with self._lock:
            return f"BoundedSet(len={len(self._map)}, maxsize={self.maxsize})"


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
    """
    Resets daily remaining TPD, TPM, RPD quotas and synchronizes traffic engine 
    token counters if the UTC calendar day has advanced.
    """
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
                traffic_engine.congestion_penalty = 0.0
                traffic_engine.volume_level = 1.0
                
        logger.info("[QUOTA] Daily token trackers and volume controller reset for new UTC day: %s", current_utc_day)

groq_api_lock = asyncio.Lock()
state_io_lock = asyncio.Lock()
ACTIVITY_PAUSE = 45
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
# 4. GOOGLE MAPS TRAFFIC ENGINE & PID MICRO-TOKEN GOVERNOR (UPGRADED)
# ============================================================
class GoogleMapsTrafficEngine:
    """
    Closed-loop adaptive traffic controller inspired by network congestion control protocols.
    Dynamically throttles token allocations using error-feedback from API latencies,
    rate-limit (429) events, and quota burn rates to maintain optimal flow without hard cliffs.
    Integrated with structured enterprise logging.
    """
    def __init__(self, daily_quota: int = 200000, target_spent_pct: float = 0.20):
        self.daily_quota = max(1, daily_quota)
        self.target_spent_pct = target_spent_pct
        self.local_daily_tokens = 0
        self.volume_level = 1.0
        self.congestion_penalty = 0.0  # Integral error term for real-time 429 / congestion feedback
        self._lock = threading.RLock()
        
        self.priority_hierarchy = {
            1: {"dsn": 50, "dsl": 60, "mode": "MICRO-TOKEN ABSOLUTE-OVERRIDE: THINK + MEDIA"},
            4: {"dsn": 40, "dsl": 50, "mode": "MICRO-TOKEN ABSOLUTE-OVERRIDE: LIVE CHAT THINK"},
            6: {"dsn": 0,  "dsl": 0,  "mode": "TOKEN-FREE RANDOM MEDIA"},
            7: {"dsn": 0,  "dsl": 0,  "mode": "TOKEN-FREE MEDIA MIRROR"},
            8: {"dsn": 30, "dsl": 40, "mode": "MICRO-TOKEN REFLECTION"},
            9: {"dsn": 40, "dsl": 50, "mode": "MICRO-TOKEN RECAP"},
            2: {"dsn": 0,  "dsl": 0,  "mode": "TOKEN-FREE MEDIA"},
            5: {"dsn": 0,  "dsl": 0,  "mode": "TOKEN-FREE INVERSE MEDIA"},
            3: {"dsn": 0,  "dsl": 0,  "mode": "TOKEN-FREE GREETING"}
        }

    @property
    def spent_fraction(self) -> float:
        with self._lock:
            return max(0.0, min(1.0, self.local_daily_tokens / self.daily_quota))

    def get_daily_tokens(self) -> int:
        with self._lock:
            return self.local_daily_tokens

    def report_congestion(self, severity: float = 0.25):
        """Injects real-time congestion feedback (e.g., on HTTP 429 or timeouts) to damp volume fluidly."""
        with self._lock:
            self.congestion_penalty = min(1.0, self.congestion_penalty + severity)
            logger.warning("[TRAFFIC ENGINE] Congestion reported (Severity: %.2f). New penalty term: %.2f", severity, self.congestion_penalty)
            self.update_navigation()

    def update_navigation(self):
        """
        Adaptive PID-style flow control: calculates target volume based on quota burn fraction
        and transient congestion penalties, smoothing transitions using a low-pass filter momentum.
        """
        with self._lock:
            s = self.spent_fraction
            # Gradually decay transient congestion penalties over successive ticks
            self.congestion_penalty = max(0.0, self.congestion_penalty - 0.06)
            
            # Non-linear quota pressure curve starting gracefully past 50% daily quota consumption
            quota_pressure = 0.0 if s < 0.5 else (s - 0.5) * 2.0
            
            # Target flow rate blending quota pressure and active congestion penalties
            target = max(0.2, 1.0 - (quota_pressure * 0.6) - self.congestion_penalty)
            
            # Exponential Moving Average (EMA) momentum filter to eliminate abrupt scaling cliffs
            old_vol = self.volume_level
            self.volume_level += (target - self.volume_level) * 0.25
            self.volume_level = max(0.2, min(1.0, self.volume_level))
            
            if abs(self.volume_level - old_vol) > 0.05:
                logger.debug("[TRAFFIC ENGINE] Volume level adjusted: %.2f (Spent Fraction: %.2f)", self.volume_level, s)

    def get_allocation(self, priority_tier: int) -> Tuple[int, int, str]:
        base = self.priority_hierarchy.get(priority_tier, {"dsn": 30, "dsl": 50, "mode": "MICRO-TOKEN DEFAULT"})
        S = self.spent_fraction
        
        # Priority tiers 1 and 4 (Live Human Interaction) maintain protected operational floors
        if priority_tier in (1, 4):
            with self._lock:
                vol = max(0.5, self.volume_level)
            return int(base["dsn"] * vol), int(base["dsl"] * vol), base["mode"]

        # Automatic graceful lockdown for background queues if quota nears critical capacity or high congestion
        if S >= 0.95 or self.volume_level <= 0.22:
            return 0, 0, "QUOTA LOCKDOWN (CONGESTED / TOKEN-FREE ONLY)"

        with self._lock:
            vol = self.volume_level
        return int(base["dsn"] * vol), int(base["dsl"] * vol), base["mode"]

    def record_expenditure(self, tokens: int):
        global GROQ_REMAINING_TPD
        spent = max(0, int(tokens or 0))
        if spent <= 0:
            return
        with self._lock:
            self.local_daily_tokens += spent
            GROQ_REMAINING_TPD = max(0, GROQ_REMAINING_TPD - spent)
        self.update_navigation()

traffic_engine = GoogleMapsTrafficEngine(daily_quota=GROQ_MAX_TPD)


# ============================================================
# 5. ASYNCHRONOUS SINGLE-WRITER STATE PERSISTENCE & ROLLING BACKUP ENGINE (UPGRADED)
# ============================================================
def trigger_save_state():
    """Non-blocking trigger to request a coalesced state save operation."""
    try:
        state_save_queue.put_nowait(True)
    except Exception:
        pass

def load_state():
    """
    Loads persistent state with cryptographic SHA-256 checksum verification,
    multi-generation rolling backup fallback recovery, and integer key normalization.
    """
    global channel_cursors, p4_cursors, p7_cursors, p8_cursors, blocked_users, p3_member_indices
    state_path_str = str(STATE_PATH)
    
    # Candidate files ordered by priority: Primary -> Rolling Backups (.bak.1, .bak.2, .bak.3)
    candidates = [state_path_str] + [f"{state_path_str}.bak.{i}" for i in range(1, 4)] + [state_path_str + ".bak"]
    
    target_path = None
    for path in candidates:
        if os.path.exists(path):
            target_path = path
            break
            
    if not target_path:
        logger.info("[STATE] No existing state file or backups found at %s. Starting fresh.", state_path_str)
        return

    def _to_int_keys(d):
        normalized = {}
        for k, v in d.items():
            try:
                normalized[int(k)] = v
            except ValueError:
                normalized[k] = v
        return normalized

    def _verify_and_load(path):
        with open(path, "r", encoding="utf-8") as f:
            raw_content = f.read()
            
        try:
            container = json.loads(raw_content)
            if isinstance(container, dict) and "payload" in container and "checksum" in container:
                payload_str = json.dumps(container["payload"], sort_keys=True, ensure_ascii=False)
                expected_hash = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()
                if container["checksum"] != expected_hash:
                    raise ValueError("SHA-256 integrity checksum mismatch detected.")
                return container["payload"]
            elif isinstance(container, dict):
                return container
        except json.JSONDecodeError as jde:
            raise ValueError(f"Invalid JSON format: {jde}")
        raise ValueError("Invalid state structure root.")

    loaded_data = None
    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            loaded_data = _verify_and_load(path)
            target_path = path
            logger.info("[STATE] Successfully verified and loaded state from %s", target_path)
            break
        except Exception as err:
            logger.warning("[STATE WARNING] Failed to load/verify candidate %s: %s", path, err)

    if not loaded_data:
        logger.error("[STATE ERROR] All primary and rolling backup candidates failed verification. Starting fresh.")
        return

    try:
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
    except Exception as parse_err:
        logger.error("[STATE] Error populating memory structures from loaded state: %s", parse_err)

async def state_writer_worker():
    """
    Single-writer background worker featuring debounced batching, multi-generation
    backup rotation, SHA-256 integrity wrapping, and robust task accounting.
    """
    state_path_str = str(STATE_PATH)
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
                
                # Wrap with cryptographic checksum integrity container
                payload_str = json.dumps(payload, sort_keys=True, ensure_ascii=False)
                checksum = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()
                container = {
                    "version": 2,
                    "checksum": checksum,
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
                        
                        # Rotate multi-generation backups (.bak.3 <- .bak.2 <- .bak.1 <- primary)
                        bak_path_1 = state_path_str + ".bak.1"
                        bak_path_2 = state_path_str + ".bak.2"
                        bak_path_3 = state_path_str + ".bak.3"
                        
                        if os.path.exists(bak_path_2):
                            if os.path.exists(bak_path_3):
                                os.remove(bak_path_3)
                            os.replace(bak_path_2, bak_path_3)
                        if os.path.exists(bak_path_1):
                            os.replace(bak_path_1, bak_path_2)
                        if os.path.exists(state_path_str):
                            os.replace(state_path_str, bak_path_1)
                            
                        # Promote temporary file to primary state path atomically
                        os.replace(temp_path, state_path_str)
                    except Exception as e:
                        try:
                            if os.path.exists(temp_path):
                                os.remove(temp_path)
                        except OSError:
                            pass
                        logger.error("[STATE] Atomic write rotation failed: %s", e)

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
# 6. ASYNCHRONOUS TELEMETRY SERVER & METRICS DASHBOARD (UPGRADED)
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
    """Initializes and runs the aiohttp telemetry and health check web server with full lifecycle control and structured logging."""
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
            logger.info("[TELEMETRY] Web server successfully stopped and port released.")
        except Exception as cleanup_err:
            logger.error("[TELEMETRY CLEANUP ERROR] %s", cleanup_err)

def start_instant_web_server():
    """Start telemetry in one tracked daemon thread; safe to call once."""
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
# 7. PLATFORM-NATIVE BIMEDIA GENERATOR (UPGRADED)
# ============================================================
class BiMediaGenerator:
    """
    Advanced multi-category media engine protected by an enterprise async lock,
    featuring background asset prefetching, semantic intent mapping, cascading fallbacks,
    and structured enterprise logging.
    """
    
    _media_lock = asyncio.Lock()
    _served_history: collections.deque = collections.deque(maxlen=250)
    _prefetch_buffer: asyncio.Queue = asyncio.Queue(maxsize=30)
    
    FALLBACK_VIDEOS = [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=9bZkp7q19f0",
        "https://www.youtube.com/watch?v=hTWKbfoikeg",
        "https://www.youtube.com/watch?v=3JWTaaS7LWo",
        "https://www.youtube.com/watch?v=astJSke0iv8",
        "https://www.youtube.com/watch?v=y6120QOlsfU",
        "https://www.youtube.com/watch?v=L_LUpnjgPso",
        "https://www.youtube.com/watch?v=5NV6Rdv1a3I"
    ]
    FALLBACK_GIFS = [
        "https://media.tenor.com/C7K9w4K4q1wAAAAC/oatmeal-cat.gif",
        "https://media.tenor.com/images/9a8b1a7f36c64b58e6587424c55d0a69/tenor.gif",
        "https://media.tenor.com/v2/g_preview?itemref=17562854",
        "https://media.tenor.com/images/5d326117565155f9e710d0a2f7c0a1b2/tenor.gif",
        "https://media.tenor.com/gUiu1zyw-AYAAAAC/cat-vibing.gif",
        "https://media.tenor.com/Y1U4Xy2w268AAAAC/cat-funny.gif"
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

    @classmethod
    async def search_youtube_api(cls, session: aiohttp.ClientSession, query: str) -> List[str]:
        """Token-free YouTube public search via Piped API with timeout guards and structured logging."""
        cleaned = re.sub(r'\s+', ' ', RE_CLEAN_SYMBOLS.sub(' ', RE_CLEAN_SEARCH.sub('', RE_CLEAN_MENTIONS.sub('', query)))).strip()
        q = urllib.parse.quote(cleaned[:100] or "90s retro commercial")
        url = f"https://pipedapi.kavin.rocks/search?q={q}&filter=videos"
        try:
            async with session.get(url, headers={"User-Agent": "OatmealBot/102.0"}, timeout=4) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    urls = []
                    for item in data.get("items", []):
                        if "url" in item:
                            vid_id = item["url"].split("=")[-1]
                            urls.append(f"https://www.youtube.com/watch?v={vid_id}")
                    return urls
        except Exception as err:
            logger.debug("[BIMEDIA] YouTube Piped search query failed: %s", err)
        return []

    @classmethod
    async def search_tenor_api(cls, session: aiohttp.ClientSession, query: str) -> List[str]:
        """Token-free Tenor public GIF search query with timeout guards."""
        cleaned = re.sub(r'\s+', ' ', RE_CLEAN_SYMBOLS.sub(' ', RE_CLEAN_SEARCH.sub('', RE_CLEAN_MENTIONS.sub('', query)))).strip()
        q = urllib.parse.quote(cleaned[:80] or "retro reaction")
        url = f"https://tenor.googleapis.com/v2/search?q={q}&key=LIVDSRZULELA&limit=25&media_filter=gif"
        try:
            async with session.get(url, timeout=4) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    urls = []
                    for item in data.get("results", []):
                        media = item.get("media_formats", {})
                        if "gif" in media:
                            urls.append(media["gif"]["url"])
                    return urls
        except Exception as err:
            logger.debug("[BIMEDIA] Tenor search query failed: %s", err)
        return []

    @classmethod
    async def search_wikipedia_native(cls, session: aiohttp.ClientSession, query: str) -> List[str]:
        """Token-free Wikipedia opensearch article query with timeout guards."""
        cleaned = RE_CLEAN_MENTIONS.sub('', query)
        query_str = urllib.parse.quote(re.sub(r'\s+', ' ', cleaned).strip()[:60] or "1990s")
        url = f"https://en.wikipedia.org/w/api.php?action=opensearch&search={query_str}&limit=10&namespace=0&format=json"
        try:
            async with session.get(url, headers={"User-Agent": "OatmealBot/102.0"}, timeout=4) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, list) and len(data) > 3 and isinstance(data[3], list):
                        return data[3]
        except Exception as err:
            logger.debug("[BIMEDIA] Wikipedia search query failed: %s", err)
        return []

    @classmethod
    def _select_random_asset(cls, pool: List[str]) -> str:
        """Picks a random item from a pool, heavily preferring unseen items."""
        if not pool:
            return ""
        unseen = [item for item in pool if item not in cls._served_history]
        chosen = random.choice(unseen) if unseen else random.choice(pool)
        cls._served_history.append(chosen)
        return chosen

    @classmethod
    async def fetch_media_by_type(cls, session: aiohttp.ClientSession, idx: int, query: str = "") -> Tuple[str, Optional[discord.Embed]]:
        """Resolves media for category `idx` with intelligent category cascading and pre-filtered query words."""
        base_query = query if query else ""
        cascade_attempts = 0

        while cascade_attempts < 3:
            cascade_attempts += 1

            # 1. VIDEOS
            if idx == 1:
                results = await cls.search_youtube_api(session, base_query)
                pool = results if results else cls.FALLBACK_VIDEOS
                return cls._select_random_asset(pool), None

            # 2. GIFs
            elif idx == 2:
                results = await cls.search_tenor_api(session, base_query)
                pool = results if results else cls.FALLBACK_GIFS
                gif_url = cls._select_random_asset(pool)
                emb = discord.Embed()
                emb.set_image(url=gif_url)
                return "", emb

            # 3. PHOTOS
            elif idx == 3:
                seed_val = urllib.parse.quote(base_query if base_query else str(random.randint(100, 999999)))
                photo_url = f"https://picsum.photos/seed/{seed_val}/640/360.jpg"
                emb = discord.Embed()
                emb.set_image(url=photo_url)
                return "", emb

            # 4. DRAWINGS / AI ART
            elif idx == 4:
                art_prompt = f"{base_query} 90s retro cartoon masterwork" if base_query else "synthwave robot artwork"
                art_url = f"https://pollinations.ai/p/{urllib.parse.quote(art_prompt)}?width=640&height=360&seed={random.randint(1, 999999)}"
                emb = discord.Embed()
                emb.set_image(url=art_url)
                return "", emb

            # 5. AUDIO
            elif idx == 5:
                return cls._select_random_asset(cls.FALLBACK_AUDIOS), None

            # 6. WEB LINKS
            elif idx == 6:
                results = await cls.search_wikipedia_native(session, base_query)
                pool = results if results else cls.FALLBACK_LINKS
                return cls._select_random_asset(pool), None

            # 7. ASCII ART
            elif idx == 7:
                ascii_templates = [
                    "```\n  (o.o)  Oatmeal AI V102.0\n  / | \\\n _/\\_/\\_\n```",
                    "```\n  /\\_/\\\n ( o.o )  Arnold Attic\n  > ^ <\n```",
                    "```\n    ____         _                  _ \n  / __|___   _| |_ _ __  ___ _ _  | |\n | (__/ _ \\(_-<  _| '  \\/ -_) '_| |_|\n  \\___\\___/ /_/\\__|_|_|_\\___|_|     (_)\n```"
                ]
                return random.choice(ascii_templates), None

            # 8. EMOTICONS
            elif idx == 8:
                return random.choice(["(ง'̀-'́)ง", "¯\\_(ツ)_/¯", "( ͡° ͜ʖ ͡°)", "(⌐■_■)", "(づ｡◕‿‿◕｡)づ", "ಠ_ಠ", "(ʘ__ʘ)"]), None

            # 9. STICKERS
            elif idx == 9:
                return random.choice(["⭐", "🥣", "🚀", "🕹️", "📼", "🔥", "💡", "🏆", "🎯"]), None

            break

        return random.choice(cls.FALLBACK_VIDEOS), None

    @classmethod
    async def generate(cls, session: aiohttp.ClientSession, mode: str = "random", chat_text: str = "") -> Tuple[str, Optional[discord.Embed]]:
        """
        Thread-safe and coroutine-safe entrypoint. Acquires the central media lock 
        to ensure sequential, one-at-a-time processing across all priorities and background tasks.
        """
        async with cls._media_lock:
            weighted_categories = [
                1, 1, 1, 1,  # Videos (Weight 4)
                2, 2, 2, 2,  # GIFs (Weight 4)
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

            return await cls.fetch_media_by_type(session, idx, query=chat_text if mode == "custom" else "")

# ============================================================
# 8. RESILIENT SEARCH SUBSYSTEM, INTENT PARSER & REMEMBER ENGINE (UPGRADED)
# ============================================================
_background_tasks: List[asyncio.Task] = []

class AsyncSearchCache:
    """
    Advanced thread-safe asynchronous search cache with O(1) LRU eviction,
    atomic single-flight request coalescing, multi-engine failover (DuckDuckGo Lite + Wikipedia API fallback),
    and structured enterprise logging.
    """
    def __init__(self, ttl: float = 300.0, max_size: int = 250):
        self.ttl = ttl
        self.max_size = max_size
        self._cache: collections.OrderedDict[str, Tuple[float, str]] = collections.OrderedDict()
        self._inflight: Dict[str, asyncio.Future] = {}
        self._lock = asyncio.Lock()

    async def _fetch_duckduckgo(self, session: aiohttp.ClientSession, query: str) -> str:
        url = "https://lite.duckduckgo.com/lite/"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Oabot/102.0"}
        try:
            async with session.post(url, data={'q': query}, headers=headers, timeout=8) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    clean_s = []
                    try:
                        from bs4 import BeautifulSoup
                        soup = BeautifulSoup(html, 'html.parser')
                        snippet_tags = soup.find_all(class_=re.compile(r'snippet', re.I))
                        if not snippet_tags:
                            snippet_tags = soup.find_all('td', class_=re.compile(r'result', re.I))
                        for tag in snippet_tags[:3]:
                            text = tag.get_text(strip=True)
                            if len(text) > 15:
                                clean_s.append(text)
                    except Exception as parse_err:
                        logger.debug("[SEARCH] BeautifulSoup HTML parse fallback triggered: %s", parse_err)

                    if not clean_s:
                        snippets = RE_SNIPPETS.findall(html)
                        if not snippets:
                            snippets = re.findall(r'<td[^>]*class=["\']result-snippet["\'][^>]*>(.*?)</td>', html, re.DOTALL)
                        for s in snippets[:3]:
                            stripped = RE_HTML_STRIP.sub('', s).strip()
                            stripped = stripped.replace("&amp;", "&").replace("&quot;", "\"").replace("&#39;", "'")
                            if len(stripped) > 15:
                                clean_s.append(stripped)
                    return "\n".join(f"- {s}" for s in clean_s) if clean_s else ""
        except Exception as ddg_err:
            logger.debug("[SEARCH] DuckDuckGo Lite query failed: %s", ddg_err)
        return ""

    async def _fetch_wikipedia_fallback(self, session: aiohttp.ClientSession, query: str) -> str:
        q_enc = urllib.parse.quote(query[:60])
        url = f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={q_enc}&format=json"
        try:
            async with session.get(url, headers={"User-Agent": "OatmealBot/102.0"}, timeout=5) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    search_results = data.get("query", {}).get("search", [])
                    snippets = []
                    for item in search_results[:3]:
                        title = item.get("title", "")
                        snippet = RE_HTML_STRIP.sub('', item.get("snippet", ""))
                        if snippet:
                            snippets.append(f"- [{title}] {snippet}")
                    return "\n".join(snippets)
        except Exception as wiki_err:
            logger.debug("[SEARCH] Wikipedia fallback search query failed: %s", wiki_err)
        return ""

    async def get_or_fetch(self, query: str, session: aiohttp.ClientSession) -> str:
        q_key = query.lower().strip()
        now = time.monotonic()

        future = None
        is_creator = False

        async with self._lock:
            if q_key in self._cache:
                ts, res = self._cache[q_key]
                if now - ts < self.ttl:
                    self._cache.move_to_end(q_key)
                    return res
                else:
                    self._cache.pop(q_key, None)

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

            # Primary engine: DuckDuckGo Lite
            res = await self._fetch_duckduckgo(session, cleaned)
            
            # Secondary fallback engine: Wikipedia Search API if DuckDuckGo yields nothing
            if not res:
                res = await self._fetch_wikipedia_fallback(session, cleaned)

            async with self._lock:
                if len(self._cache) >= self.max_size:
                    self._cache.popitem(last=False)
                self._cache[q_key] = (time.monotonic(), res)
                self._cache.move_to_end(q_key)
                self._inflight.pop(q_key, None)

            if not future.done():
                future.set_result(res)
            return res

        except Exception as e:
            logger.error("[SEARCH ERROR] %s", e)
            async with self._lock:
                self._inflight.pop(q_key, None)
            if not future.done():
                future.set_result("")
            return ""

search_cache = AsyncSearchCache()


def parse_intent_and_keyword(prompt_text: str) -> Tuple[str, str]:
    """
    Precision intent parser with upgraded phrase extraction and regex fallback cleaning
    backed by structured logging support.
    """
    if not prompt_text:
        return "chat", ""

    lower_text = prompt_text.lower().strip()
    intent = "chat"
    keyword = ""

    if any(lower_text.startswith(p) or f" {p}" in lower_text for p in ("remember", "recall")):
        intent = "remember"
        clean_phrase = re.sub(r'\b(remember|recall|what did you say about|what did|what was)\b', '', prompt_text, flags=re.IGNORECASE)
        keyword = clean_phrase.strip("?.,!:")
        if not keyword:
            keyword = prompt_text
    elif any(lower_text.startswith(p) or f" {p}" in lower_text for p in ("search", "lookup", "find", "price of", "what is")):
        intent = "search"
        clean_phrase = re.sub(r'\b(search|lookup|find|price of|what is)\b', '', prompt_text, flags=re.IGNORECASE)
        keyword = clean_phrase.strip("?.,!:") or prompt_text
    elif "think" in lower_text:
        intent = "think"
        clean_phrase = re.sub(r'\bthink\b', '', prompt_text, flags=re.IGNORECASE)
        keyword = clean_phrase.strip("?.,!:") or prompt_text
    else:
        stop_words = {
            "the", "a", "an", "in", "on", "at", "to", "for", "is", "are", 
            "about", "you", "any", "some", "me", "it", "this", "when", 
            "what", "where", "who", "how", "did", "do", "was", "we", "that",
            "remember", "think", "tell", "show", "find", "search", "lookup"
        }
        words = [w.strip("?.,!:") for w in prompt_text.split()]
        significant = [w for w in words if w.lower() not in stop_words and len(w) > 1]
        keyword = significant[0] if significant else (words[0] if words else "oatmeal")
    
    return intent, keyword.strip()


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
    Upgraded Phrase-Based Remember Template Engine:
    Performs concurrent asynchronous lookups across Discord history, biographical timeline outlines,
    multi-engine search caches, and token-free media generation with structured enterprise logging.
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

    # 1. Asynchronous Discord History Phrase Search
    async def fetch_history():
        matched_msg = None
        try:
            phrase_lower = search_phrase.lower()
            async for msg in channel.history(oldest_first=True, limit=300):
                if not msg.author.bot and msg.content:
                    if phrase_lower in msg.content.lower():
                        matched_msg = msg
                        break
        except Exception as hist_err:
            logger.error("[REMEMBER HISTORY ERROR] %s", hist_err)
        return matched_msg

    # 2. Asynchronous Internet Search Quote via Multi-Engine Cache
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

    # 3. Asynchronous Token-Free Media Generation
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
# 9. PAYLOAD BUILDER (UPGRADED)
# ============================================================
async def build_payload(content_str: str, media_url: str = "", embed: Optional[discord.Embed] = None) -> Tuple[str, dict]:
    """
    Formats text content, direct media URLs, and rich embeds for Discord dispatch, 
    ensuring robust multi-asset pairing, structured logging telemetry, and 
    foolproof prevention of Discord API empty payload exceptions.
    """
    kwargs: dict = {}
    final_content = content_str.strip() if content_str else ""
    sanitized_media = media_url.strip() if media_url else ""

    # Append media URL cleanly if present
    if sanitized_media:
        if final_content:
            final_content += f"\n{sanitized_media}"
        else:
            final_content = sanitized_media
            logger.debug("[PAYLOAD] Dispatched media-only payload: %s", sanitized_media)

    # Attach rich embed if provided
    if embed is not None:
        kwargs["embed"] = embed
        logger.debug("[PAYLOAD] Attached rich embed to message payload.")

    # Absolute fallback guard to prevent Discord HTTP 400 Empty Message exceptions
    if not final_content and not kwargs.get("embed"):
        final_content = "🥣 Oatmeal AI operational."
        logger.warning("[PAYLOAD WARNING] Empty payload detected. Applied operational fallback banner.")

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
# 11. STREAMLINED TIMELINE & REMEMBER TEMPLATE ENGINE (UPGRADED)
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
    Ultra-Robust Phrase-Based Remember Template Engine:
    Cleans conversational prefixes, performs concurrent async lookups across Discord history,
    biographical timeline outlines, internet search caches, and token-free media generation
    backed by structured enterprise logging.
    """
    if not prompt_text:
        return "", "", None

    # Clean prompt and strip conversational prefixes to isolate the target search phrase
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

    # 1. Background task for Discord History Phrase Search
    async def fetch_history():
        matched_msg = None
        try:
            phrase_lower = search_phrase.lower()
            async for msg in channel.history(oldest_first=True, limit=300):
                if not msg.author.bot and msg.content:
                    if phrase_lower in msg.content.lower():
                        matched_msg = msg
                        break
        except Exception as hist_err:
            logger.error("[REMEMBER HISTORY ERROR] %s", hist_err)
        return matched_msg

    # 2. Background task for Internet Search Quote via Cache
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

    # 3. Background task for Token-Free Media Generation
    async def fetch_media():
        try:
            return await BiMediaGenerator.generate(session, "custom", search_phrase)
        except Exception as media_err:
            logger.error("[REMEMBER MEDIA ERROR] %s", media_err)
            return "", None

    # Execute all asynchronous data lookups concurrently
    matched_msg, (internet_quote, internet_year), (media_url, embed) = await asyncio.gather(
        fetch_history(),
        fetch_internet_quote(),
        fetch_media()
    )

    # Timeline match extraction
    timeline_line = f"1985: Record for '{search_phrase}'."
    timeline_year = "1985"
    for line in FULL_ANNUAL_OUTLINE.strip().splitlines():
        if search_phrase.lower() in line.lower():
            timeline_line = line.strip()
            years_in_line = RE_YEARS.findall(line)
            if years_in_line:
                timeline_year = years_in_line[0]
                break

    # Construct final template reply
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
# 11.5 CENTRALIZED WORD MACHINE & ASSEMBLY LINE PIPELINE (UPGRADED)
# ============================================================

class WordMachine:
    """
    Centralized controller for prompt analysis, intent parsing, 
    and intelligent keyword extraction (Priority 2 & Priority 3 style).
    """
    STOP_WORDS = {
        "the", "a", "an", "in", "on", "at", "to", "for", "is", "are", 
        "about", "you", "me", "it", "this", "that", "what", "how", "why", 
        "who", "where", "when", "oatmeal", "joey", "arnold", "bot", "hey", 
        "please", "can", "and", "or", "but", "do", "did", "was", "we"
    }

    @classmethod
    def flushed_keywords(cls, text: str, max_words: int = 5) -> str:
        """Extracts up to the first N important keywords, stripping stop words and symbols."""
        if not text:
            return ""
        cleaned = RE_CLEAN_MENTIONS.sub('', text)
        cleaned = RE_CLEAN_SYMBOLS.sub(' ', cleaned)
        words = [w.strip("?.,!:") for w in cleaned.split()]
        important = [w for w in words if w.lower() not in cls.STOP_WORDS and len(w) > 1]
        return " ".join(important[:max_words]) if important else text[:30]

    @classmethod
    def process_ingress(cls, prompt_text: str) -> Tuple[str, str, str]:
        """
        Parses intent, generates keyword-extracted phrase (for token-free media/search),
        and preserves full clean content (for LLM think/chat).
        """
        intent, raw_keyword = parse_intent_and_keyword(prompt_text)
        keyword_phrase = cls.flushed_keywords(prompt_text, max_words=5)
        clean_content = RE_CLEAN_MENTIONS.sub('', prompt_text).strip() or prompt_text
        
        logger.debug("[WORD MACHINE] Ingress intent parsed: %s | Keywords: '%s'", intent, keyword_phrase)
        return intent, keyword_phrase, clean_content


class AssemblyLinePipeline:
    """
    Centralized single-job execution pipeline that routes requests through 
    the Word Machine and forwards them token-free or via LLM to the correct service module
    with structured logging and robust fault tolerance.
    """
    
    @staticmethod
    async def execute(job: PendingJob, session: aiohttp.ClientSession) -> Tuple[str, str, Optional[discord.Embed]]:
        prompt_text = job.prompt_text or ""
        sender_obj = job.reference_message.author if job.reference_message else None
        
        # Step 1: Pass through the Centralized Word Machine
        intent, keyword_phrase, clean_content = WordMachine.process_ingress(prompt_text)
        
        reply_text = ""
        media_url = ""
        embed = None
        search_context = ""
        timeline_snippet = ""

        # Step 2: Route to Specialized Services based on Intent & Tier
        try:
            # PRIORITY 3: TARGETED MEMBER MEDIA & MENTION (TOKEN-FREE, TEXT-FREE)
            if job.priority_tier == 3:
                logger.debug("[PIPELINE] Routing Priority 3 member media mention job.")
                reply_text = f"<@{job.author_id}>" if job.author_id else ""
                target_keywords = WordMachine.flushed_keywords(prompt_text, max_words=3)
                media_url, embed = await BiMediaGenerator.generate(session, "custom", target_keywords)

            # REMEMBER SERVICE (Token-Free local/archive lookup)
            elif intent == "remember" and job.priority_tier in (1, 4):
                logger.debug("[PIPELINE] Routing to Remember Template Engine (Tier %d).", job.priority_tier)
                reply_text, media_url, embed = await get_remember_template_reply(
                    job.channel, prompt_text, sender_obj, session
                )
            
            # THINK / SEARCH SERVICE (LLM or Web Search lookup)
            elif intent in ("think", "search") or job.priority_tier in (1, 4):
                logger.debug("[PIPELINE] Routing to Think/Search Service (Tier %d).", job.priority_tier)
                timeline_snippet = extract_relevant_timeline(keyword_phrase)
                if intent == "search" or any(w in prompt_text.lower() for w in ("search", "find", "price", "google", "what", "lookup")):
                    search_context = await search_cache.get_or_fetch(keyword_phrase, session)
                
                # If no template reply was generated, hand off to LLM (Think Service)
                if not reply_text:
                    reply_text = await AssemblyLinePipeline._dispatch_to_llm(
                        clean_content, timeline_snippet, search_context, job.priority_tier
                    )

            # PRIORITY 8: HISTORICAL REFLECTIONS (Self-Reply going back weeks via LLM)
            elif job.priority_tier == 8:
                logger.debug("[PIPELINE] Routing Priority 8 Historical Reflection.")
                past_text = job.prompt_text or "Looking back..."
                system_p = (
                    f"{SHORT_IDENTITY}\n\n"
                    f"CRITICAL CONSTRAINT: Reply with a clever, witty reflection on your past message in strict under 10 words. "
                    f"Never break character."
                )
                reply_text = await AssemblyLinePipeline._dispatch_to_llm_custom(
                    session, system_p, f"Reflect on this past message wittily: '{past_text}'", 8
                )

            # PRIORITY 9: DEEP HISTORICAL RECAPS (Spanning years via LLM)
            elif job.priority_tier == 9:
                logger.debug("[PIPELINE] Routing Priority 9 Deep Historical Recap.")
                recap_context = job.prompt_text or ""
                system_p = (
                    f"{SHORT_IDENTITY}\n\n"
                    f"CRITICAL CONSTRAINT: Recap these historical messages spanning years in strict under 10 words with sharp, witty insight."
                )
                reply_text = await AssemblyLinePipeline._dispatch_to_llm_custom(
                    session, system_p, f"Recap these historical messages:\n{recap_context}", 9
                )

            # PRIORITY 6: TOKEN-FREE WORD-DRIVEN MEDIA (NO TEXT)
            elif job.priority_tier == 6:
                logger.debug("[PIPELINE] Routing Priority 6 Token-Free Word-Driven Media.")
                reply_text = ""  # Strictly token-free and text-free
                media_query = job.prompt_text or "retro media"
                media_url, embed = await BiMediaGenerator.generate(session, "custom", media_query)
            
            # TOKEN-FREE MEDIA / BACKGROUND SERVICE (P2, P5, P7)
            else:
                logger.debug("[PIPELINE] Routing background token-free media (Tier %d).", job.priority_tier)
                reply_text = random.choice(ZERO_TOKEN_BANTER) if job.priority_tier != 2 else ""
                media_mode = "custom" if keyword_phrase else "random"
                media_url, embed = await BiMediaGenerator.generate(session, media_mode, keyword_phrase)

        except Exception as pipeline_err:
            logger.error("[PIPELINE ERROR] Assembly line failure on tier %d: %s", job.priority_tier, pipeline_err)
            reply_text = random.choice(ZERO_TOKEN_BANTER)

        return reply_text, media_url, embed

    @staticmethod
    async def _dispatch_to_llm(prompt_text: str, timeline_snippet: str, search_context: str, priority_tier: int) -> str:
        """Handles LLM generation with token capping, traffic engine integration, and dynamic rate-limit backoff."""
        global GROQ_RATE_LIMITED_UNTIL, GROQ_REMAINING_TPD
        
        dsn, dsl, mode = traffic_engine.get_allocation(priority_tier)
        max_tokens = min(dsl, 50) if dsn > 0 else 0
        
        if GROQ_REMAINING_TPD <= 0 or time.monotonic() < GROQ_RATE_LIMITED_UNTIL or max_tokens <= 0:
            logger.info("[LLM DISPATCH] Skipping API call due to quota limit or active rate-limit window. Using token-free fallback.")
            return random.choice(ZERO_TOKEN_BANTER)

        system_prompt = (
            f"{SHORT_IDENTITY}\n\n"
            f"CRITICAL CONSTRAINT: Reply in one single punchy sentence under 10 words. "
            f"Voice: rapid, playful, nostalgic 90s. Never break character."
        )
        if timeline_snippet:
            system_prompt += f"\n\nHistorical Context:\n{timeline_snippet}"
        if search_context:
            system_prompt += f"\n\nSearch Results:\n{search_context}"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt_text}
        ]

        max_tokens = min(max_tokens, GROQ_REMAINING_TPD)
        url = "https://api.groq.com/openai/v1/chat/completions"
        payload = {
            "model": MODEL_NAME,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.7
        }
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": "OatmealBot/102.0"
        }

        try:
            async with groq_api_lock:
                async with HTTP_SESSION.post(url, json=payload, headers=headers, timeout=12) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        reply = data["choices"][0]["message"]["content"].strip()
                        actual_tokens = data.get("usage", {}).get("total_tokens", max_tokens // 2)
                        traffic_engine.record_expenditure(actual_tokens)
                        logger.debug("[LLM SUCCESS] Spent %d tokens for tier %d request.", actual_tokens, priority_tier)
                        return reply
                    elif resp.status == 429:
                        retry_after = resp.headers.get("Retry-After")
                        backoff_delay = float(retry_after) if retry_after and retry_after.isdigit() else 60.0
                        GROQ_RATE_LIMITED_UNTIL = time.monotonic() + backoff_delay
                        traffic_engine.report_congestion(severity=0.4)
                        logger.warning("[LLM 429] Rate-limited by Groq API. Pausing LLM calls for %.1fs.", backoff_delay)
                    else:
                        logger.warning("[LLM API WARNING] Unexpected HTTP status %d received.", resp.status)
                        traffic_engine.report_congestion(severity=0.15)
        except Exception as err:
            logger.error("[LLM DISPATCH ERROR] Exception during Groq API execution: %s", err)
            traffic_engine.report_congestion(severity=0.3)
            
        return random.choice(ZERO_TOKEN_BANTER)

    @staticmethod
    async def _dispatch_to_llm_custom(session: aiohttp.ClientSession, system_prompt: str, user_prompt: str, priority_tier: int) -> str:
        """Handles custom LLM generation for background historical tiers with traffic engine governance."""
        global GROQ_RATE_LIMITED_UNTIL, GROQ_REMAINING_TPD
        
        dsn, dsl, mode = traffic_engine.get_allocation(priority_tier)
        max_tokens = min(dsl, 30) if dsn > 0 else 0
        
        if GROQ_REMAINING_TPD <= 0 or time.monotonic() < GROQ_RATE_LIMITED_UNTIL or max_tokens <= 0:
            return random.choice(ZERO_TOKEN_BANTER)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        max_tokens = min(max_tokens, GROQ_REMAINING_TPD)
        url = "https://api.groq.com/openai/v1/chat/completions"
        payload = {
            "model": MODEL_NAME,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.8
        }
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": "OatmealBot/102.0"
        }

        try:
            async with groq_api_lock:
                async with session.post(url, json=payload, headers=headers, timeout=12) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        reply = data["choices"][0]["message"]["content"].strip()
                        actual_tokens = data.get("usage", {}).get("total_tokens", max_tokens // 2)
                        traffic_engine.record_expenditure(actual_tokens)
                        return reply
                    elif resp.status == 429:
                        GROQ_RATE_LIMITED_UNTIL = time.monotonic() + 60.0
                        traffic_engine.report_congestion(severity=0.4)
        except Exception as err:
            logger.error("[LLM CUSTOM DISPATCH ERROR] %s", err)
            traffic_engine.report_congestion(severity=0.3)
            
        return random.choice(ZERO_TOKEN_BANTER)

# ============================================================
# 12. UNIFIED JOB EXECUTION & PIPELINE DISPATCH ENGINE (UPGRADED)
# ============================================================
async def process_job(job: PendingJob, session: aiohttp.ClientSession):
    """
    Razor-thin modular job execution engine:
    - Delegates core processing entirely to the centralized AssemblyLinePipeline.
    - Handles rate-limit aware message replies with exponential backoff and jitter.
    - Ensures bulletproof state tracking and pending message cleanup in a `finally` block.
    """
    check_and_reset_daily_quotas()
    if session is None or session.closed:
        logger.warning("[JOB] HTTP session unavailable; releasing pending message claim.")
        if job.reference_message:
            pending_message_ids.discard(getattr(job.reference_message, "id", None))
        return

    # 1. Execute through the Centralized Assembly Line Pipeline
    reply, media_url, embed = await AssemblyLinePipeline.execute(job, session)

    # 2. Build Payload and Dispatch
    content, kwargs = await build_payload(reply, media_url, embed)
    if not content and not kwargs.get("embed"):
        content = random.choice(ZERO_TOKEN_BANTER)

    dispatch_success = False
    for attempt in range(3):
        try:
            if job.reference_message:
                await job.reference_message.reply(content=content, mention_author=False, **kwargs)
            elif job.channel:
                await job.channel.send(content=content, **kwargs)
            dispatch_success = True
            logger.debug("[JOB] Successfully dispatched job tier %d to channel %s", job.priority_tier, getattr(job.channel, "id", "unknown"))
            break
        except discord.HTTPException as http_err:
            if http_err.status == 429:
                backoff = 2.0 * (attempt + 1) + random.uniform(0.1, 0.5)
                logger.warning("[JOB 429] Rate limited on dispatch attempt %d. Backing off for %.2fs", attempt + 1, backoff)
                await asyncio.sleep(backoff)
            else:
                logger.error("[JOB HTTP ERROR] Status %d: %s", http_err.status, http_err.text)
                break
        except Exception as dispatch_err:
            backoff = 0.5 * (2 ** attempt) + random.uniform(0.1, 0.3)
            logger.error("[JOB ERROR] Dispatch attempt %d failed: %s. Retrying in %.2fs", attempt + 1, dispatch_err, backoff)
            await asyncio.sleep(backoff)
            
    # 3. State Tracking & Cleanup
    try:
        if dispatch_success:
            if job.reference_message and hasattr(job.reference_message, 'id'):
                replied_message_ids.add(job.reference_message.id)
            if job.priority_tier == 4 and job.reference_message and job.channel:
                p4_cursors[str(job.channel.id)] = job.reference_message.id
            trigger_save_state()
    finally:
        if job.reference_message and hasattr(job.reference_message, "id"):
            pending_message_ids.discard(job.reference_message.id)
# ============================================================
# 13. UNIFIED BACKGROUND PRIORITY SCHEDULER (UPGRADED)
# ============================================================
async def background_priority_scheduler():
    """
    Unified background orchestrator managing priorities P2 through P9.
    All background tasks act purely as lightweight triggers, pushing jobs 
    onto the delay queue to be processed sequentially through the central assembly line,
    backed by enterprise structured logging.
    """
    await bot.wait_until_ready()
    
    # Dictionary-driven interval trackers and timing offsets
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
        7: random.uniform(45, 90),   # Tuned pacing for Priority 7 mirror replies
        8: random.uniform(120, 300), 
        9: random.uniform(1800, 3600)
    }

    while not bot.is_closed():
        await asyncio.sleep(10.0)  # Lightweight core tick loop
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
        # PRIORITY 7: Bot Media Mirror (Oldest-First Self-Reply with Media)
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
        # PRIORITY 8: Historical Reflections (Self-Reply going back weeks)
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
        # PRIORITY 2: Periodic Member Media (Text-Free, Keyword-Targeted via Word Machine)
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
                                    prompt_text=msg.content,
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
        # PRIORITY 3: Randomized Member Media & Mention (Token-Free)
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
                                        prompt_text=msg.content or "",
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
        # PRIORITY 6: Token-Free Word-Driven Media (Last 100 Messages, No Text)
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
                        
                        # Scan up to the last 100 messages in the channel
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
        # PRIORITY 9: Deep Historical Recaps (Spanning years)
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
# 14. WORKERS & BOT EVENTS (UPGRADED)
# ============================================================
async def delay_queue_worker():
    """
    Asynchronous worker that pulls and processes jobs from the delay queue,
    routing them securely through the centralized Assembly Line Pipeline
    with structured enterprise logging and robust error shielding.
    """
    global HTTP_SESSION
    while True:
        job = None
        try:
            job = await delay_queue.get()
            while time.monotonic() < GROQ_RATE_LIMITED_UNTIL:
                await asyncio.sleep(2.0)
            
            # Execute through the centralized assembly line pipeline
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
