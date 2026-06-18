import os
import logging
from datetime import datetime, timezone
from flask import Flask, request, jsonify
import pymongo

# ---------------------------------------------------------------------------
# Logging — StreamHandler (Render console) + MongoLogHandler (permanent)
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


class MongoLogHandler(logging.Handler):
    """
    Writes every log record to MongoDB so logs survive Render restarts.
    Render's console is ephemeral — this collection is the permanent audit trail.
    """
    def __init__(self, collection):
        super().__init__()
        self.collection = collection

    def emit(self, record):
        try:
            self.collection.insert_one({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": record.levelname,
                "message": self.format(record),
                "func": record.funcName,
                "line": record.lineno,
            })
        except Exception:
            pass  # Never let log failures crash a request


# ---------------------------------------------------------------------------
# MongoDB
# ---------------------------------------------------------------------------
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME   = "error_logs"

mongo_client = pymongo.MongoClient(
    MONGO_URI,
    w=1,
    serverSelectionTimeoutMS=5000
)
db = mongo_client[DB_NAME]

# webhook_raw_events  — every event Language Cloud sends, stored permanently.
#                       webhook_tracker.py reads directly from here.
# webhook_logs        — every log line, stored permanently.
#                       Replaces Render's ephemeral console as audit trail.
raw_events_col  = db["webhook_raw_events"]
webhook_logs_col = db["webhook_logs"]

# Attach Mongo log handler — all logger calls below now write to both
# Render console AND MongoDB
_mongo_handler = MongoLogHandler(webhook_logs_col)
_mongo_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(_mongo_handler)


def setup_indexes():
    # raw_events: unique eventId prevents duplicate deliveries from LC retries
    raw_events_col.create_index(
        [("eventId", pymongo.ASCENDING)],
        unique=True,
        name="unique_event_id"
    )
    # raw_events: fast filter by type + time (used by tracker's direct query)
    raw_events_col.create_index(
        [("eventType", pymongo.ASCENDING), ("_receivedAt", pymongo.DESCENDING)],
        name="idx_type_received"
    )
    # tracker uses this to mark events as processed without deleting them
    raw_events_col.create_index(
        [("_processed", pymongo.ASCENDING), ("_receivedAt", pymongo.ASCENDING)],
        name="idx_processed_received"
    )
    # logs: latest-first queries
    webhook_logs_col.create_index(
        [("timestamp", pymongo.DESCENDING)],
        name="idx_log_ts"
    )
    webhook_logs_col.create_index(
        [("level", pymongo.ASCENDING), ("timestamp", pymongo.DESCENDING)],
        name="idx_log_level_ts"
    )
    logger.info("Indexes ensured on webhook_raw_events and webhook_logs.")


# ---------------------------------------------------------------------------
# Flask
# ---------------------------------------------------------------------------
app = Flask(__name__)

MAX_CONTENT_BYTES = 1 * 1024 * 1024  # 1 MB


