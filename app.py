import json
import os
import re
import sqlite3
import threading
import time
import uuid
from typing import Any

from flask import Flask, jsonify, render_template, request, session
from werkzeug.security import check_password_hash, generate_password_hash

try:
    import psycopg2
except ImportError:
    psycopg2 = None

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "schrewizium-clean-rewrite-key-2026")

DEFAULT_NEON_URL = (
    "postgresql://schrewizium_owner:npg_omq7YWvKI9Dn@ep-lively-heart-bbyfei9v-pooler.c-15.us-east-1.aws.neon.tech/schrewizium?sslmode=require&channel_binding=require"
)
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip() or DEFAULT_NEON_URL
DB_FILE = os.environ.get("DATABASE_PATH", "schrewizium.db")
LEGACY_JSON_FILE = "schrewizium_state.json"
STATE_LOCK = threading.Lock()
USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.]{2,20}$")
VALID_STATUSES = {"online", "idle", "dnd", "invisible", "offline"}
ALLOWED_REACTIONS = {"👍", "❤️", "😂", "🔥", "👀", "🫡"}
MAX_IMAGE_CHARS = 950_000


def use_postgres() -> bool:
    return bool(DATABASE_URL and psycopg2 is not None)


def get_pg_conn():
    url = DATABASE_URL
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    return psycopg2.connect(url)


