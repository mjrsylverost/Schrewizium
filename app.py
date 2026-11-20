import json
import os
import re
import threading
import time
import uuid
from typing import Any

from flask import Flask, jsonify, render_template, request, session

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "schrewizium-clean-rewrite-key-2026")

DATA_FILE = "schrewizium_state.json"
STATE_LOCK = threading.Lock()
USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.]{2,20}$")
VALID_STATUSES = {"online", "idle", "dnd", "offline"}


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


def load_state() -> dict[str, Any]:
    if not os.path.exists(DATA_FILE):
        state = default_state()
        save_state(state)
        return state
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        if not isinstance(loaded, dict):
            return default_state()
    except (OSError, json.JSONDecodeError):
        return default_state()

    loaded.setdefault("users", {})
    loaded.setdefault("username_map", {})
    loaded.setdefault("dms", {})
    loaded.setdefault("gcs", {})
    loaded.setdefault("bulletin", [])
    loaded.setdefault("polls", [])
    if "gc_general" not in loaded["gcs"]:
        loaded["gcs"]["gc_general"] = make_general_gc()
    return loaded


def save_state(state: dict[str, Any]) -> None:
    temp_path = f"{DATA_FILE}.tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)
    os.replace(temp_path, DATA_FILE)


def clean_username(raw: Any) -> str:
    if not raw:
        return ""
    return re.sub(r"\s+", "", str(raw).strip())


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


@app.route("/")
def index() -> str:
    return render_template("index.html")


@app.route("/api/auth", methods=["POST"])
def auth():
    payload = request.get_json(silent=True) or {}
    username = clean_username(payload.get("username", ""))
    if not USERNAME_RE.match(username):
        return (
            jsonify(
                {
                    "error": (
                        "Username must be 2-20 characters using letters, "
                        "numbers, '_' or '.' with no spaces."
                    )
                }
            ),
            400,
        )

    lower_key = username.lower()
    with STATE_LOCK:
        state = load_state()
        if lower_key in state["username_map"]:
            uid = state["username_map"][lower_key]
            user = state["users"][uid]
        else:
            uid = f"u_{uuid.uuid4().hex[:10]}"
            user = {
                "id": uid,
                "username": username,
                "status": "online",
                "custom_status": "",
                "avatar": str(payload.get("avatar", "")).strip()[:300],
                "bio": str(payload.get("bio", "")).strip()[:200],
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
                return jsonify({"error": "Invalid username (no spaces allowed)."}), 400
            new_key = new_username.lower()
            existing_id = state["username_map"].get(new_key)
            if existing_id and existing_id != me["id"]:
                return jsonify({"error": "Username is already taken."}), 409
            del state["username_map"][me["username"].lower()]
            state["username_map"][new_key] = me["id"]
            me["username"] = new_username

        status = payload.get("status", me.get("status", "online"))
        if status in VALID_STATUSES:
            me["status"] = status

        me["custom_status"] = str(payload.get("custom_status", me.get("custom_status", "")))[:60]
        me["avatar"] = str(payload.get("avatar", me.get("avatar", "")))[:300]
        me["bio"] = str(payload.get("bio", me.get("bio", "")))[:200]

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

        return jsonify({"results": matches[:15]})


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
                target["incoming"].append(tid)

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
        if me["id"] in target["outgoing"]:
            target["outgoing"].remove(me["id"])

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
                    "typing": {},
                }

        save_state(state)
        return jsonify({"ok": True})


@app.route("/api/gcs/create", methods=["POST"])
def create_gc():
    payload = request.get_json(silent=True) or {}
    name = re.sub(r"\s+", "-", str(payload.get("name", "")).strip())[:30]
    is_public = bool(payload.get("is_public", False))
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
        elif kind == "gc" and target_id:
            room = state["gcs"].get(target_id)
            if room:
                room.setdefault("mention_unread", {})[me["id"]] = False

        save_state(state)
        return jsonify({"ok": True})


