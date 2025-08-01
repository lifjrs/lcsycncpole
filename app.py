from flask import Flask, request, jsonify
from datetime import datetime
import json
import os
import re

app = Flask(__name__)

event_log = []

@app.route("/webhook/languagecloud", methods=["POST"])
def receive_webhook():
    try:
        raw_data = request.data.decode("utf-8", errors="replace")

        # Replace null characters which break json.loads
        cleaned_data = raw_data.replace("\x00", "")

        try:
            data = json.loads(cleaned_data)
        except json.JSONDecodeError as e:
            print(f"❌ JSON decode error: {e}")
            return "Malformed JSON", 400

        print(f"[{datetime.now()}] 🔔 Webhook received: {data.get('eventType')}")
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
        print(f"  Project ID : {event.get('project', {}).get('id', 'N/A')}")
        print(f"  Outcome    : {event.get('outcome', 'N/A')}")
        print(f"  Task Type  : {event.get('taskType', {}).get('key', 'N/A')}")
        print(f"  Error Code : {event.get('failedTask', {}).get('errors', [{}])[0].get('code', 'N/A')}")
        print(f"  Error Value: {event.get('failedTask', {}).get('errors', [{}])[0].get('value', '')}")

    return "✅ Events printed to console.", 200


@app.route("/", methods=["GET"])
def health_check():
    return "✅ Webhook server is running", 200


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(debug=True, host='0.0.0.0', port=port)