def init_db() -> None:
    if use_postgres():
        with get_pg_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS app_state (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        payload TEXT NOT NULL,
                        updated_at DOUBLE PRECISION NOT NULL
                    )
                    """
                )
            conn.commit()
        return

    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                payload TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        conn.commit()


def make_general_gc() -> dict[str, Any]:
    return {
        "id": "gc_general",
        "name": "general-lounge",
        "is_public": True,
        "owner_id": "system",
        "members": [],
        "messages": [],
        "pins": [],
        "mention_unread": {},
        "last_seen_ts": {},
        "typing": {},
    }


def default_state() -> dict[str, Any]:
    return {
        "users": {},
        "username_map": {},
        "dms": {},
        "gcs": {"gc_general": make_general_gc()},
        "bulletin": [],
        "polls": [],
    }


def normalize_state(loaded: dict[str, Any]) -> dict[str, Any]:
    loaded.setdefault("users", {})
    loaded.setdefault("username_map", {})
    loaded.setdefault("dms", {})
    loaded.setdefault("gcs", {})
    loaded.setdefault("bulletin", [])
    loaded.setdefault("polls", [])
    if "gc_general" not in loaded["gcs"]:
        loaded["gcs"]["gc_general"] = make_general_gc()
    for room in loaded["gcs"].values():
        room.setdefault("last_seen_ts", {})
        room.setdefault("mention_unread", {})
        for msg in room.get("messages", []):
            msg.setdefault("reactions", {})
            msg.setdefault("image", "")
    for dm_room in loaded["dms"].values():
        dm_room.setdefault("last_seen_ts", {})
        dm_room.setdefault("unread_for", {})
        for msg in dm_room.get("messages", []):
            msg.setdefault("reactions", {})
            msg.setdefault("image", "")
    return loaded


def load_state() -> dict[str, Any]:
    init_db()
    row = None
    if use_postgres():
        with get_pg_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT payload FROM app_state WHERE id = 1")
                row = cur.fetchone()
    else:
        with sqlite3.connect(DB_FILE) as conn:
            row = conn.execute("SELECT payload FROM app_state WHERE id = 1").fetchone()

    if row and row[0]:
        try:
            loaded = json.loads(row[0])
            if isinstance(loaded, dict):
                return normalize_state(loaded)
        except json.JSONDecodeError:
            pass

    if os.path.exists(LEGACY_JSON_FILE):
        try:
            with open(LEGACY_JSON_FILE, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                state = normalize_state(loaded)
                save_state(state)
                return state
        except (OSError, json.JSONDecodeError):
            pass

    state = default_state()
    save_state(state)
    return state


def save_state(state: dict[str, Any]) -> None:
    init_db()
    serialized = json.dumps(state)
    now = time.time()
    if use_postgres():
        with get_pg_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app_state (id, payload, updated_at)
                    VALUES (1, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        payload = EXCLUDED.payload,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (serialized, now),
                )
            conn.commit()
        return

    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            """
            INSERT INTO app_state (id, payload, updated_at)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                payload = excluded.payload,
                updated_at = excluded.updated_at
            """,
            (serialized, now),
        )
        conn.commit()


def clean_username(raw: Any) -> str:
    if not raw:
        return ""
    return re.sub(r"\s+", "", str(raw).strip())


def sanitize_image_src(raw: Any) -> str:
    src = str(raw or "").strip()
    if not src:
        return ""
    if len(src) > MAX_IMAGE_CHARS:
        return ""
    if src.startswith("data:image/"):
        if ";base64," in src:
            return src
        return ""
    if src.startswith("https://") or src.startswith("http://"):
        return src[:500]
    return ""


def dm_key(user_a: str, user_b: str) -> str:
    first, second = sorted([user_a, user_b])
    return f"dm_{first}_{second}"


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": user["id"],
        "username": user["username"],
        "status": user.get("status", "online"),
        "custom_status": user.get("custom_status", ""),
        "avatar": user.get("avatar", ""),
        "bio": user.get("bio", ""),
        "banner_color": user.get("banner_color", "#2b2d31"),
        "pronouns": user.get("pronouns", ""),
    }


def current_user(state: dict[str, Any]) -> dict[str, Any] | None:
    uid = session.get("user_id")
    if not uid or uid not in state["users"]:
        return None
    return state["users"][uid]


def resolve_room(
    state: dict[str, Any], uid: str, kind: str | None, target_id: str | None
) -> dict[str, Any] | None:
    if kind == "dm" and target_id:
        return state["dms"].get(dm_key(uid, target_id))
    if kind == "gc" and target_id:
        room = state["gcs"].get(target_id)
        if room and (room.get("is_public") or uid in room.get("members", [])):
            return room
    return None


def latest_incoming_ts(room: dict[str, Any], uid: str) -> int:
    latest = 0
    for msg in room.get("messages", []):
        if msg.get("author_id") != uid:
            ts = int(msg.get("ts", 0) or 0)
            if ts > latest:
                latest = ts
    return latest


def has_unseen_messages(room: dict[str, Any] | None, uid: str) -> bool:
    if not room:
        return False
    latest = latest_incoming_ts(room, uid)
    if latest == 0:
        return False
    seen = int(room.get("last_seen_ts", {}).get(uid, 0) or 0)
    return latest > seen


def mark_room_seen(room: dict[str, Any] | None, uid: str) -> None:
    if not room:
        return
    latest = latest_incoming_ts(room, uid)
    now_ms = int(time.time() * 1000)
    seen_map = room.setdefault("last_seen_ts", {})
    prev_seen = int(seen_map.get(uid, 0) or 0)
    seen_map[uid] = max(prev_seen, latest, now_ms)


@app.route("/")
def index() -> str:
    return render_template("index.html")


@app.route("/api/auth", methods=["POST"])
def auth():
    payload = request.get_json(silent=True) or {}
    username = clean_username(payload.get("username", ""))
    password = str(payload.get("password", ""))
    mode = str(payload.get("mode", "register")).strip().lower()

    if not USERNAME_RE.match(username):
        return (
            jsonify(
                {
                    "error": (
                        "Username must be 2-20 characters using letters, "
                        "numbers, '_' or '.' with no spaces or special symbols."
                    )
                }
            ),
            400,
        )

    if len(password) < 4:
        return jsonify({"error": "Password must be at least 4 characters."}), 400

    lower_key = username.lower()
    with STATE_LOCK:
        state = load_state()
        if lower_key in state["username_map"]:
            if mode == "register":
                return (
                    jsonify(
                        {
                            "error": (
                                f"Username '{username}' is already taken. "
                                "Choose another or switch to Log In."
                            )
                        }
                    ),
                    409,
                )
            uid = state["username_map"][lower_key]
            user = state["users"][uid]
            stored_hash = user.get("password_hash", "")
            if stored_hash:
                if not check_password_hash(stored_hash, password):
                    return jsonify({"error": "Incorrect password."}), 401
            else:
                user["password_hash"] = generate_password_hash(password)
        else:
            if mode == "login":
                return jsonify({"error": f"No account found for '{username}'."}), 404
            uid = f"u_{uuid.uuid4().hex[:10]}"
            user = {
                "id": uid,
                "username": username,
                "password_hash": generate_password_hash(password),
                "status": "online",
                "custom_status": str(payload.get("custom_status", "")).strip()[:60],
                "avatar": str(payload.get("avatar", "")).strip()[:300],
                "bio": str(payload.get("bio", "")).strip()[:200],
                "banner_color": str(payload.get("banner_color", "#2b2d31")).strip()[:20],
                "pronouns": str(payload.get("pronouns", "")).strip()[:30],
                "friends": [],
                "incoming": [],
                "outgoing": [],
            }
            state["users"][uid] = user
            state["username_map"][lower_key] = uid

        for room in state["gcs"].values():
            if room.get("is_public") and uid not in room["members"]:
                room["members"].append(uid)

        save_state(state)
        session["user_id"] = uid
        return jsonify({"user": public_user(user)})


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/backup/export")
def export_backup():
    with STATE_LOCK:
        state = load_state()
        me = current_user(state)
        if not me:
            return jsonify({"error": "Not logged in."}), 401
        return jsonify({"backup": state, "exported_at": int(time.time() * 1000)})


@app.route("/api/backup/import", methods=["POST"])
def import_backup():
    payload = request.get_json(silent=True) or {}
    backup = payload.get("backup")
    if not isinstance(backup, dict) or "users" not in backup or "username_map" not in backup:
        return jsonify({"error": "Invalid backup JSON format."}), 400

    with STATE_LOCK:
        restored = normalize_state(backup)
        save_state(restored)
        return jsonify({"ok": True})


@app.route("/api/profile", methods=["POST"])
def update_profile():
    payload = request.get_json(silent=True) or {}
    with STATE_LOCK:
        state = load_state()
        me = current_user(state)
        if not me:
            return jsonify({"error": "Not logged in."}), 401

        new_username = clean_username(payload.get("username", me["username"]))
        if new_username != me["username"]:
            if not USERNAME_RE.match(new_username):
                return jsonify({"error": "Invalid username (no spaces or special symbols)."}), 400
            new_key = new_username.lower()
            existing_id = state["username_map"].get(new_key)
            if existing_id and existing_id != me["id"]:
                return jsonify({"error": "Username is already taken."}), 409
            del state["username_map"][me["username"].lower()]
            state["username_map"][new_key] = me["id"]
            me["username"] = new_username

        new_password = str(payload.get("new_password", ""))
        if new_password:
            if len(new_password) < 4:
                return jsonify({"error": "New password must be at least 4 characters."}), 400
            me["password_hash"] = generate_password_hash(new_password)

        status = payload.get("status", me.get("status", "online"))
        if status in VALID_STATUSES:
            me["status"] = status

        me["custom_status"] = str(payload.get("custom_status", me.get("custom_status", "")))[:60]
        me["avatar"] = str(payload.get("avatar", me.get("avatar", "")))[:300]
        me["bio"] = str(payload.get("bio", me.get("bio", "")))[:200]
        me["banner_color"] = str(payload.get("banner_color", me.get("banner_color", "#2b2d31")))[:20]
        me["pronouns"] = str(payload.get("pronouns", me.get("pronouns", "")))[:30]

        save_state(state)
        return jsonify({"user": public_user(me)})


@app.route("/api/users/search")
def search_users():
    query = clean_username(request.args.get("q", "")).lower()
    with STATE_LOCK:
        state = load_state()
        me = current_user(state)
        if not me:
            return jsonify({"results": []})

        matches = []
        for user in state["users"].values():
            if user["id"] == me["id"]:
                continue
            if not query or query in user["username"].lower():
                relation = "none"
                if user["id"] in me["friends"]:
                    relation = "friend"
                elif user["id"] in me["outgoing"]:
                    relation = "outgoing"
                elif user["id"] in me["incoming"]:
                    relation = "incoming"
                entry = public_user(user)
                entry["relation"] = relation
                matches.append(entry)

        return jsonify({"results": matches[:25]})


@app.route("/api/friends/request", methods=["POST"])
def send_friend_request():
    payload = request.get_json(silent=True) or {}
    target_name = clean_username(payload.get("username", "")).lower()
    with STATE_LOCK:
        state = load_state()
        me = current_user(state)
        if not me:
            return jsonify({"error": "Not logged in."}), 401

        target_id = state["username_map"].get(target_name)
        target = state["users"].get(target_id) if target_id else None
        if not target:
            return jsonify({"error": "User not found."}), 404
        if target["id"] == me["id"]:
            return jsonify({"error": "You cannot add yourself."}), 400

        tid = target["id"]
        if tid in me["friends"]:
            return jsonify({"ok": True})

        if tid in me["incoming"]:
            me["incoming"].remove(tid)
            if me["id"] in target["outgoing"]:
                target["outgoing"].remove(me["id"])
            if tid not in me["friends"]:
                me["friends"].append(tid)
            if me["id"] not in target["friends"]:
                target["friends"].append(me["id"])
        else:
            if tid not in me["outgoing"]:
                me["outgoing"].append(tid)
            if me["id"] not in target["incoming"]:
                target["incoming"].append(me["id"])

        save_state(state)
        return jsonify({"ok": True})


@app.route("/api/friends/respond", methods=["POST"])
def respond_friend_request():
    payload = request.get_json(silent=True) or {}
    target_id = payload.get("user_id")
    action = payload.get("action")
    with STATE_LOCK:
        state = load_state()
        me = current_user(state)
        if not me:
            return jsonify({"error": "Not logged in."}), 401

        target = state["users"].get(target_id) if target_id else None
        if not target:
            return jsonify({"error": "User not found."}), 404

        tid = target["id"]
        if tid in me["incoming"]:
            me["incoming"].remove(tid)
        if tid in me["outgoing"]:
            me["outgoing"].remove(tid)
        if me["id"] in target["outgoing"]:
            target["outgoing"].remove(me["id"])
        if me["id"] in target["incoming"]:
            target["incoming"].remove(me["id"])

        if action == "accept":
            if tid not in me["friends"]:
                me["friends"].append(tid)
            if me["id"] not in target["friends"]:
                target["friends"].append(me["id"])
            key = dm_key(me["id"], tid)
            if key not in state["dms"]:
                state["dms"][key] = {
                    "id": key,
                    "members": [me["id"], tid],
                    "messages": [],
                    "unread_for": {me["id"]: False, tid: False},
                    "last_seen_ts": {me["id"]: int(time.time() * 1000), tid: int(time.time() * 1000)},
                    "typing": {},
                }
        elif action == "remove":
            if tid in me["friends"]:
                me["friends"].remove(tid)
            if me["id"] in target["friends"]:
                target["friends"].remove(me["id"])

        save_state(state)
        return jsonify({"ok": True})


@app.route("/api/gcs/create", methods=["POST"])
def create_gc():
    payload = request.get_json(silent=True) or {}
    name = re.sub(r"\s+", "-", str(payload.get("name", "")).strip())[:30]
    is_public = bool(payload.get("is_public", True))
    requested_members = payload.get("members", [])
    if not name:
        return jsonify({"error": "Group chat name is required."}), 400

    with STATE_LOCK:
        state = load_state()
        me = current_user(state)
        if not me:
            return jsonify({"error": "Not logged in."}), 401

        gc_id = f"gc_{uuid.uuid4().hex[:10]}"
        members = list(state["users"].keys()) if is_public else [me["id"]]
        if not is_public and isinstance(requested_members, list):
            for member_id in requested_members:
                if member_id in state["users"] and member_id not in members:
                    members.append(member_id)

        state["gcs"][gc_id] = {
            "id": gc_id,
            "name": name,
            "is_public": is_public,
            "owner_id": me["id"],
            "members": members,
            "messages": [],
            "pins": [],
            "mention_unread": {},
            "last_seen_ts": {me["id"]: int(time.time() * 1000)},
            "typing": {},
        }
        save_state(state)
        return jsonify({"gc_id": gc_id})


@app.route("/api/typing", methods=["POST"])
def set_typing():
    payload = request.get_json(silent=True) or {}
    kind = payload.get("kind")
    target_id = payload.get("target_id")
    is_typing = bool(payload.get("typing", True))

    with STATE_LOCK:
        state = load_state()
        me = current_user(state)
        if not me:
            return jsonify({"ok": False}), 401

        room = resolve_room(state, me["id"], kind, target_id)
        if room is not None:
            typing_map = room.setdefault("typing", {})
            if is_typing:
                typing_map[me["id"]] = time.time()
            else:
                typing_map.pop(me["id"], None)
            save_state(state)
        return jsonify({"ok": True})


@app.route("/api/read", methods=["POST"])
def mark_read():
    payload = request.get_json(silent=True) or {}
    kind = payload.get("kind")
    target_id = payload.get("target_id")

    with STATE_LOCK:
        state = load_state()
        me = current_user(state)
        if not me:
            return jsonify({"ok": False}), 401

        if kind == "dm" and target_id:
            room = state["dms"].get(dm_key(me["id"], target_id))
            if room:
                room.setdefault("unread_for", {})[me["id"]] = False
                mark_room_seen(room, me["id"])
        elif kind == "gc" and target_id:
            room = state["gcs"].get(target_id)
            if room:
                room.setdefault("mention_unread", {})[me["id"]] = False
                mark_room_seen(room, me["id"])

        save_state(state)
        return jsonify({"ok": True})


@app.route("/api/messages/send", methods=["POST"])
def send_message():
    payload = request.get_json(silent=True) or {}
    kind = payload.get("kind")
    target_id = payload.get("target_id")
    text = str(payload.get("text", "")).strip()
    image = sanitize_image_src(payload.get("image", ""))
    reply_to_id = payload.get("reply_to")

    if not text and not image:
        return jsonify({"error": "Message or image is required."}), 400
    if payload.get("image") and not image:
        return jsonify({"error": "Image is too large or unsupported."}), 400

    with STATE_LOCK:
        state = load_state()
        me = current_user(state)
        if not me:
            return jsonify({"error": "Not logged in."}), 401

        if kind == "dm":
            if not target_id or target_id not in state["users"]:
                return jsonify({"error": "Recipient not found."}), 404
            key = dm_key(me["id"], target_id)
            if key not in state["dms"]:
                state["dms"][key] = {
                    "id": key,
                    "members": [me["id"], target_id],
                    "messages": [],
                    "unread_for": {me["id"]: False, target_id: False},
                    "last_seen_ts": {},
                    "typing": {},
                }
            room = state["dms"][key]
            room.setdefault("unread_for", {})[target_id] = True
            room["unread_for"][me["id"]] = False
        elif kind == "gc":
            room = state["gcs"].get(target_id) if target_id else None
            if not room:
                return jsonify({"error": "Group chat not found."}), 404
            if room.get("is_public") and me["id"] not in room["members"]:
                room["members"].append(me["id"])
            if me["id"] not in room["members"]:
                return jsonify({"error": "Not a member of this group chat."}), 403
        else:
            return jsonify({"error": "Invalid chat destination."}), 400

        reply_snapshot = None
        if reply_to_id:
            for existing in room["messages"]:
                if existing["id"] == reply_to_id:
                    reply_snapshot = {
                        "id": existing["id"],
                        "author_username": existing["author_username"],
                        "text": existing["text"][:140],
                    }
                    break

        mentioned_ids = []
        mentioned_names = {
            token.lower() for token in re.findall(r"@([a-zA-Z0-9_.]{2,20})", text)
        }
        for member_id in room["members"]:
            member_user = state["users"].get(member_id)
            if (
                member_user
                and member_user["username"].lower() in mentioned_names
                and member_id != me["id"]
            ):
                mentioned_ids.append(member_id)
                if kind == "gc":
                    room.setdefault("mention_unread", {})[member_id] = True

        msg_ts = int(time.time() * 1000)
        message = {
            "id": f"m_{uuid.uuid4().hex[:12]}",
            "author_id": me["id"],
            "author_username": me["username"],
            "author_avatar": me.get("avatar", ""),
            "text": text[:2000],
            "image": image,
            "ts": msg_ts,
            "edited": False,
            "reply_to": reply_snapshot,
            "mentions": mentioned_ids,
            "reactions": {},
        }

        room["messages"].append(message)
        room["messages"] = room["messages"][-300:]
        room.setdefault("last_seen_ts", {})[me["id"]] = max(
            int(room.get("last_seen_ts", {}).get(me["id"], 0) or 0), msg_ts
        )
        room.setdefault("typing", {}).pop(me["id"], None)
        save_state(state)
        return jsonify({"message": message})


@app.route("/api/messages/edit", methods=["POST"])
def edit_message():
    payload = request.get_json(silent=True) or {}
    kind = payload.get("kind")
    target_id = payload.get("target_id")
    msg_id = payload.get("msg_id")
    new_text = str(payload.get("text", "")).strip()
    if not new_text:
        return jsonify({"error": "Message cannot be empty."}), 400

    with STATE_LOCK:
        state = load_state()
        me = current_user(state)
        if not me:
            return jsonify({"error": "Not logged in."}), 401

        room = resolve_room(state, me["id"], kind, target_id)
        if not room:
            return jsonify({"error": "Chat not found."}), 404

        for msg in room["messages"]:
            if msg["id"] == msg_id:
                if msg["author_id"] != me["id"]:
                    return jsonify({"error": "Only the message author can edit it."}), 403
                msg["text"] = new_text[:2000]
                msg["edited"] = True
                save_state(state)
                return jsonify({"ok": True})

        return jsonify({"error": "Message not found."}), 404


@app.route("/api/messages/delete", methods=["POST"])
def delete_message():
    payload = request.get_json(silent=True) or {}
    kind = payload.get("kind")
    target_id = payload.get("target_id")
    msg_id = payload.get("msg_id")

    with STATE_LOCK:
        state = load_state()
        me = current_user(state)
        if not me:
            return jsonify({"error": "Not logged in."}), 401

        room = resolve_room(state, me["id"], kind, target_id)
        if not room:
            return jsonify({"error": "Chat not found."}), 404

        for idx, msg in enumerate(room["messages"]):
            if msg["id"] == msg_id:
                if msg["author_id"] != me["id"]:
                    return jsonify({"error": "Only the message author can delete it."}), 403
                room["messages"].pop(idx)
                save_state(state)
                return jsonify({"ok": True})

        return jsonify({"error": "Message not found."}), 404


@app.route("/api/messages/react", methods=["POST"])
def react_message():
    payload = request.get_json(silent=True) or {}
    kind = payload.get("kind")
    target_id = payload.get("target_id")
    msg_id = payload.get("msg_id")
    emoji = str(payload.get("emoji", "")).strip()

    if emoji not in ALLOWED_REACTIONS:
        return jsonify({"error": "Unsupported reaction."}), 400

    with STATE_LOCK:
        state = load_state()
        me = current_user(state)
        if not me:
            return jsonify({"error": "Not logged in."}), 401

        room = resolve_room(state, me["id"], kind, target_id)
        if not room:
            return jsonify({"error": "Chat not found."}), 404

        for msg in room["messages"]:
            if msg["id"] == msg_id:
                reactions = msg.setdefault("reactions", {})
                voters = reactions.setdefault(emoji, [])
                if me["id"] in voters:
                    voters.remove(me["id"])
                    if not voters:
                        reactions.pop(emoji, None)
                else:
                    voters.append(me["id"])
                save_state(state)
                return jsonify({"reactions": reactions})

        return jsonify({"error": "Message not found."}), 404


@app.route("/api/bulletin/post", methods=["POST"])
def post_bulletin():
    payload = request.get_json(silent=True) or {}
    title = str(payload.get("title", "")).strip()[:100]
    body = str(payload.get("body", "")).strip()[:1000]
    if not title or not body:
        return jsonify({"error": "Title and body are required."}), 400

    with STATE_LOCK:
        state = load_state()
        me = current_user(state)
        if not me:
            return jsonify({"error": "Not logged in."}), 401

        post = {
            "id": f"b_{uuid.uuid4().hex[:10]}",
            "author_id": me["id"],
            "author_username": me["username"],
            "author_avatar": me.get("avatar", ""),
            "title": title,
            "body": body,
            "ts": int(time.time() * 1000),
        }
        state["bulletin"].insert(0, post)
        state["bulletin"] = state["bulletin"][:100]
        save_state(state)
        return jsonify({"post": post})


@app.route("/api/bulletin/delete", methods=["POST"])
def delete_bulletin():
    payload = request.get_json(silent=True) or {}
    post_id = payload.get("post_id")

    with STATE_LOCK:
        state = load_state()
        me = current_user(state)
        if not me:
            return jsonify({"error": "Not logged in."}), 401

        for idx, post in enumerate(state["bulletin"]):
            if post["id"] == post_id:
                if post["author_id"] != me["id"]:
                    return jsonify({"error": "Only the author can delete this post."}), 403
                state["bulletin"].pop(idx)
                save_state(state)
                return jsonify({"ok": True})

        return jsonify({"error": "Post not found."}), 404


@app.route("/api/polls/create", methods=["POST"])
def create_poll():
    payload = request.get_json(silent=True) or {}
    question = str(payload.get("question", "")).strip()[:160]
    raw_options = payload.get("options", [])
    options = []
    if isinstance(raw_options, list):
        for raw_opt in raw_options:
            label = str(raw_opt).strip()[:80]
            if label:
                options.append({"id": f"o_{uuid.uuid4().hex[:6]}", "label": label, "votes": []})

    if not question or len(options) < 2:
        return jsonify({"error": "Provide a question and at least two options."}), 400

    with STATE_LOCK:
        state = load_state()
        me = current_user(state)
        if not me:
            return jsonify({"error": "Not logged in."}), 401

        poll = {
            "id": f"p_{uuid.uuid4().hex[:10]}",
            "author_id": me["id"],
            "author_username": me["username"],
            "question": question,
            "options": options[:8],
            "ts": int(time.time() * 1000),
        }
        state["polls"].insert(0, poll)
        state["polls"] = state["polls"][:100]
        save_state(state)
        return jsonify({"poll": poll})


@app.route("/api/polls/vote", methods=["POST"])
def vote_poll():
    payload = request.get_json(silent=True) or {}
    poll_id = payload.get("poll_id")
    option_id = payload.get("option_id")

    with STATE_LOCK:
        state = load_state()
        me = current_user(state)
        if not me:
            return jsonify({"error": "Not logged in."}), 401

        for poll in state["polls"]:
            if poll["id"] == poll_id:
                for option in poll["options"]:
                    if me["id"] in option["votes"]:
                        option["votes"].remove(me["id"])
                for option in poll["options"]:
                    if option["id"] == option_id:
                        option["votes"].append(me["id"])
                        save_state(state)
                        return jsonify({"ok": True})

        return jsonify({"error": "Poll or option not found."}), 404


@app.route("/api/sync")
def sync():
    kind = request.args.get("kind", "friends")
    target_id = request.args.get("target_id", "friends")
    now = time.time()

    with STATE_LOCK:
        state = load_state()
        me = current_user(state)
        if not me:
            return jsonify({"authenticated": False})

        for room in state["gcs"].values():
            if room.get("is_public") and me["id"] not in room["members"]:
                room["members"].append(me["id"])

        users_dir = {uid: public_user(u) for uid, u in state["users"].items()}
        friends = [users_dir[fid] for fid in me["friends"] if fid in users_dir]
        incoming = [users_dir[iid] for iid in me["incoming"] if iid in users_dir]
        outgoing = [users_dir[oid] for oid in me["outgoing"] if oid in users_dir]

        dm_list = []
        seen_dm_users = set()
        for fid in me["friends"]:
            if fid not in users_dir:
                continue
            seen_dm_users.add(fid)
            dm_room = state["dms"].get(dm_key(me["id"], fid))
            is_current_dm = kind == "dm" and target_id == fid
            if is_current_dm and dm_room:
                mark_room_seen(dm_room, me["id"])
            unread = False if is_current_dm else has_unseen_messages(dm_room, me["id"])
            dm_list.append({"user": users_dir[fid], "unread": unread})

        for dm_room in state["dms"].values():
            if me["id"] in dm_room["members"]:
                other_ids = [mid for mid in dm_room["members"] if mid != me["id"]]
                if other_ids:
                    other_id = other_ids[0]
                    if other_id not in seen_dm_users and other_id in users_dir:
                        is_current_dm = kind == "dm" and target_id == other_id
                        if is_current_dm:
                            mark_room_seen(dm_room, me["id"])
                        unread = False if is_current_dm else has_unseen_messages(dm_room, me["id"])
                        dm_list.append({"user": users_dir[other_id], "unread": unread})

        gc_list = []
        for room in state["gcs"].values():
            if room.get("is_public") or me["id"] in room["members"]:
                is_current_gc = kind == "gc" and target_id == room["id"]
                if is_current_gc:
                    mark_room_seen(room, me["id"])
                    room.setdefault("mention_unread", {})[me["id"]] = False
                unread = False if is_current_gc else has_unseen_messages(room, me["id"])
                gc_list.append(
                    {
                        "id": room["id"],
                        "name": room["name"],
                        "is_public": bool(room.get("is_public", False)),
                        "unread": unread,
                        "mention_unread": bool(room.get("mention_unread", {}).get(me["id"], False)),
                        "member_count": len(room["members"]),
                    }
                )

        active_messages = []
        active_members = []
        typing_users = []
        is_public = False

        room = resolve_room(state, me["id"], kind, target_id)
        if kind == "dm" and target_id in users_dir:
            active_members = [users_dir[me["id"]], users_dir[target_id]]
        elif kind == "gc" and room:
            is_public = bool(room.get("is_public", False))
            active_members = [users_dir[mid] for mid in room["members"] if mid in users_dir]

        if room:
            active_messages = room.get("messages", [])
            for uid, last_ts in list(room.get("typing", {}).items()):
                if uid != me["id"] and (now - last_ts) < 3.0 and uid in users_dir:
                    typing_users.append(users_dir[uid]["username"])

        save_state(state)
        return jsonify(
            {
                "authenticated": True,
                "me": public_user(me),
                "users": users_dir,
                "friends": friends,
                "incoming": incoming,
                "outgoing": outgoing,
                "dms": dm_list,
                "gcs": gc_list,
                "bulletin": state.get("bulletin", []),
                "polls": state.get("polls", []),
                "active": {
                    "kind": kind,
                    "target_id": target_id,
                    "is_public": is_public,
                    "messages": active_messages,
                    "members": active_members,
                    "typing": typing_users,
                },
            }
        )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)