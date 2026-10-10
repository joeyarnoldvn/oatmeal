import os
from flask import Flask

app = Flask(__name__)


@app.route("/")
def home():
  return "Flask is alive!"


if __name__ == "__main__":
  port = int(os.environ.get("PORT", 10000))
  print(f"Starting test server on port {port}...")
  app.run(host="0.0.0.0", port=port)
