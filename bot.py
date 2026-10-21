# ============================================================
# 9. DISCORD EVENTS & BACKGROUND LOOPS (STRICT SEQUENCING)
# ============================================================
@bot.event
async def on_ready():
    print(f"Oatmeal AI logged in as {bot.user}")
    load_state()
    bot.loop.create_task(background_engagement_loop())

@bot.event
async def on_message(message):
    # ... (all your on_message code stays here) ...

async def next_historical_message(channel):
    # ... (all your next_historical_message code stays here) ...

async def next_reverse_historical_message(channel):
    # ... (all your next_reverse_historical_message code stays here) ...

# ==========================================================
# 1. DEFINE PRIORITY 4 FIRST (Right here above background loop)
# ==========================================================
async def priority_4_ambient_loop():
    """Track 3: Independent Priority 4 Genie Train with proportional infinite token availability scaling."""
    global last_genie_timestamp
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            await asyncio.sleep(10.0)
            
            metrics = get_trajectory_metrics()
            usage = metrics["usage"]
            titanic = metrics["titanic"]
            
            if time.monotonic() < DAILY_EXHAUSTED_UNTIL or time.monotonic() < GROQ_RATE_LIMITED_UNTIL:
                continue

            availability_factor = max(0.001, 1.0 - usage)
            dynamic_delay = (15.0 / availability_factor) + (titanic * 1800.0)
            
            if (time.time() - last_genie_timestamp) < dynamic_delay:
                continue

            react_chance = max(0.001, 0.20 * (availability_factor ** 3.0))
            if random.random() > react_chance:
                continue

            for guild in list(bot.guilds):
                active = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages]
                if active:
                    ch = random.choice(active)
                    rev = await next_reverse_historical_message(ch)
                    if rev:
                        last_genie_timestamp = time.time()
                        clean = re.sub(r"<@!?\d+>", "", rev.content or "").strip()
                        prompt = f"Live chat pending queue: '{clean[:140]}'. React as Joey in one short phrase."
                        await generate_and_send(ch, prompt, reference_message=rev, priority_tier=4, author_id=rev.author.id)
                        break
        except Exception as e:
            print(f"priority 4 track error: {e}")
            await asyncio.sleep(60)

# ==========================================================
# 2. THEN DEFINE BACKGROUND ENGAGEMENT LOOP BELOW IT
# ==========================================================
async def background_engagement_loop():
    global p2_complete
    await bot.wait_until_ready()
    
    # Launch Priority 4 (Genie) as a concurrent independent background track
    bot.loop.create_task(priority_4_ambient_loop())
    
    print("[BACKGROUND] Engagement loop started. Scanning for Priority 2 backlog...")
    
    while not bot.is_closed():
        try:
            now_mono = time.monotonic()
            usage = get_token_usage_ratio()

            if now_mono < DAILY_EXHAUSTED_UNTIL or now_mono < GROQ_RATE_LIMITED_UNTIL:
                await asyncio.sleep(30)
                continue

            if usage >= 0.93:
                await asyncio.sleep(80 + (usage - 0.93) * 2200)
                continue

            # Track 2: Priorities 2 & 3 (Sequential Backlog & Proactive Sweep)
            oldest = None
            if not p2_complete:
                for guild in list(bot.guilds):
                    channels = [c for c in guild.text_channels
                                if c.permissions_for(guild.me).send_messages
                                and c.permissions_for(guild.me).read_message_history]
                    for channel in channels:
                        msg = await next_historical_message(channel)
                        if msg and (oldest is None or msg.id < oldest.id):
                            oldest = msg

            if oldest is not None:
                clean = re.sub(r"<@!?\d+>", "", oldest.content or "").strip()
                print(f"[P2 BACKLOG] Processing historical message ID {oldest.id} in channel #{oldest.channel.name}")
                await generate_and_send(oldest.channel, clean or "What's up?", reference_message=oldest, priority_tier=2, author_id=oldest.author.id)
                await asyncio.sleep(calculate_dynamic_post_delay(2))
                continue
            elif not p2_complete:
                p2_complete = True
                save_state()
                print("[P2] COMPLETE — Backlog fully cleared. Unlocking Priority 3 proactive stream.")

            if p2_complete and usage < 0.88:
                if random.random() < max(0.03, 0.13 * (1.0 - usage)):
                    for guild in list(bot.guilds):
                        members = sorted(
                            [m for m in guild.members if not m.bot],
                            key=lambda x: x.display_name.lower()
                        )
                        active = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages]
                        if members and active:
                            target_member = members[0]
                            ch = random.choice(active)
                            prompt = "Drop a short proactive thought."
                            await generate_and_send(ch, prompt, target_member=target_member, priority_tier=3)
                            await asyncio.sleep(calculate_dynamic_post_delay(3))
                            break

            await asyncio.sleep(20)
        except Exception as e:
            print(f"background error: {e}")
            await asyncio.sleep(30)

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
