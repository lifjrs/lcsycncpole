from flask import Flask, request, jsonify
from datetime import datetime
import pytz
import os

app = Flask(__name__)

@app.route('/webhook/languagecloud', methods=['POST'])
def receive_webhook():
    try:
        payload = request.json
        print("Received payload:", payload)

        # Do whatever you want with it
        return jsonify({"status": "received"}), 200

    except Exception as e:
        print("Error:", e)
        return jsonify({"error": str(e)}), 500

@app.route('/', methods=['GET'])
def health_check():
    return "Webhook is running!", 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
