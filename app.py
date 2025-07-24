from flask import Flask, request, jsonify
import sys

app = Flask(__name__)

# ✅ Webhook route for Language Cloud
@app.route('/webhook/languagecloud', methods=['POST'])
def languagecloud_webhook():
    try:
        data = request.get_json(force=True)
        print("✅ Received webhook at /webhook/languagecloud")
        print("Headers:", dict(request.headers))
        print("Body:", data)
        sys.stdout.flush()

        # You can now add MongoDB logic or other processing here

        return jsonify({"status": "success"}), 200
    except Exception as e:
        print("❌ Error handling webhook:", str(e))
        sys.stdout.flush()
        return jsonify({"error": str(e)}), 400

# ✅ Catch-all for debugging unexpected routes
@app.route('/', methods=['GET', 'POST'])
@app.route('/<path:path>', methods=['GET', 'POST'])
def catch_all(path=''):
    print(f"⚠️  Request to unknown path: /{path}")
    print("Headers:", dict(request.headers))
    print("Body:", request.get_data(as_text=True))
    sys.stdout.flush()
    return "Catch-all OK", 200

# ✅ Start server with debug logging
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=10000)
