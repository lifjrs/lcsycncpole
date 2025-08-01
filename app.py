from flask import Flask, request, jsonify
from datetime import datetime
import json
import os

app = Flask(__name__)

# In-memory store for webhook events
event_log = []

@app.route("/webhook/languagecloud", methods=["POST"])
def receive_webhook():
    try:
        data = request.json
        if not data:
            return "Invalid JSON", 400

        print(f"[{datetime.now()}] 🔔 Webhook received: {data.get('eventType')}")

        # Add event to memory
        event_log.append(data)

        return jsonify({"status": "received"}), 200

    except Exception as e:
        print(f"❌ Error handling webhook: {e}")
        return "Internal Server Error", 500


@app.route("/webhook/export", methods=["GET"])
def export_webhook_data():
    if not event_log:
        return "No events received yet.", 200

    print("\n📋 === Webhook Events Summary ===")
    for idx, event in enumerate(event_log, start=1):
        print(f"\nEvent #{idx}")
        print(f"  Event Type : {event.get('eventType')}")
        print(f"  Timestamp  : {event.get('timestamp', 'N/A')}")
        print(f"  Project ID : {event.get('projectId', 'N/A')}")
        print(f"  Full Event : {json.dumps(event, indent=2)}")

    return "✅ Events printed to console.", 200


@app.route("/", methods=["GET"])
def health_check():
    return "✅ Webhook server is running", 200


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(debug=True, host='0.0.0.0', port=port)