@app.route("/api/messages/send", methods=["POST"])
def send_message():
    payload = request.get_json(silent=True) or {}
    kind = payload.get("kind")
    target_id = payload.get("target_id")
    text = str(payload.get("text", "")).strip()
    reply_to_id = payload.get("reply_to")

    if not text:
        return jsonify({"error": "Message cannot be empty."}), 400

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
                        "text": existing["text"][:90],
                    }
                    break

        mentioned_ids = []
        if kind == "gc":
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
                    room.setdefault("mention_unread", {})[member_id] = True

        message = {
            "id": f"m_{uuid.uuid4().hex[:12]}",
            "author_id": me["id"],
            "author_username": me["username"],
            "author_avatar": me.get("avatar", ""),
            "text": text[:2000],
            "ts": int(time.time() * 1000),
            "edited": False,
            "reply_to": reply_snapshot,
            "mentions": mentioned_ids,
        }

        room["messages"].append(message)
        room["messages"] = room["messages"][-300:]
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
                if "pins" in room and msg_id in room["pins"]:
                    room["pins"].remove(msg_id)
                save_state(state)
                return jsonify({"ok": True})

        return jsonify({"error": "Message not found."}), 404


@app.route("/api/gcs/pin", methods=["POST"])
def toggle_pin():
    payload = request.get_json(silent=True) or {}
    gc_id = payload.get("gc_id")
    msg_id = payload.get("msg_id")

    with STATE_LOCK:
        state = load_state()
        me = current_user(state)
        if not me:
            return jsonify({"error": "Not logged in."}), 401

        room = state["gcs"].get(gc_id) if gc_id else None
        if not room:
            return jsonify({"error": "Group chat not found."}), 404
        if room.get("is_public"):
            return jsonify({"error": "Pinning is disabled on public group chats."}), 403
        if me["id"] not in room["members"]:
            return jsonify({"error": "Not a member of this group chat."}), 403

        pins = room.setdefault("pins", [])
        if msg_id in pins:
            pins.remove(msg_id)
        elif any(m["id"] == msg_id for m in room["messages"]):
            pins.append(msg_id)

        save_state(state)
        return jsonify({"ok": True, "pins": pins})


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
            unread = bool(dm_room.get("unread_for", {}).get(me["id"], False)) if dm_room else False
            dm_list.append({"user": users_dir[fid], "unread": unread})

        for dm_room in state["dms"].values():
            if me["id"] in dm_room["members"]:
                other_ids = [mid for mid in dm_room["members"] if mid != me["id"]]
                if other_ids:
                    other_id = other_ids[0]
                    if other_id not in seen_dm_users and other_id in users_dir:
                        unread = bool(dm_room.get("unread_for", {}).get(me["id"], False))
                        dm_list.append({"user": users_dir[other_id], "unread": unread})

        gc_list = []
        for room in state["gcs"].values():
            if room.get("is_public") or me["id"] in room["members"]:
                gc_list.append(
                    {
                        "id": room["id"],
                        "name": room["name"],
                        "is_public": bool(room.get("is_public", False)),
                        "mention_unread": bool(room.get("mention_unread", {}).get(me["id"], False)),
                        "member_count": len(room["members"]),
                    }
                )

        active_messages = []
        active_members = []
        active_pins = []
        typing_users = []
        is_public = False

        room = resolve_room(state, me["id"], kind, target_id)
        if kind == "dm" and target_id in users_dir:
            active_members = [users_dir[me["id"]], users_dir[target_id]]
        elif kind == "gc" and room:
            is_public = bool(room.get("is_public", False))
            active_members = [users_dir[mid] for mid in room["members"] if mid in users_dir]
            pin_ids = set(room.get("pins", []))
            active_pins = [m for m in room["messages"] if m["id"] in pin_ids]

        if room:
            active_messages = room.get("messages", [])
            for uid, last_ts in list(room.get("typing", {}).items()):
                if uid != me["id"] and (now - last_ts) < 3.0 and uid in users_dir:
                    typing_users.append(users_dir[uid]["username"])

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
                    "pins": active_pins,
                    "typing": typing_users,
                },
            }
        )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)