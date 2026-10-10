import os
import threading
from flask import Flask

# 1. Start Flask immediately
app = Flask(__name__)


@app.route("/")
def home():
  return "Oatmeal AI is running!"


def run_web():
  port = int(os.environ.get("PORT", 10000))
  app.run(host="0.0.0.0", port=port, use_reloader=False)


threading.Thread(target=run_web, daemon=True).start()

# 2. Check Environment Variables
discord_token = os.environ.get("DISCORD_TOKEN")
gemini_key = os.environ.get("GEMINI_API_KEY")

print(
    f"[DIAGNOSTIC] DISCORD_TOKEN is set: {bool(discord_token)} (Length:"
    f" {len(discord_token) if discord_token else 0})"
)
print(
    f"[DIAGNOSTIC] GEMINI_API_KEY is set: {bool(gemini_key)} (Length:"
    f" {len(gemini_key) if gemini_key else 0})"
)

if not discord_token or not gemini_key:
  print(
      "[CRITICAL ERROR] Missing environment variables in Render dashboard!"
  )
else:
  print("[DIAGNOSTIC] All environment variables present.")
