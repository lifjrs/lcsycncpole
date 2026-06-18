from flask import Flask, request, jsonify
from datetime import datetime, timezone
import json
import os
import threading
import logging

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

DATA_FILE = "webhook_events.jsonl"
TEMP_FILE = "webhook_events.jsonl.processing"

# threading.Lock() is correct here — plain `python app.py` is a single process.
# If you ever switch to Gunicorn with multiple workers, replace this with
# fcntl.flock() (see comments in gunicorn version).
_file_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/webhook/languagecloud", methods=["POST"])
def receive_webhook():
    # L1 — log arrival before any parsing so we always have a trace
    logger.info(
        "Incoming POST | Content-Type: %s | Content-Length: %s | IP: %s",
        request.content_type,
        request.content_length,
        request.headers.get("X-Forwarded-For", "unknown")
    )

    raw_body = request.get_data(as_text=True)

    # force=True: parse as JSON regardless of Content-Type header.
    # Language Cloud sometimes sends non-standard Content-Type headers
    # even when the body is valid JSON.
    data = request.get_json(force=True, silent=True)

    if not data:
        # L2 — parse failure: log exactly what arrived so we can diagnose
        logger.warning(
            "JSON parse failed | Content-Type: %s | Body: %s",
            request.content_type, raw_body[:500]
        )
        return jsonify({"status": "error", "message": "Invalid JSON"}), 400

    event_type = data.get("eventType", "UNKNOWN")
    event_id   = data.get("eventId",   "no-id")
    data["_receivedAt"] = datetime.now(timezone.utc).isoformat()

    # L3 — successful parse
    logger.info("Webhook received: %s (eventId: %s)", event_type, event_id)

    try:
        with _file_lock:
            with open(DATA_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(data) + "\n")
                f.flush()
                os.fsync(f.fileno())  # ensure write survives a crash
    except OSError as e:
        logger.error("File write failed for event %s: %s", event_id, e)
        return jsonify({"status": "error", "message": "Storage failure"}), 500

    return jsonify({"status": "received"}), 200


@app.route("/webhook/export", methods=["GET"])
def export_webhook_data():
    """
    Atomic drain — returns all events since last export, clears the file.

    Uses os.rename (atomic on Linux) so no event is ever lost between
    the read and the clear. Lock ensures the rename and file recreation
    happen as one unit from the perspective of any concurrent POST.
    """
    with _file_lock:
        if not os.path.exists(DATA_FILE):
            return jsonify([]), 200

        # Stale temp file = previous export crashed mid-read
        if os.path.exists(TEMP_FILE):
            logger.warning("Stale temp file found — removing before export.")
            os.remove(TEMP_FILE)

        # Atomic swap
        os.rename(DATA_FILE, TEMP_FILE)

        # Recreate immediately so incoming POSTs have somewhere to write
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            pass

    # Read outside the lock — new POSTs are already writing to the fresh file
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
                    logger.warning("Skipped malformed line %d.", i)
                    skipped += 1
    finally:
        if os.path.exists(TEMP_FILE):
            os.remove(TEMP_FILE)

    if skipped:
        logger.warning("Export: skipped %d malformed line(s).", skipped)

    logger.info("Export: returning %d event(s).", len(events))
    return jsonify(events), 200


@app.route("/", methods=["GET"])
def health_check():
    """
    Shows how many events are queued since last poll.
    If this keeps growing, LC_webhook.py may not be running.
    """
    count = 0
    if os.path.exists(DATA_FILE):
        try:
            with _file_lock:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    count = sum(1 for line in f if line.strip())
        except OSError:
            pass

    return jsonify({
        "status":       "ok",
        "queuedEvents": count,
        "timestamp":    datetime.now(timezone.utc).isoformat()
    }), 200


# ---------------------------------------------------------------------------
# Entry point
#
# CRITICAL — use_reloader=False:
#   Flask's debug reloader watches ALL files in the working directory.
#   Every time webhook_events.jsonl is written to, the reloader sees the
#   change and RESTARTS Flask — killing in-flight requests and losing any
#   events being processed at that moment. This was the root cause of
#   "works for a few events then stops" behaviour.
#   use_reloader=False disables this while keeping the app running stably.
#
# threaded=True:
#   Allows Flask to handle multiple simultaneous requests (e.g. a POST
#   arriving while an export GET is in progress) without queuing them.
#   The threading.Lock() above keeps file access safe across these threads.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    logger.info("Starting on port %d", port)
    app.run(
        debug=False,          # NEVER True in production — exposes debugger over HTTP
        use_reloader=False,   # CRITICAL — prevents file-write restarts
        threaded=True,        # handle concurrent requests safely
        host="0.0.0.0",
        port=port
    )
