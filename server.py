"""
Anime Character Voting - Flask Backend
Shared multi-user voting with daily limits (100 votes/day/user, max 10/character/day/user).
"""
import os
import sqlite3
import datetime
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__, static_folder=".", static_url_path="")
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "votes.db")

# ----- Database -----
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn

def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            character_id TEXT NOT NULL,
            user_token TEXT NOT NULL,
            vote_count INTEGER NOT NULL DEFAULT 0,
            vote_date TEXT NOT NULL,
            UNIQUE(character_id, user_token, vote_date)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_votes_date ON votes(vote_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_votes_user ON votes(user_token, vote_date)")
    conn.commit()
    conn.close()

init_db()

# ----- Helpers -----
def today_str():
    return datetime.date.today().isoformat()

MAX_DAILY_VOTES = 100
MAX_PER_CHARACTER = 10

# ----- Routes -----
@app.route("/")
def index():
    return send_from_directory(".", "index.html")

@app.route("/images/<path:filename>")
def images(filename):
    return send_from_directory("images", filename)

@app.route("/api/votes")
def get_votes():
    """Return total votes per character."""
    conn = get_db()
    rows = conn.execute("""
        SELECT character_id, SUM(vote_count) as total
        FROM votes
        GROUP BY character_id
    """).fetchall()
    conn.close()
    result = {row["character_id"]: row["total"] for row in rows}
    return jsonify(result)

@app.route("/api/user-stats")
def user_stats():
    """Return current user's vote usage for today."""
    user_token = request.args.get("user_token", "").strip()
    if not user_token:
        return jsonify({"error": "missing user_token"}), 400

    date = today_str()
    conn = get_db()
    rows = conn.execute("""
        SELECT character_id, vote_count FROM votes
        WHERE user_token = ? AND vote_date = ?
    """, (user_token, date)).fetchall()
    conn.close()

    per_char = {row["character_id"]: row["vote_count"] for row in rows}
    total_used = sum(per_char.values())
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
    data = request.get_json(silent=True) or {}
    character_id = (data.get("character_id") or "").strip()
    user_token = (data.get("user_token") or "").strip()

    if not character_id or not user_token:
        return jsonify({"error": "missing character_id or user_token"}), 400

    date = today_str()
    conn = get_db()

    # Check total votes today
    total_today = conn.execute("""
        SELECT COALESCE(SUM(vote_count), 0) FROM votes
        WHERE user_token = ? AND vote_date = ?
    """, (user_token, date)).fetchone()[0]

    if total_today >= MAX_DAILY_VOTES:
        conn.close()
        return jsonify({"error": f"今日投票次数已用完（{MAX_DAILY_VOTES}/天）", "total_used": total_today}), 429

    # Check per-character limit
    current_char = conn.execute("""
        SELECT vote_count FROM votes
        WHERE character_id = ? AND user_token = ? AND vote_date = ?
    """, (character_id, user_token, date)).fetchone()

    char_count = current_char["vote_count"] if current_char else 0
    if char_count >= MAX_PER_CHARACTER:
        conn.close()
        return jsonify({"error": f"该角色今日已投{MAX_PER_CHARACTER}票，已达上限"}), 429

    # Upsert vote
    if current_char:
        conn.execute("""
            UPDATE votes SET vote_count = vote_count + 1
            WHERE character_id = ? AND user_token = ? AND vote_date = ?
        """, (character_id, user_token, date))
    else:
        conn.execute("""
            INSERT INTO votes (character_id, user_token, vote_count, vote_date)
            VALUES (?, ?, 1, ?)
        """, (character_id, user_token, date))

    conn.commit()

    # Return updated stats
    new_char_count = char_count + 1
    new_total = total_today + 1

    # Get all totals for leaderboard update
    all_votes = {}
    for row in conn.execute("""
        SELECT character_id, SUM(vote_count) as total
        FROM votes GROUP BY character_id
    """).fetchall():
        all_votes[row["character_id"]] = row["total"]

    conn.close()

    return jsonify({
        "ok": True,
        "character_id": character_id,
        "char_votes": all_votes.get(character_id, 0),
        "all_votes": all_votes,
        "total_used": new_total,
        "total_remaining": MAX_DAILY_VOTES - new_total,
        "char_used": new_char_count,
        "char_remaining": MAX_PER_CHARACTER - new_char_count,
    })

@app.route("/api/reset", methods=["POST"])
def reset_votes():
    """Reset all votes. Body: {admin_token} (simple protection)."""
    data = request.get_json(silent=True) or {}
    admin_token = (data.get("admin_token") or "").strip()
    if admin_token != "reset-all-votes-2024":
        return jsonify({"error": "unauthorized"}), 403

    conn = get_db()
    conn.execute("DELETE FROM votes")
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "message": "所有投票已重置"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
