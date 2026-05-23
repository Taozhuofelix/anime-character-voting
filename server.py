"""
Anime Character Voting - Flask Backend
Uses GitHub Gist for persistent storage. Token from GITHUB_TOKEN env var.
"""
import os
import json
import datetime
import urllib.request
import urllib.error
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__, static_folder=".", static_url_path="")

# CORS — allow GitHub Pages and local dev
@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
GIST_ID = os.environ.get("GIST_ID", "5c2ae750f014e032cce5f2f2c188e8b7")
GIST_API = f"https://api.github.com/gists/{GIST_ID}"
GIST_RAW = f"https://gist.githubusercontent.com/Taozhuofelix/{GIST_ID}/raw/votes.json"

MAX_DAILY_VOTES = 100
MAX_PER_CHARACTER = 10

# ----- Gist helpers -----
def _gist_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "AnimeVoting/1.0",
    }

def gist_read():
    """Read votes.json from the public Gist raw URL (no auth needed)."""
    req = urllib.request.Request(GIST_RAW + "?t=" + str(datetime.datetime.now().timestamp()))
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())

def gist_write(data):
    """Write votes.json to the Gist (needs auth token)."""
    if not GITHUB_TOKEN:
        raise RuntimeError("GITHUB_TOKEN not configured")
    payload = json.dumps({"files": {"votes.json": {"content": json.dumps(data, ensure_ascii=False)}}}).encode()
    req = urllib.request.Request(GIST_API, data=payload, headers=_gist_headers(), method="PATCH")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:200]
        raise RuntimeError(f"Gist write failed (HTTP {e.code}): {body}")

def today_str():
    return datetime.date.today().isoformat()

# ----- Routes -----
@app.route("/")
def index():
    return send_from_directory(".", "index.html")

@app.route("/images/<path:filename>")
def images(filename):
    return send_from_directory("images", filename)

@app.route("/api/votes")
def get_votes():
    """Return total votes per character (public, no auth needed)."""
    try:
        data = gist_read()
        return jsonify(data.get("votes", {}))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/user-stats")
def user_stats():
    """Return current user's vote usage for today."""
    user_token = request.args.get("user_token", "").strip()
    if not user_token:
        return jsonify({"error": "missing user_token"}), 400

    try:
        data = gist_read()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    today = today_str()
    daily = (data.get("daily") or {}).get(today) or {}
    my_data = daily.get(user_token) or {}
    per_char = my_data.get("per_char") or {}
    total_used = my_data.get("total", 0)

    return jsonify({
        "total_used": total_used,
        "total_remaining": MAX_DAILY_VOTES - total_used,
        "per_character": per_char,
        "max_daily": MAX_DAILY_VOTES,
        "max_per_char": MAX_PER_CHARACTER,
    })

@app.route("/api/vote", methods=["POST"])
def cast_vote():
    """Cast a vote. Body: {character_id, user_token}"""
    if not GITHUB_TOKEN:
        return jsonify({"error": "Server token not configured"}), 500

    data = request.get_json(silent=True) or {}
    character_id = (data.get("character_id") or "").strip()
    user_token = (data.get("user_token") or "").strip()

    if not character_id or not user_token:
        return jsonify({"error": "missing character_id or user_token"}), 400

    today = today_str()

    try:
        gist_data = gist_read()
    except Exception as e:
        return jsonify({"error": f"Failed to read data: {e}"}), 500

    # Ensure structure
    if gist_data.get("last_reset") != today:
        gist_data["daily"] = {}
        gist_data["daily"][today] = {}
        gist_data["last_reset"] = today
    gist_data.setdefault("daily", {}).setdefault(today, {})
    gist_data.setdefault("votes", {})

    day_data = gist_data["daily"][today]
    my_data = day_data.get(user_token) or {"total": 0, "per_char": {}}

    # Check limits
    if my_data["total"] >= MAX_DAILY_VOTES:
        return jsonify({"error": f"今日投票次数已用完（{MAX_DAILY_VOTES}/天）", "total_used": my_data["total"]}), 429

    char_count = my_data.get("per_char", {}).get(character_id, 0)
    if char_count >= MAX_PER_CHARACTER:
        return jsonify({"error": f"该角色今日已投{MAX_PER_CHARACTER}票，已达上限"}), 429

    # Update
    my_data["total"] += 1
    my_data.setdefault("per_char", {})[character_id] = char_count + 1
    day_data[user_token] = my_data
    gist_data["votes"][character_id] = gist_data["votes"].get(character_id, 0) + 1

    try:
        gist_write(gist_data)
    except Exception as e:
        return jsonify({"error": f"Failed to write data: {e}"}), 500

    new_char_count = char_count + 1
    new_total = my_data["total"]

    return jsonify({
        "ok": True,
        "character_id": character_id,
        "char_votes": gist_data["votes"].get(character_id, 0),
        "all_votes": gist_data["votes"],
        "total_used": new_total,
        "total_remaining": MAX_DAILY_VOTES - new_total,
        "char_used": new_char_count,
        "char_remaining": MAX_PER_CHARACTER - new_char_count,
    })

@app.route("/api/reset", methods=["POST"])
def reset_votes():
    """Reset all votes. Body: {admin_token} (simple protection)."""
    if not GITHUB_TOKEN:
        return jsonify({"error": "Server token not configured"}), 500

    data = request.get_json(silent=True) or {}
    admin_token = (data.get("admin_token") or "").strip()
    if admin_token != "reset-all-votes-2024":
        return jsonify({"error": "unauthorized"}), 403

    try:
        gist_write({"votes": {}, "daily": {}, "last_reset": today_str()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({"ok": True, "message": "所有投票已重置"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
