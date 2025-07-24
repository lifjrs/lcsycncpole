from flask import Flask, request, jsonify
from datetime import datetime
import json
import os

app = Flask(__name__)

# Optional: store events in memory (reset on restart)
event_log = []

# Or: persist to file (append-only)
DATA_FILE = "webhook_events.jsonl"

@app.route("/webhook/languagecloud", methods=["POST"])
def receive_webhook():
    try:
        data = request.json
        if not data:
            return "Invalid JSON", 400

        print(f"[{datetime.now()}] 🔔 Webhook received: {data.get('eventType')}")

        # Save to in-memory list
        event_log.append(data)

        # Optionally: Append to file
        with open(DATA_FILE, "a") as f:
            f.write(json.dumps(data) + "\n")

        return jsonify({"status": "received"}), 200

    except Exception as e:
        print(f"❌ Error handling webhook: {e}")
        return "Internal Server Error", 500


@app.route("/webhook/export", methods=["GET"])
def export_webhook_data():
    events = []

    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            for line in f:
                try:
                    events.append(json.loads(line.strip()))
                except json.JSONDecodeError:
                    continue
    else:
        events = event_log  # fallback to memory

    return jsonify(events), 200


@app.route("/", methods=["GET"])
def health_check():
    return "✅ Webhook server is running", 200


if __name__ == "__main__":
    app.run(debug=True)
