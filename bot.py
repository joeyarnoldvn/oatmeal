@bot.event
async def on_message(message):
    global last_genie_timestamp
    if message.author.bot or message.guild is None or message.is_system():
        return
    lifetime_interacted_members.add(message.author.id)
    
    usage_ratio = get_token_usage_ratio()
    content_lower = message.content.lower()

    # --- 1. PRIORITY 1: DIRECT MENTIONS / REPLIES / NAMES FIRST ---
    mentioned = bot.user in message.mentions
    replied_to_bot = False
    if message.reference and isinstance(message.reference.resolved, discord.Message):
        replied_to_bot = message.reference.resolved.author == bot.user
    named = any(k in content_lower for k in ("joey", "arnold", "oatmeal", "joeyarnoldvn", "ojawall"))
    
    if mentioned or replied_to_bot or named:
        clean = re.sub(r"<@!?\d+>", "", message.content)
        clean = re.sub(r"<@&?\d+>", "", clean).strip()
        await generate_and_send(
            message.channel,
            clean or "Hello Oatmeal.",
            reference_message=message,
            priority_tier=1,
            author_id=message.author.id,
        )
        return  # Handled directly as Priority 1, stop processing here

    # --- 2. PRIORITY 4: FREED GENIE LIVE INTERCEPTION (Strictly < 20% Tokens + Cooldown) ---
    now = time.time()
    genie_cooldown_passed = (now - last_genie_timestamp) > 180  # 3 minutes cooldown between genie posts
    
    if usage_ratio < 0.20 and genie_cooldown_passed and random.random() < 0.12:
        last_genie_timestamp = now
        clean = re.sub(r"<@!?\d+>", "", message.content).strip()
        
        # Total creative freedom: single words, short snaps, or full rants
        genie_prompts = [
            f"Someone said: '{clean}'. React with a single punchy word like amazing, weird, awesome, cool, or no way.",
            f"Someone said: '{clean}'. Give your authentic freestyle thought, a quick memory, or a fun twist.",
            f"Someone said: '{clean}'. Drop a casual reaction as Joey Arnold."
        ]
        chosen_prompt = random.choice(genie_prompts)
        
        # 50% chance to reply directly to the message or drop as an independent channel post
        ref_msg = message if random.random() < 0.5 else None
        
        await generate_and_send(
            message.channel,
            chosen_prompt,
            reference_message=ref_msg,
            priority_tier=4,
            author_id=message.author.id,
        )
        return
