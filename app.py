from flask import Flask, request, jsonify
from datetime import datetime, timezone
import json
import os
import threading
import logging

# ---------------------------------------------------------------------------
# Logging — same as original print() style but via proper logger so
# Render captures it correctly under all Gunicorn worker configs
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

DATA_FILE = "webhook_events.jsonl"
TEMP_FILE = "webhook_events.jsonl.processing"

# Thread lock — prevents concurrent Gunicorn threads from interleaving
# writes to the same file line (was missing in original, caused corruption)
_file_lock = threading.Lock()


@app.route("/webhook/languagecloud", methods=["POST"])
def receive_webhook():
    try:
        # --- Use force=True so Content-Type header doesn't matter ---
        # Original used request.json which raises on bad Content-Type.
        # force=True tells Flask to always try parsing as JSON regardless
        # of what Content-Type Language Cloud sends — matches original intent.
        data = request.get_json(force=True, silent=True)

        if not data:
            # Log the raw body so we can see exactly what LC sent
            raw = request.get_data(as_text=True)
            logger.warning(
                "Could not parse JSON | Content-Type: %s | Raw body: %s",
                request.content_type, raw[:500]
            )
            return jsonify({"status": "error", "message": "Invalid JSON"}), 400

        event_type = data.get("eventType", "UNKNOWN")
        event_id   = data.get("eventId",   "no-id")
        data["_receivedAt"] = datetime.now(timezone.utc).isoformat()

        logger.info("Webhook received: %s (eventId: %s)", event_type, event_id)

        # Thread-safe file append
        with _file_lock:
            with open(DATA_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(data) + "\n")

        return jsonify({"status": "received"}), 200

    except Exception as e:
        logger.error("Error handling webhook: %s", e)
        return jsonify({"status": "error", "message": "Internal Server Error"}), 500


@app.route("/webhook/export", methods=["GET"])
def export_webhook_data():
    """
    Atomic drain — returns events accumulated since last export, then clears.
    Uses rename swap so no events are lost between read and clear.
    """
    with _file_lock:
        if not os.path.exists(DATA_FILE):
            return jsonify([]), 200

        # Remove any stale temp file from a previous crashed export
        if os.path.exists(TEMP_FILE):
            logger.warning("Stale temp file found, removing.")
            os.remove(TEMP_FILE)

        # Atomic swap — rename is atomic on Linux (Render runs Linux)
        # New POSTs now go to the fresh DATA_FILE the moment lock is released
        os.rename(DATA_FILE, TEMP_FILE)
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            pass  # create fresh empty file

    events  = []
    skipped = 0

    try:
        with open(TEMP_FILE, "r", encoding="utf-8") as f:
            for i, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.warning("Skipped malformed line %d", i)
                    skipped += 1
    finally:
        # Always clean up temp file — even if parsing crashed partway through
        if os.path.exists(TEMP_FILE):
            os.remove(TEMP_FILE)

    if skipped:
        logger.warning("Export: skipped %d malformed line(s).", skipped)

    logger.info("Export: returning %d event(s).", len(events))
    return jsonify(events), 200


@app.route("/", methods=["GET"])
def health_check():
    count = 0
    if os.path.exists(DATA_FILE):
        try:
            with _file_lock:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    count = sum(1 for line in f if line.strip())
        except OSError:
            pass

    return jsonify({
        "status": "ok",
        "queuedEvents": count,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }), 200


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(debug=False, host='0.0.0.0', port=port)
