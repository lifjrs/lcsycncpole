import json
import os
import threading
import logging
from datetime import datetime, timezone
from flask import Flask, request, jsonify

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# --- Config ---
DATA_FILE = "webhook_events.jsonl"
MAX_CONTENT_BYTES = 1 * 1024 * 1024  # 1 MB per webhook payload limit

# Thread lock — one lock shared across all threads in a worker process.
# Prevents concurrent requests from interleaving writes to the .jsonl file,
# which would corrupt lines and cause JSONDecodeError on export.
_file_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/webhook/languagecloud", methods=["POST"])
def receive_webhook():
    """
    Receives incoming webhook POSTs from Language Cloud.

    Key fixes vs original:
    - Content-Length guard rejects oversized payloads before parsing.
    - File write is protected by a threading.Lock so concurrent Gunicorn
      threads can't interleave partial writes onto the same line.
    - Timestamps added at receipt time for audit trail.
    - debug=True removed so Flask doesn't re-parse or double-log in production.
    """
    # Guard: reject payloads that are too large before even parsing JSON
    content_length = request.content_length
    if content_length and content_length > MAX_CONTENT_BYTES:
        logger.warning("Rejected oversized payload: %d bytes", content_length)
        return jsonify({"error": "Payload too large"}), 413

    data = request.get_json(silent=True)
    if not data:
        logger.warning("Received invalid or empty JSON body")
        return jsonify({"error": "Invalid JSON"}), 400

    event_type = data.get("eventType", "UNKNOWN")
    event_id = data.get("eventId", "no-id")

    # Stamp server-side receipt time — useful for diagnosing delay between
    # Language Cloud sending and this service actually receiving
    data["_receivedAt"] = datetime.now(timezone.utc).isoformat()

    logger.info("Webhook received: %s (eventId: %s)", event_type, event_id)

    # Thread-safe file append
    # Lock ensures each json.dumps(...) + "\n" lands as one atomic line
    try:
        with _file_lock:
            with open(DATA_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(data) + "\n")
    except OSError as e:
        logger.error("Failed to write event %s to file: %s", event_id, e)
        return jsonify({"error": "Storage failure"}), 500

    return jsonify({"status": "received", "eventId": event_id}), 200


@app.route("/webhook/export", methods=["GET"])
def export_webhook_data():

    temp_file = f"{DATA_FILE}.processing"

    with _file_lock:
        if not os.path.exists(DATA_FILE):
            return jsonify([]), 200

        os.rename(DATA_FILE, temp_file)

        # create fresh file immediately
        open(DATA_FILE, "a").close()

    events = []

    with open(temp_file, "r", encoding="utf-8") as f:
        for line in f:
            try:
                events.append(json.loads(line))
            except:
                pass

    os.remove(temp_file)

    logger.info("Exported %d events", len(events))
    return jsonify(events)


@app.route("/", methods=["GET"])
def health_check():
    """Basic health check — also reports event count for quick sanity checks."""
    count = 0
    if os.path.exists(DATA_FILE):
        with _file_lock:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                count = sum(1 for line in f if line.strip())
    return jsonify({
        "status": "ok",
        "storedEvents": count,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }), 200


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
# NOTE: This block is only used for LOCAL development.
# On Render, Gunicorn is the actual server — see gunicorn_config.py.
# Never run debug=True in production: it disables the GIL protection Flask
# relies on and exposes an interactive debugger over HTTP.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    logger.info("Starting dev server on port %d (use Gunicorn in production)", port)
    app.run(debug=False, host="0.0.0.0", port=port)
