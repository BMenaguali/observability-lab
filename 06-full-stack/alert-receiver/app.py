from flask import Flask, request, jsonify
import json

app = Flask(__name__)


@app.route("/alerts", methods=["POST"])
def alerts():
    payload = request.get_json(silent=True)

    print("\n========== GRAFANA ALERT ==========")
    print(json.dumps(payload, indent=2))
    print("===================================\n", flush=True)

    return jsonify({
        "status": "received"
    }), 200


@app.route("/health")
def health():
    return jsonify({
        "status": "ok"
    }), 200


app.run(
    host="0.0.0.0",
    port=8081
)