@app.route("/webhook/languagecloud", methods=["POST"])
def receive_webhook():
    """
    Receives webhook POSTs from Language Cloud and writes them to MongoDB.

    No more .jsonl file — MongoDB is the sole store. webhook_tracker.py
    reads directly from webhook_raw_events, so there is no HTTP export hop.

    3-layer logging so every request leaves a permanent trace:
      L1 — raw HTTP headers logged before parsing (catches silent failures)
      L2 — raw body logged if JSON parse fails (shows exactly what arrived)
      L3 — eventType + eventId logged on success
    """
    # L1 — arrival
    logger.info(
        "Incoming POST | Content-Type: %s | Content-Length: %s | IP: %s",
        request.content_type,
        request.content_length,
        request.headers.get("X-Forwarded-For", "unknown")
    )

    if request.content_length and request.content_length > MAX_CONTENT_BYTES:
        logger.warning("Rejected oversized payload: %d bytes", request.content_length)
        return jsonify({"error": "Payload too large"}), 413

    raw_body = request.get_data(as_text=True)
    data = request.get_json(silent=True)

    if not data:
        # L2 — parse failure
        logger.warning(
            "JSON parse failed | Content-Type: %s | Body preview: %s",
            request.content_type,
            raw_body[:500]
        )
        return jsonify({"error": "Invalid JSON"}), 400

    event_type = data.get("eventType", "UNKNOWN")
    event_id   = data.get("eventId",   "no-id")
    data["_receivedAt"] = datetime.now(timezone.utc).isoformat()
    data["_processed"]  = False  # tracker flips this to True after processing

    # L3 — success
    logger.info("Webhook received: %s (eventId: %s)", event_type, event_id)

    try:
        raw_events_col.insert_one(data)
    except pymongo.errors.DuplicateKeyError:
        # Language Cloud retried a delivery we already stored — safe to ignore
        logger.info("Duplicate event ignored: %s", event_id)
    except Exception as e:
        logger.error("MongoDB write failed for event %s: %s", event_id, e)
        return jsonify({"error": "Storage failure"}), 500

    return jsonify({"status": "received", "eventId": event_id}), 200


@app.route("/webhook/export", methods=["GET"])
def export_webhook_data():
    """
    Kept for backward compatibility — webhook_tracker.py no longer calls this
    (it reads MongoDB directly), but it's useful for manual inspection.

    Query params:
      ?eventType=PROJECT.ERROR.TASK.CREATED  — filter by type
      ?processed=false                        — only unprocessed events
      ?limit=500                              — default 500
    """
    event_type_filter = request.args.get("eventType")
    processed_filter  = request.args.get("processed")
    try:
        limit = int(request.args.get("limit", 500))
    except ValueError:
        limit = 500

    query = {}
    if event_type_filter:
        query["eventType"] = event_type_filter
    if processed_filter is not None:
        query["_processed"] = processed_filter.lower() != "false"

    try:
        events = list(
            raw_events_col
            .find(query, {"_id": 0})
            .sort("_receivedAt", pymongo.ASCENDING)
            .limit(limit)
        )
        logger.info("Export: %d events (filter: %s)", len(events), query)
        return jsonify(events), 200
    except Exception as e:
        logger.error("Export failed: %s", e)
        return jsonify({"error": "Export failed"}), 500


@app.route("/webhook/logs", methods=["GET"])
def export_logs():
    """
    Returns stored log lines — permanent replacement for Render's ephemeral console.

    Query params:
      ?level=ERROR    — filter by INFO / WARNING / ERROR
      ?limit=200      — default 200
    """
    level_filter = request.args.get("level")
    try:
        limit = int(request.args.get("limit", 200))
    except ValueError:
        limit = 200

    query = {}
    if level_filter:
        query["level"] = level_filter.upper()

    try:
        logs = list(
            webhook_logs_col
            .find(query, {"_id": 0})
            .sort("timestamp", pymongo.DESCENDING)
            .limit(limit)
        )
        return jsonify(logs), 200
    except Exception as e:
        logger.error("Log export failed: %s", e)
        return jsonify({"error": "Log export failed"}), 500


@app.route("/", methods=["GET"])
def health_check():
    """Health check — shows event + log counts from MongoDB."""
    try:
        return jsonify({
            "status": "ok",
            "storedEvents":     raw_events_col.count_documents({}),
            "unprocessedEvents": raw_events_col.count_documents({"_processed": False}),
            "totalLogs":        webhook_logs_col.count_documents({}),
            "errorLogs":        webhook_logs_col.count_documents({"level": "ERROR"}),
            "timestamp":        datetime.now(timezone.utc).isoformat()
        }), 200
    except Exception as e:
        return jsonify({"status": "degraded", "error": str(e)}), 500


# ---------------------------------------------------------------------------
# Startup — runs for both local dev and Gunicorn
# ---------------------------------------------------------------------------
setup_indexes()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    logger.info("Dev server on port %d", port)
    app.run(debug=False, host="0.0.0.0", port=port)
