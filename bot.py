async def choose_media(prompt_text, priority_tier, channel):
    text = (prompt_text or "").lower()
    url = ""
    sticker = None

    wants_draw = any(k in text for k in ("draw", "sketch", "paint", "illustrat", "picture of", "image of", "art")) and not any(k in text for k in ("link", "article", "website", "page"))
    wants_audio = any(k in text for k in ("song", "music", "suno", "soundtrack", "track", "spotify", "soundcloud", "reverbnation", "podcast", "audio", "fm bot"))
    wants_video = any(k in text for k in ("youtube", "video", "clip", "watch"))
    wants_gif = any(k in text for k in ("gif", "meme", "reaction"))
    wants_sticker = "sticker" in text
    wants_link = any(k in text for k in ("link", "article", "website", "blog", "hive", "post"))

    if wants_link:
        # Post a real web link (e.g., your Hive blog or Twitter X) instead of a drawing
        web_links = [
            "https://hive.blog/@joeyarnoldvn",
            "https://x.com/Joeyarnoldvn"
        ]
        url = random.choice(web_links)
    elif wants_draw:
        url = pollinations_image_url(prompt_text)
    elif wants_audio:
        url = get_audio_embed_link(prompt_text)
        extra = await fetch_direct_youtube_video(prompt_text)
        if extra and random.random() < 0.5:
            url = extra + "\n" + url
    elif wants_video:
        url = await fetch_direct_youtube_video(prompt_text)
    elif wants_gif:
        url = await fetch_clean_tenor_gif(prompt_text)
    elif wants_sticker:
        sticker = await fetch_guild_sticker(channel)
    else:
        roll = random.random()
        usage = get_token_usage_ratio()
        
        # Balanced random roll: Reduced drawing weight, added web link fallback
        if roll < (0.25 - (usage * 0.1)):
            url = pollinations_image_url(prompt_text)
        elif roll < (0.50 - (usage * 0.1)):
            url = await fetch_clean_tenor_gif(prompt_text)
        elif roll < (0.75):
            url = await fetch_direct_youtube_video(prompt_text)
        elif roll < (0.90):
            url = "https://hive.blog/@joeyarnoldvn"
        else:
            sticker = await fetch_guild_sticker(channel)

    return url, sticker
