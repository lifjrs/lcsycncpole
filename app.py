import json
import os
import threading
import logging
from datetime import datetime, timezone
from flask import Flask, request, jsonify

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DATA_FILE = "webhook_events.jsonl"
TEMP_FILE = "webhook_events.jsonl.processing"
MAX_CONTENT_BYTES = 1 * 1024 * 1024  # 1 MB

# One lock shared across all Gunicorn threads in a worker process.
# Guards both the receive (append) and export (rename+recreate) paths so
# they never run concurrently and corrupt the file.
_file_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/webhook/languagecloud", methods=["POST"])
def receive_webhook():
    """
    Receives webhook POSTs from Language Cloud.

    3-layer logging so every request leaves a trace even if parsing fails:
      L1 — raw headers logged on arrival (if this is missing for an event type,
            LC is not sending it — check the webhook subscription config)
      L2 — raw body logged if JSON parse fails (shows exactly what arrived)
      L3 — parsed eventType + eventId logged on success
    """
    # L1 — always logged, even if everything else fails
    logger.info(
        "Incoming POST | Content-Type: %s | Content-Length: %s | IP: %s",
        request.content_type,
        request.content_length,
        request.headers.get("X-Forwarded-For", "unknown")
    )

    if request.content_length and request.content_length > MAX_CONTENT_BYTES:
        logger.warning("Rejected oversized payload: %d bytes", request.content_length)
        return jsonify({"error": "Payload too large"}), 413

    # Read raw body before get_json so we can log it on parse failure
    raw_body = request.get_data(as_text=True)
    data = request.get_json(silent=True)

    if not data:
        # L2 — parse failure with full context
        logger.warning(
            "JSON parse failed | Content-Type: %s | Body preview: %s",
            request.content_type,
            raw_body[:500]
        )
        return jsonify({"error": "Invalid JSON"}), 400

    event_type = data.get("eventType", "UNKNOWN")
    event_id   = data.get("eventId",   "no-id")
    data["_receivedAt"] = datetime.now(timezone.utc).isoformat()

    # L3 — success
    logger.info("Webhook received: %s (eventId: %s)", event_type, event_id)

    try:
        with _file_lock:
            with open(DATA_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(data) + "\n")
    except OSError as e:
        logger.error("File write failed for event %s: %s", event_id, e)
        return jsonify({"error": "Storage failure"}), 500

    return jsonify({"status": "received", "eventId": event_id}), 200


@app.route("/webhook/export", methods=["GET"])
def export_webhook_data():
    """
    Returns all events accumulated since the last export, then clears the file.

    This is the "drain" pattern — LC_webhook.py calls this every 30s, gets
    only the new events since the last call, and the file never grows unbounded.

    Atomic swap approach (Linux-safe on Render):
      Step 1 — os.rename(DATA_FILE → TEMP_FILE) inside the lock.
               rename() is atomic on Linux: no incoming POST can append to a
               file that no longer exists at DATA_FILE.
      Step 2 — Recreate DATA_FILE immediately (still inside lock) so new
               POSTs have somewhere to write the moment the lock is released.
      Step 3 — Release lock. New POSTs now write to the fresh DATA_FILE.
      Step 4 — Read and parse TEMP_FILE outside the lock (no contention needed;
               only this request touches TEMP_FILE).
      Step 5 — Delete TEMP_FILE inside a finally block so it's cleaned up even
               if parsing crashes partway through.

    Compared to reading-then-truncating (the naive approach), rename guarantees
    zero event loss: there is no window where an event could be appended to the
    file after we've read it but before we've truncated it.
    """
    with _file_lock:
        if not os.path.exists(DATA_FILE):
            # Nothing written yet — return empty without creating a temp file
            return jsonify([]), 200

        # Clean up any leftover temp file from a previous crashed export
        if os.path.exists(TEMP_FILE):
            logger.warning("Stale temp file found — removing before export.")
            os.remove(TEMP_FILE)

        # Atomic swap: DATA_FILE → TEMP_FILE
        os.rename(DATA_FILE, TEMP_FILE)

        # Recreate fresh DATA_FILE immediately so incoming POSTs aren't blocked
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            pass  # empty file, explicit close via context manager

    # Parse TEMP_FILE outside the lock — new POSTs are already writing to DATA_FILE
    events = []
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
                    logger.warning("Skipped malformed line %d in temp file", i)
                    skipped += 1
    finally:
        # Always clean up TEMP_FILE — even if reading crashed halfway through.
        # Without this, a crash here would leave TEMP_FILE on disk, and the next
        # export would hit the "stale temp file" warning above and remove it,
        # meaning those events would be permanently lost. Logging here makes
        # that scenario visible in Render logs.
        if os.path.exists(TEMP_FILE):
            os.remove(TEMP_FILE)

    if skipped:
        logger.warning("Export: skipped %d malformed line(s).", skipped)

    logger.info("Export: returning %d event(s).", len(events))
    return jsonify(events), 200


@app.route("/", methods=["GET"])
def health_check():
    """
    Health check — reports how many events are currently queued in the file
    (i.e. received since the last export poll).
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
        "status": "ok",
        "queuedEvents": count,   # events waiting to be picked up by next poll
        "timestamp": datetime.now(timezone.utc).isoformat()
    }), 200


# ---------------------------------------------------------------------------
# Entry point — LOCAL DEV ONLY
# On Render, Gunicorn is the server (see gunicorn_config.py).
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    logger.info("Starting dev server on port %d (use Gunicorn in production)", port)
    app.run(debug=False, host="0.0.0.0", port=port)
