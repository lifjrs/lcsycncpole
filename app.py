from flask import Flask, request, jsonify
import json
import os
from datetime import datetime

app = Flask(__name__)

# In-memory storage for quick testing (or use a file or external DB like MongoDB Atlas for persistence)
webhook_storage = []

@app.route('/', methods=['GET'])
def home():
    return "Webhook Receiver is Running!"

@app.route('/webhook/languagecloud', methods=['POST'])
def receive_webhook():
    try:
        payload = request.json
        payload['received_at'] = datetime.utcnow().isoformat()
        webhook_storage.append(payload)  # Save in memory for now

        # (Optional) Save to file for temporary persistence
        with open("webhooks.json", "a") as f:
            f.write(json.dumps(payload) + "\n")

        print("Received webhook:", payload)
        return jsonify({"status": "received"}), 200

    except Exception as e:
        print("Error:", str(e))
        return jsonify({"error": str(e)}), 500

@app.route('/webhook/export', methods=['GET'])
def export_webhooks():
    return jsonify(webhook_storage)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
