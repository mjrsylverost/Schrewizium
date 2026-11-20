import json
import os
import queue
import threading
import uuid
from datetime import datetime, timezone

from flask import Flask, Response, jsonify, render_template_string, request

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "schrewizium_data.json")
STATE_LOCK = threading.Lock()
SUBSCRIBERS = []
SUB_LOCK = threading.Lock()

START_ESF = 2000
BULLETINS = ["International", "Anime", "Gaming", "Music", "Tech", "Creative Writing"]


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def default_state():
    return {
        "users": {},
        "posts": {},
        "dms": {},
        "friends": {},
        "friend_requests": {},
        "groups": {
            "hearth-general": {
                "id": "hearth-general",
                "name": "hearth-general",
                "topic": "International winter parlour hall. Polls, ||spoilers||, reactions, and @mentions.",
                "visibility": "public",
                "invite_code": "HEARTH26",
                "slowmode": 0,
                "owner": "System",
                "members": ["Anonymous"],
                "pinned": [],
                "polls": [],
                "messages": [],
                "created_at": now_iso(),
            }
        },
        "rooms": {},
    }


def read_state():
    with STATE_LOCK:
        if not os.path.exists(DATA_FILE):
            data = default_state()
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            return data
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
                if not isinstance(data, dict):
                    data = default_state()
            except json.JSONDecodeError:
                data = default_state()
        for k, v in default_state().items():
            if k not in data or not isinstance(data[k], dict):
                data[k] = v
        for udoc in data["users"].values():
            udoc.setdefault("esf", START_ESF)
            udoc.setdefault("status", "Online")
            udoc.setdefault("pfp", "")
            udoc.setdefault("bio", "")
            udoc.setdefault("banner_color", "#1f7347")
        for gid, g in data["groups"].items():
            g.setdefault("visibility", "public")
            g.setdefault("invite_code", gid[:6].upper())
            g.setdefault("slowmode", 0)
            g.setdefault("owner", "System")
            g.setdefault("members", [])
            g.setdefault("pinned", [])
            g.setdefault("polls", [])
            g.setdefault("messages", [])
        return data


def write_state(data):
    with STATE_LOCK:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)


def broadcast(event, payload):
    msg = f"event: {event}\ndata: {json.dumps(payload)}\n\n"
    with SUB_LOCK:
        dead = []
        for q in SUBSCRIBERS:
            try:
                q.put_nowait(msg)
            except Exception:
                dead.append(q)
        for q in dead:
            SUBSCRIBERS.remove(q)


def can_access_group(g, user):
    if g.get("visibility", "public") == "public":
        return True
    if not user:
        return False
    u = user.lower()
    if g.get("owner", "").lower() == u:
        return True
    return any(m.lower() == u for m in g.get("members", []))


HTML_UI = """<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>MERRY CHRISTMAS // SCHREWIZIUM</title>
  <style>
    :root {
      --bg-top: #07161a;
      --bg-mid: #0e262d;
      --panel: rgba(12, 29, 34, 0.9);
      --card: rgba(19, 43, 49, 0.94);
      --input: rgba(7, 17, 21, 0.94);
      --accent: #f3c669;
      --accent-soft: rgba(243, 198, 105, 0.18);
      --crimson: #9f243b;
      --pine: #1f7347;
      --ink: #f1f5f9;
      --muted: #9bb2ba;
      --line: rgba(243, 198, 105, 0.25);
      --font-scale: 15px;
    }
    body.theme-burgundy {
      --bg-top: #1b0910; --bg-mid: #30111c; --panel: rgba(38, 15, 24, 0.9);
      --card: rgba(52, 22, 33, 0.94); --input: rgba(22, 8, 13, 0.94);
      --accent: #f6ce7b; --line: rgba(246, 206, 123, 0.28);
    }
    body.theme-frost {
      --bg-top: #0b1c30; --bg-mid: #143252; --panel: rgba(16, 38, 63, 0.9);
      --card: rgba(24, 53, 85, 0.94); --input: rgba(10, 23, 39, 0.94);
      --accent: #7dd3fc; --accent-soft: rgba(125, 211, 252, 0.18); --line: rgba(125, 211, 252, 0.28);
    }
    body.theme-midnight {
      --bg-top: #090b10; --bg-mid: #121724; --panel: rgba(18, 23, 36, 0.92);
      --card: rgba(26, 34, 50, 0.94); --input: rgba(9, 12, 19, 0.94);
      --accent: #d4af37; --line: rgba(212, 175, 55, 0.25);
    }

    * { box-sizing: border-box; }
    html { font-size: var(--font-scale); }
    body {
      margin: 0; min-height: 100vh; color: var(--ink);
      font-family: "Inter", "Segoe UI", system-ui, sans-serif;
      background:
        radial-gradient(circle at 84% 12%, rgba(255, 248, 220, 0.14), transparent 24%),
        linear-gradient(180deg, var(--bg-top) 0%, var(--bg-mid) 65%, #08161a 100%);
      overflow-x: hidden;
    }
    .vic-title { font-family: "Georgia", "Palatino Linotype", serif; letter-spacing: 0.5px; }

    #snowCanvas { position: fixed; inset: 0; width: 100%; height: 100%; pointer-events: none; z-index: 0; }
    .winter-landscape { position: fixed; left: 0; right: 0; bottom: 0; height: 185px; pointer-events: none; z-index: 0; opacity: 0.85; }

    /* CHRISTMAS BANNER + TWINKLING LIGHTS + GARLAND */
    .xmas-banner {
      position: relative; z-index: 35;
      background: linear-gradient(90deg, #7f1d1d, #14532d, #991b1b, #14532d, #7f1d1d);
      background-size: 200% 100%;
      color: #fef08a; text-align: center; padding: 9px 14px;
      font-weight: 800; letter-spacing: 2.5px; font-size: 1.05rem;
      border-bottom: 1px solid rgba(254, 240, 138, 0.45);
      animation: xmasGlow 2.2s ease-in-out infinite alternate, bannerShift 8s linear infinite;
    }
    @keyframes bannerShift {
      0% { background-position: 0% 50%; }
      100% { background-position: 200% 50%; }
    }
    @keyframes xmasGlow {
      from { text-shadow: 0 0 6px rgba(254, 240, 138, 0.45); }
      to { text-shadow: 0 0 18px rgba(254, 240, 138, 1), 0 0 28px rgba(248, 113, 113, 0.7); }
    }
    .xmas-lights {
      position: relative; z-index: 34;
      display: flex; justify-content: space-around; align-items: center;
      padding: 5px 12px; background: rgba(6, 15, 18, 0.88);
      border-bottom: 1px solid rgba(255,255,255,0.07);
    }
    .bulb {
      width: 10px; height: 10px; border-radius: 50%;
      animation: blinkBulb 1.3s infinite alternate;
    }
    .bulb:nth-child(4n+1) { background: #ef4444; box-shadow: 0 0 10px #ef4444; animation-delay: 0s; }
    .bulb:nth-child(4n+2) { background: #22c55e; box-shadow: 0 0 10px #22c55e; animation-delay: 0.32s; }
    .bulb:nth-child(4n+3) { background: #facc15; box-shadow: 0 0 10px #facc15; animation-delay: 0.65s; }
    .bulb:nth-child(4n+4) { background: #38bdf8; box-shadow: 0 0 10px #38bdf8; animation-delay: 0.98s; }
    @keyframes blinkBulb {
      0% { opacity: 0.35; transform: scale(0.88); }
      100% { opacity: 1; transform: scale(1.22); }
    }

    /* FLOATING CHRISTMAS ORNAMENTS */
    .xmas-ornaments {
      position: fixed; top: 82px; left: 0; right: 0; pointer-events: none; z-index: 2;
      display: flex; justify-content: space-between; padding: 0 28px;
    }
    .ornament {
      width: 18px; height: 18px; border-radius: 50%;
      border: 2px solid #fef08a; transform-origin: top center;
      animation: swingOrnament 3.4s ease-in-out infinite alternate;
    }
    .ornament.red { background: radial-gradient(circle at 30% 30%, #fca5a5, #991b1b); }
    .ornament.green { background: radial-gradient(circle at 30% 30%, #86efac, #14532d); animation-delay: 1.1s; }
    .ornament.gold { background: radial-gradient(circle at 30% 30%, #fef08a, #b45309); animation-delay: 2.1s; }
    @keyframes swingOrnament {
      from { transform: rotate(-9deg); }
      to { transform: rotate(9deg); }
    }

    .topbar {
      position: sticky; top: 0; z-index: 30;
      display: flex; justify-content: space-between; align-items: center; gap: 12px;
      padding: 10px 20px; background: rgba(7, 17, 21, 0.92); backdrop-filter: blur(12px);
      border-bottom: 1px solid var(--line);
    }
    .plus-wrap { position: relative; }
    .plus-menu {
      position: absolute; right: 0; top: 44px; min-width: 230px;
      background: var(--card); border: 1px solid var(--line); border-radius: 12px;
      padding: 8px; box-shadow: 0 14px 32px rgba(0,0,0,0.6); display: none; z-index: 50;
    }
    .plus-menu.open { display: block; }
    .plus-item {
      width: 100%; display: flex; justify-content: space-between; align-items: center;
      padding: 9px 11px; border: none; border-radius: 8px; background: transparent;
      color: var(--ink); font-weight: 600; font-size: 0.88rem; cursor: pointer; text-align: left;
    }
    .plus-item:hover:not(.locked) { background: rgba(255,255,255,0.08); }
    .plus-item.locked { opacity: 0.65; cursor: not-allowed; }

    .workspace {
      position: relative; z-index: 1; max-width: 1560px; margin: 0 auto;
      padding: 16px 20px 140px; display: grid;
      grid-template-columns: 270px minmax(0, 1fr) 285px; gap: 18px; align-items: start;
    }
    body.compact-mode .workspace { gap: 10px; padding: 10px 12px 120px; }
    @media (max-width: 1140px) { .workspace { grid-template-columns: 235px minmax(0, 1fr); } .right-sidebar { display: none; } }
    @media (max-width: 780px) { .workspace { grid-template-columns: 1fr; } }

    .panel {
      background: var(--panel); backdrop-filter: blur(12px);
      border: 1px solid var(--line); border-top: 2px solid var(--accent);
      border-radius: 14px; box-shadow: 0 12px 32px rgba(0, 0, 0, 0.36);
    }
    .left-sidebar { padding: 14px; }
    .nav-heading {
      font-size: 0.72rem; text-transform: uppercase; letter-spacing: 1px;
      color: var(--muted); font-weight: 700; margin: 14px 8px 6px;
    }
    .nav-btn {
      width: 100%; display: flex; justify-content: space-between; align-items: center;
      padding: 9px 12px; margin-bottom: 4px; border-radius: 9px;
      border: 1px solid transparent; background: transparent; color: var(--ink);
      font-size: 0.9rem; font-weight: 600; cursor: pointer; text-align: left;
    }
    .nav-btn:hover:not(.locked) { background: rgba(255, 255, 255, 0.06); }
    .nav-btn.active { background: var(--accent-soft); border-color: var(--accent); color: var(--accent); }
    .nav-btn.locked { opacity: 0.62; cursor: not-allowed; }
    .upcoming-tag {
      font-size: 0.68rem; padding: 2px 7px; border-radius: 999px;
      background: rgba(248, 113, 113, 0.16); color: #fca5a5; border: 1px solid rgba(248, 113, 113, 0.35);
    }
    .vis-badge {
      font-size: 0.68rem; padding: 2px 6px; border-radius: 6px;
      background: rgba(255,255,255,0.08); color: var(--muted); text-transform: uppercase;
    }

    .center-stage { padding: 20px; min-height: 640px; }
    .stage-header {
      display: flex; justify-content: space-between; align-items: center;
      flex-wrap: wrap; gap: 12px; padding-bottom: 14px; margin-bottom: 16px;
      border-bottom: 1px solid var(--line);
    }
    .bulletin-pills { display: flex; gap: 7px; flex-wrap: wrap; margin-bottom: 12px; }

    .btn {
      padding: 8px 14px; border-radius: 9px; border: 1px solid rgba(255,255,255,0.14);
      background: rgba(255,255,255,0.07); color: var(--ink); font-weight: 600;
      font-size: 0.85rem; cursor: pointer;
    }
    .btn:hover { background: rgba(255,255,255,0.13); }
    .btn.gold { background: var(--accent); color: #120d04; border-color: var(--accent); }
    .btn.pine { background: var(--pine); color: #fff; border-color: #34d399; }
    .btn.crimson { background: var(--crimson); color: #fff; border-color: #fb7185; }
    .btn.sm { padding: 4px 10px; font-size: 0.78rem; border-radius: 7px; }

    input, textarea, select {
      width: 100%; padding: 9px 12px; border-radius: 9px; border: 1px solid var(--line);
      background: var(--input); color: var(--ink); font-size: 0.9rem; margin-bottom: 10px; font-family: inherit;
    }
    input:focus, textarea:focus, select:focus { outline: none; border-color: var(--accent); }

    .card-item {
      background: var(--card); border: 1px solid rgba(255,255,255,0.08);
      border-radius: 12px; padding: 15px; margin-bottom: 13px;
    }
    .flair {
      display: inline-block; padding: 2px 9px; border-radius: 999px; font-size: 0.74rem;
      font-weight: 700; background: var(--accent-soft); color: var(--accent); border: 1px solid var(--line);
    }
    .meta { font-size: 0.8rem; color: var(--muted); }
    .replies-wrap {
      margin-top: 12px; padding: 12px; border-radius: 10px;
      background: rgba(5,12,15,0.55); border: 1px solid rgba(255,255,255,0.06);
    }
    .reply-entry {
      display: flex; justify-content: space-between; align-items: flex-start; gap: 10px;
      padding: 7px 0 7px 11px; border-left: 2px solid var(--accent); margin-bottom: 8px;
    }

    /* Spoilers, Mentions, Reactions, Polls, Group Chat */
    .spoiler {
      background: rgba(255, 255, 255, 0.22); color: transparent; border-radius: 4px;
      padding: 0 4px; cursor: pointer; user-select: none;
    }
    .spoiler.revealed { background: rgba(255, 255, 255, 0.1); color: var(--ink); }
    .mention-chip {
      color: var(--accent); background: var(--accent-soft); padding: 1px 5px;
      border-radius: 5px; font-weight: 600;
    }
    .react-pill {
      padding: 2px 8px; border-radius: 999px; border: 1px solid rgba(255,255,255,0.12);
      background: rgba(255,255,255,0.05); color: var(--ink); font-size: 0.76rem; cursor: pointer;
    }
    .react-pill.active { border-color: var(--accent); background: var(--accent-soft); }
    .poll-card {
      background: rgba(5, 12, 15, 0.65); border: 1px solid var(--line);
      border-radius: 10px; padding: 12px; margin-bottom: 12px;
    }
    .chat-layout { display: grid; grid-template-columns: 1fr 215px; gap: 14px; }
    @media (max-width: 960px) { .chat-layout { grid-template-columns: 1fr; } }
    .pinned-banner {
      padding: 8px 12px; border-radius: 9px; background: var(--accent-soft);
      border: 1px solid var(--line); font-size: 0.82rem; margin-bottom: 10px;
    }
    .msg-row { padding: 9px 11px; border-radius: 9px; margin-bottom: 6px; background: rgba(255, 255, 255, 0.03); }
    .msg-row:hover { background: rgba(255, 255, 255, 0.06); }
    .reply-quote { font-size: 0.78rem; color: var(--muted); border-left: 2px solid var(--accent); padding-left: 8px; margin-bottom: 4px; }

    /* Discord / Telegram style DMs & Friends */
    .dm-grid { display: grid; grid-template-columns: 260px minmax(0, 1fr); gap: 14px; min-height: 460px; }
    @media (max-width: 820px) { .dm-grid { grid-template-columns: 1fr; } }
    .friend-pane {
      background: rgba(5, 12, 15, 0.55); border: 1px solid rgba(255,255,255,0.08);
      border-radius: 11px; padding: 12px;
    }
    .friend-row {
      display: flex; justify-content: space-between; align-items: center;
      padding: 8px 10px; border-radius: 8px; cursor: pointer; margin-bottom: 5px;
      background: rgba(255,255,255,0.03);
    }
    .friend-row:hover { background: rgba(255,255,255,0.08); }
    .friend-row.active { background: var(--accent-soft); border: 1px solid var(--accent); }
    .status-dot { width: 8px; height: 8px; border-radius: 50%; background: #22c55e; display: inline-block; margin-right: 6px; }
    .dm-chat-box {
      display: flex; flex-direction: column; justify-content: space-between;
      background: rgba(5, 12, 15, 0.45); border: 1px solid rgba(255,255,255,0.08);
      border-radius: 11px; padding: 12px;
    }
    .dm-bubble {
      padding: 9px 12px; border-radius: 10px; margin-bottom: 8px;
      background: rgba(255,255,255,0.05); border-left: 3px solid var(--accent);
    }
    .dm-bubble.mine { background: var(--accent-soft); border-left-color: #34d399; }

    .right-sidebar { padding: 16px; }
    .avatar {
      width: 48px; height: 48px; border-radius: 50%;
      background: linear-gradient(135deg, var(--crimson), var(--pine));
      border: 1.5px solid var(--accent); display: flex; align-items: center;
      justify-content: center; font-weight: 700; color: #fff; overflow: hidden; flex-shrink: 0;
    }
    .avatar img { width: 100%; height: 100%; object-fit: cover; }

    .modal {
      position: fixed; inset: 0; background: rgba(0,0,0,0.76); backdrop-filter: blur(5px);
      display: none; align-items: center; justify-content: center; padding: 16px; z-index: 60;
    }
    .modal.open { display: flex; }
    .modal-box { width: 100%; max-width: 550px; max-height: 88vh; overflow-y: auto; padding: 20px; }
    .setting-row {
      display: flex; justify-content: space-between; align-items: center; gap: 12px;
      padding: 10px 0; border-bottom: 1px solid rgba(255,255,255,0.08);
    }
    .set-nav { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 12px; }
  </style>
</head>
<body>
  <div class="xmas-banner vic-title">MERRY CHRISTMAS</div>
  <div class="xmas-lights" aria-hidden="true">
    <span class="bulb"></span><span class="bulb"></span><span class="bulb"></span><span class="bulb"></span>
    <span class="bulb"></span><span class="bulb"></span><span class="bulb"></span><span class="bulb"></span>
    <span class="bulb"></span><span class="bulb"></span><span class="bulb"></span><span class="bulb"></span>
    <span class="bulb"></span><span class="bulb"></span><span class="bulb"></span><span class="bulb"></span>
    <span class="bulb"></span><span class="bulb"></span><span class="bulb"></span><span class="bulb"></span>
  </div>

  <div class="xmas-ornaments" aria-hidden="true">
    <span class="ornament red"></span>
    <span class="ornament green"></span>
    <span class="ornament gold"></span>
    <span class="ornament red"></span>
  </div>

  <canvas id="snowCanvas"></canvas>

  <svg class="winter-landscape" viewBox="0 0 1440 220" preserveAspectRatio="none" aria-hidden="true">
    <path fill="#0a1b20" d="M0,165 Q240,120 480,155 T960,145 T1440,160 L1440,220 L0,220 Z" />
    <g fill="#0c2426">
      <polygon points="90,55 55,165 125,165" /><polygon points="90,85 45,185 135,185" />
      <polygon points="210,70 180,175 240,175" /><polygon points="360,50 320,170 400,170" />
      <polygon points="1080,60 1045,170 1115,170" /><polygon points="1230,45 1185,175 1275,175" />
      <polygon points="1350,68 1315,180 1385,180" />
    </g>
    <g fill="#071618">
      <polygon points="145,35 100,140 190,140" /><polygon points="145,75 88,185 202,185" />
      <polygon points="285,62 248,185 322,185" /><polygon points="1155,40 1112,145 1198,145" />
      <polygon points="1155,80 1100,190 1210,190" /><polygon points="1295,58 1255,190 1335,190" />
    </g>
    <path fill="rgba(226, 241, 248, 0.14)" d="M0,185 Q360,155 720,182 T1440,175 L1440,220 L0,220 Z" />
  </svg>

  <header class="topbar">
    <span class="vic-title" style="font-size:1.18rem;font-weight:700;color:var(--accent)">SCHREWIZIUM</span>
    <div class="plus-wrap">
      <button class="btn gold" onclick="togglePlusMenu(event)" style="font-size:1.08rem;padding:6px 15px">+</button>
      <div id="plusMenu" class="plus-menu">
        <button class="plus-item" onclick="closePlusMenu();openModal('forumModal')"><span>+ New Forum</span></button>
        <button class="plus-item" onclick="closePlusMenu();openModal('roomModal')"><span>+ Game Room</span></button>
        <button class="plus-item" onclick="closePlusMenu();openModal('createGroupModal')"><span>+ Group Chat</span></button>
        <button class="plus-item" onclick="closePlusMenu();openAccount()"><span>Account</span></button>
        <button class="plus-item" onclick="closePlusMenu();openSettings()"><span>Settings</span></button>
        <button class="plus-item locked" disabled><span>ESF Bank</span><span class="upcoming-tag">upcoming</span></button>
        <button class="plus-item locked" disabled><span>Community Hub</span><span class="upcoming-tag">upcoming</span></button>
      </div>
    </div>
  </header>

  <div class="workspace">
    <aside class="panel left-sidebar">
      <div class="nav-heading">Spaces</div>
      <button class="nav-btn active" id="nav-bulletins" onclick="showSection('bulletins')"><span>Bulletins</span></button>
      <button class="nav-btn" id="nav-groups" onclick="showSection('groups')"><span>Group Chats</span></button>
      <button class="nav-btn" id="nav-dms" onclick="showSection('dms')"><span>DMs</span></button>

      <div class="nav-heading">Bulletin Boards</div>
      <button class="nav-btn active" id="sb-International" onclick="selectBulletin('International')"><span>International (Main)</span></button>
      <button class="nav-btn" id="sb-Anime" onclick="selectBulletin('Anime')"><span>Anime</span></button>
      <button class="nav-btn" id="sb-Gaming" onclick="selectBulletin('Gaming')"><span>Gaming</span></button>
      <button class="nav-btn" id="sb-Music" onclick="selectBulletin('Music')"><span>Music</span></button>
      <button class="nav-btn" id="sb-Tech" onclick="selectBulletin('Tech')"><span>Tech</span></button>
      <button class="nav-btn" id="sb-Creative Writing" onclick="selectBulletin('Creative Writing')"><span>Creative Writing</span></button>

      <div class="nav-heading">Locked</div>
      <button class="nav-btn locked" disabled><span>Minigames</span><span class="upcoming-tag">upcoming</span></button>
      <button class="nav-btn locked" disabled><span>Arcade</span><span class="upcoming-tag">upcoming</span></button>
      <button class="nav-btn locked" disabled><span>ESF Bank</span><span class="upcoming-tag">upcoming</span></button>
      <button class="nav-btn locked" disabled><span>Community Hub</span><span class="upcoming-tag">upcoming</span></button>

      <div style="display:flex;justify-content:space-between;align-items:center;margin-top:14px">
        <span class="nav-heading" style="margin:0 8px">Groups</span>
        <button class="btn sm" onclick="openModal('createGroupModal')">+ New</button>
      </div>
      <div id="sidebarGroups" style="margin-top:6px"></div>
    </aside>

    <main class="panel center-stage">
      <!-- BULLETINS & FORUMS -->
      <section id="sec-bulletins">
        <div class="stage-header">
          <div>
            <h2 id="bulletinTitle" class="vic-title" style="margin:0;color:var(--accent)">International Bulletin</h2>
            <div id="bulletinSubtitle" class="meta">Main Parlour • Forums, replies, edit &amp; delete</div>
          </div>
          <select id="sortSelect" onchange="loadForums()" style="width:auto;margin:0;padding:7px 11px">
            <option value="new">Newest First</option>
            <option value="discussed">Most Discussed</option>
          </select>
        </div>
        <div class="bulletin-pills">
          <button class="btn sm gold" id="bp-International" onclick="selectBulletin('International')">International</button>
          <button class="btn sm" id="bp-Anime" onclick="selectBulletin('Anime')">Anime</button>
          <button class="btn sm" id="bp-Gaming" onclick="selectBulletin('Gaming')">Gaming</button>
          <button class="btn sm" id="bp-Music" onclick="selectBulletin('Music')">Music</button>
          <button class="btn sm" id="bp-Tech" onclick="selectBulletin('Tech')">Tech</button>
          <button class="btn sm" id="bp-Creative Writing" onclick="selectBulletin('Creative Writing')">Creative Writing</button>
        </div>
        <input id="searchBox" placeholder="Search forums, tags, or authors..." oninput="loadForums()" />
        <div id="forumsBox"></div>
      </section>

      <!-- GROUP CHATS -->
      <section id="sec-groups" style="display:none">
        <div class="stage-header">
          <div>
            <h2 class="vic-title" style="margin:0;color:var(--accent)">Group Chats</h2>
            <div class="meta">Public or private rooms • Polls, slowmode, ||spoilers||, reactions &amp; forwarding</div>
          </div>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <button class="btn" onclick="openModal('joinPrivateModal')">Join by Code</button>
            <button class="btn pine" onclick="openModal('createGroupModal')">+ Create Group</button>
          </div>
        </div>
        <div id="groupCardsList" style="margin-bottom:16px"></div>
        <div id="activeGroupView" style="display:none;border-top:1px solid var(--line);padding-top:16px">
          <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;margin-bottom:10px">
            <div>
              <strong id="agTitle" class="vic-title" style="font-size:1.15rem;color:var(--accent)"></strong>
              <span id="agVisBadge" class="vis-badge" style="margin-left:8px"></span>
              <span id="agSlowBadge" class="vis-badge" style="margin-left:4px"></span>
              <div id="agTopic" class="meta" style="margin-top:2px"></div>
            </div>
            <div style="display:flex;gap:7px;flex-wrap:wrap" id="agOwnerControls"></div>
          </div>
          <div id="pinnedBox" class="pinned-banner" style="display:none"></div>
          <div id="pollsBox"></div>
          <div class="chat-layout">
            <div>
              <div id="chatStream" style="max-height:360px;overflow-y:auto;padding-right:4px;margin-bottom:10px"></div>
              <div id="replyingBar" style="display:none;justify-content:space-between;align-items:center;padding:6px 10px;background:var(--accent-soft);border-radius:8px;margin-bottom:6px;font-size:0.8rem">
                <span id="replyingText"></span>
                <button class="btn sm" onclick="clearChatReply()">Cancel</button>
              </div>
              <div style="display:flex;gap:8px;flex-wrap:wrap">
                <input id="chatInput" placeholder="Message... ||spoiler|| or @username" style="margin:0;flex:1;min-width:190px" onkeydown="if(event.key==='Enter')sendGroupMessage()" />
                <label class="meta" style="display:flex;align-items:center;gap:4px;cursor:pointer">
                  <input type="checkbox" id="silentSend" style="width:auto;margin:0" /> Silent
                </label>
                <button class="btn gold" onclick="sendGroupMessage()">Send</button>
              </div>
            </div>
            <div style="background:rgba(5,12,15,0.5);border:1px solid rgba(255,255,255,0.07);border-radius:10px;padding:10px">
              <div class="nav-heading" style="margin:0 0 8px">Members</div>
              <div id="agMembersList" class="meta" style="line-height:1.7"></div>
            </div>
          </div>
        </div>
      </section>

      <!-- DMs & FRIENDS (DISCORD / TELEGRAM STYLE) -->
      <section id="sec-dms" style="display:none">
        <div class="stage-header">
          <div>
            <h2 class="vic-title" style="margin:0;color:var(--accent)">DMs</h2>
            <div class="meta">Add friends and send direct messages</div>
          </div>
        </div>
        <div style="display:flex;gap:8px;margin-bottom:12px">
          <input id="addFriendInput" placeholder="Add friend by username..." style="margin:0;flex:1" onkeydown="if(event.key==='Enter')addFriend()" />
          <button class="btn pine" onclick="addFriend()">Add Friend</button>
        </div>
        <div class="dm-grid">
          <div class="friend-pane">
            <div class="nav-heading" style="margin:0 0 8px">Friends List</div>
            <div id="friendsListBox"></div>
          </div>
          <div class="dm-chat-box">
            <div>
              <div style="display:flex;justify-content:space-between;align-items:center;padding-bottom:8px;margin-bottom:10px;border-bottom:1px solid rgba(255,255,255,0.08)">
                <strong id="dmChatTitle" style="color:var(--accent)">Select a friend</strong>
                <span id="dmChatSub" class="meta"></span>
              </div>
              <div id="dmMessagesBox" style="max-height:310px;overflow-y:auto;margin-bottom:10px"></div>
            </div>
            <div style="display:flex;gap:8px">
              <input id="dmMsgInput" placeholder="Message friend... ||spoiler||" style="margin:0;flex:1" onkeydown="if(event.key==='Enter')sendFriendDM()" />
              <button class="btn gold" onclick="sendFriendDM()">Send</button>
            </div>
          </div>
        </div>
      </section>
    </main>

    <aside class="panel right-sidebar">
      <div style="display:flex;align-items:center;gap:11px;margin-bottom:10px">
        <div class="avatar" id="sidePfp">G</div>
        <div>
          <div id="sideName" style="font-weight:700;color:var(--accent)">Guest</div>
          <div id="sideHandle" class="meta">@guest</div>
        </div>
      </div>
      <div id="sideStatus" style="font-size:0.8rem;color:#a7f3d0;margin-bottom:6px">Status: Online</div>
      <div id="sideBio" class="meta" style="margin-bottom:14px">Sign in via + → Account to add friends and post forums.</div>
      <div style="border-top:1px solid var(--line);padding-top:12px">
        <div class="meta" style="font-weight:700;color:var(--accent);margin-bottom:6px">Holiday Parlour</div>
        <div class="meta" style="font-style:italic;line-height:1.45">“Merry Christmas — warm hearth, falling snow, and open forums.”</div>
      </div>
    </aside>
  </div>

  <!-- NEW FORUM MODAL -->
  <div id="forumModal" class="modal" onclick="if(event.target===this)closeModal('forumModal')">
    <div class="panel modal-box">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
        <h3 class="vic-title" style="margin:0;color:var(--accent)">New Forum</h3>
        <button class="btn sm" onclick="closeModal('forumModal')">Close</button>
      </div>
      <select id="nfBulletin">
        <option value="International">International (Main Parlour)</option>
        <option value="Anime">Anime Bulletin</option>
        <option value="Gaming">Gaming Bulletin</option>
        <option value="Music">Music Bulletin</option>
        <option value="Tech">Tech Bulletin</option>
        <option value="Creative Writing">Creative Writing Bulletin</option>
      </select>
      <input id="nfTitle" placeholder="Forum title" />
      <input id="nfTag" placeholder="Tag (e.g. Discussion, Review, News)" />
      <textarea id="nfBody" rows="4" placeholder="Write your forum post..."></textarea>
      <button class="btn gold" onclick="createForum()">Publish Forum</button>
    </div>
  </div>

  <!-- GAME ROOM MODAL -->
  <div id="roomModal" class="modal" onclick="if(event.target===this)closeModal('roomModal')">
    <div class="panel modal-box">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
        <h3 class="vic-title" style="margin:0;color:var(--accent)">Create Game Room</h3>
        <button class="btn sm" onclick="closeModal('roomModal')">Close</button>
      </div>
      <input id="rmNameInput" placeholder="Room name" />
      <select id="rmVisInput">
        <option value="public">Public</option>
        <option value="private">Private</option>
      </select>
      <button class="btn pine" onclick="createRoom()">Create</button>
    </div>
  </div>

  <!-- CREATE GROUP MODAL -->
  <div id="createGroupModal" class="modal" onclick="if(event.target===this)closeModal('createGroupModal')">
    <div class="panel modal-box">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
        <h3 class="vic-title" style="margin:0;color:var(--accent)">Create Group Chat</h3>
        <button class="btn sm" onclick="closeModal('createGroupModal')">Close</button>
      </div>
      <input id="cgName" placeholder="Group name" />
      <input id="cgTopic" placeholder="Topic" />
      <select id="cgVis">
        <option value="private">Private (Invite code &amp; members)</option>
        <option value="public">Public</option>
      </select>
      <select id="cgSlow">
        <option value="0">Slowmode: Off</option>
        <option value="3">Slowmode: 3s</option>
        <option value="10">Slowmode: 10s</option>
      </select>
      <input id="cgMembers" placeholder="Invite usernames (comma-separated, optional)" />
      <button class="btn pine" onclick="createGroup()">Create</button>
    </div>
  </div>

  <!-- JOIN PRIVATE GROUP MODAL -->
  <div id="joinPrivateModal" class="modal" onclick="if(event.target===this)closeModal('joinPrivateModal')">
    <div class="panel modal-box">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
        <h3 class="vic-title" style="margin:0;color:var(--accent)">Join Private Group</h3>
        <button class="btn sm" onclick="closeModal('joinPrivateModal')">Close</button>
      </div>
      <input id="joinCodeInput" placeholder="Group invite code" />
      <button class="btn gold" onclick="joinByCode()">Join</button>
    </div>
  </div>

  <!-- CREATE POLL MODAL -->
  <div id="pollModal" class="modal" onclick="if(event.target===this)closeModal('pollModal')">
    <div class="panel modal-box">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
        <h3 class="vic-title" style="margin:0;color:var(--accent)">Create Poll</h3>
        <button class="btn sm" onclick="closeModal('pollModal')">Close</button>
      </div>
      <input id="pollQ" placeholder="Question" />
      <input id="pollOpts" placeholder="Options (comma-separated)" />
      <button class="btn pine" onclick="createPoll()">Create</button>
    </div>
  </div>

  <!-- SETTINGS MODAL -->
  <div id="settingsModal" class="modal" onclick="if(event.target===this)closeModal('settingsModal')">
    <div class="panel modal-box">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
        <h3 class="vic-title" style="margin:0;color:var(--accent)">Settings</h3>
        <button class="btn sm" onclick="closeModal('settingsModal')">Close</button>
      </div>
      <div class="set-nav">
        <button class="btn sm gold" id="stab-look" onclick="switchSetTab('look')">Appearance</button>
        <button class="btn sm" id="stab-xmas" onclick="switchSetTab('xmas')">Christmas</button>
        <button class="btn sm" id="stab-chat" onclick="switchSetTab('chat')">Chat</button>
      </div>
      <div id="spane-look">
        <div class="setting-row">
          <div><strong>Theme</strong><div class="meta">Palette</div></div>
          <select id="setTheme" onchange="applySettings()" style="width:auto;margin:0">
            <option value="evergreen">Evergreen &amp; Gold</option>
            <option value="burgundy">Victorian Burgundy</option>
            <option value="frost">Midnight Frost</option>
            <option value="midnight">Obsidian &amp; Gold</option>
          </select>
        </div>
        <div class="setting-row">
          <div><strong>Text Size</strong><div class="meta">Font scale</div></div>
          <select id="setFont" onchange="applySettings()" style="width:auto;margin:0">
            <option value="14px">Compact</option>
            <option value="15px" selected>Standard</option>
            <option value="17px">Large</option>
          </select>
        </div>
        <div class="setting-row" style="border:none">
          <div><strong>Compact Spacing</strong><div class="meta">Tighter panels</div></div>
          <input type="checkbox" id="setCompact" onchange="applySettings()" style="width:auto;margin:0" />
        </div>
      </div>
      <div id="spane-xmas" style="display:none">
        <div class="setting-row">
          <div><strong>Snowfall</strong><div class="meta">Snow animation</div></div>
          <select id="setSnow" onchange="applySettings()" style="width:auto;margin:0">
            <option value="normal">Snow</option>
            <option value="blizzard">Blizzard</option>
            <option value="off">Off</option>
          </select>
        </div>
        <div class="setting-row">
          <div><strong>Christmas Lights</strong><div class="meta">Twinkling bulbs</div></div>
          <input type="checkbox" id="setLights" checked onchange="applySettings()" style="width:auto;margin:0" />
        </div>
        <div class="setting-row" style="border:none">
          <div><strong>Swinging Ornaments</strong><div class="meta">Hanging ornaments</div></div>
          <input type="checkbox" id="setOrnaments" checked onchange="applySettings()" style="width:auto;margin:0" />
        </div>
      </div>
      <div id="spane-chat" style="display:none">
        <div class="setting-row">
          <div><strong>Timestamps</strong><div class="meta">Show message times</div></div>
          <input type="checkbox" id="setTimes" checked onchange="applySettings()" style="width:auto;margin:0" />
        </div>
        <div class="setting-row" style="border:none">
          <div><strong>Auto-Reveal Spoilers</strong><div class="meta">Show ||spoilers||</div></div>
          <input type="checkbox" id="setSpoilers" onchange="applySettings()" style="width:auto;margin:0" />
        </div>
      </div>
    </div>
  </div>

  <!-- ACCOUNT MODAL -->
  <div id="accountModal" class="modal" onclick="if(event.target===this)closeModal('accountModal')">
    <div class="panel modal-box">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
        <h3 class="vic-title" style="margin:0;color:var(--accent)">Account</h3>
        <button class="btn sm" onclick="closeModal('accountModal')">Close</button>
      </div>
      <div id="authBox">
        <input id="inUser" placeholder="Username" />
        <input id="inPass" type="password" placeholder="Password" />
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="btn gold" onclick="authSubmit('register')">Register</button>
          <button class="btn pine" onclick="authSubmit('login')">Login</button>
          <button class="btn" onclick="setAnon()">Anonymous</button>
        </div>
      </div>
      <div id="profBox" style="display:none">
        <input id="edDisplay" placeholder="Display name" />
        <input id="edStatus" placeholder="Status" />
        <input id="edPfp" placeholder="Avatar URL (optional)" />
        <textarea id="edBio" rows="2" placeholder="Bio..."></textarea>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="btn gold" onclick="saveProfile()">Save</button>
          <button class="btn" onclick="setAnon()">Anon</button>
          <button class="btn crimson" onclick="logoutUser()">Logout</button>
        </div>
      </div>
    </div>
  </div>

  <script>
    const ALL_BULLETINS = ["International", "Anime", "Gaming", "Music", "Tech", "Creative Writing"];
    let user = JSON.parse(localStorage.getItem("schw_v8_user") || "null");
    let cfg = JSON.parse(localStorage.getItem("schw_v8_cfg") || '{"theme":"evergreen","snow":"normal","font":"15px","compact":false,"lights":true,"ornaments":true,"times":true,"spoilers":false}');
    let currentBulletin = "International";
    let selectedFriend = null;
    let activeGroupId = null;
    let replyToMsg = null;

    const canvas = document.getElementById("snowCanvas");
    const ctx = canvas.getContext("2d");
    let flakes = [];
    function resizeCanvas() { canvas.width = window.innerWidth; canvas.height = window.innerHeight; }
    window.addEventListener("resize", resizeCanvas);
    resizeCanvas();
    function rebuildFlakes(n) {
      flakes = Array.from({ length: n }, () => ({
        x: Math.random() * canvas.width, y: Math.random() * canvas.height,
        r: 1.3 + Math.random() * 2.5, vy: 0.7 + Math.random() * 1.5,
        vx: -0.4 + Math.random() * 0.8, alpha: 0.4 + Math.random() * 0.5
      }));
    }
    function tickSnow() {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      if (cfg.snow !== "off") {
        for (const f of flakes) {
          f.y += f.vy; f.x += f.vx;
          if (f.y > canvas.height) f.y = -5;
          if (f.x > canvas.width) f.x = 0;
          if (f.x < 0) f.x = canvas.width;
          ctx.beginPath(); ctx.arc(f.x, f.y, f.r, 0, Math.PI * 2);
          ctx.fillStyle = `rgba(255, 250, 235, ${f.alpha})`; ctx.fill();
        }
      }
      requestAnimationFrame(tickSnow);
    }
    tickSnow();

    function applySettings() {
      cfg.theme = document.getElementById("setTheme").value;
      cfg.snow = document.getElementById("setSnow").value;
      cfg.font = document.getElementById("setFont").value;
      cfg.compact = document.getElementById("setCompact").checked;
      cfg.lights = document.getElementById("setLights").checked;
      cfg.ornaments = document.getElementById("setOrnaments").checked;
      cfg.times = document.getElementById("setTimes").checked;
      cfg.spoilers = document.getElementById("setSpoilers").checked;
      localStorage.setItem("schw_v8_cfg", JSON.stringify(cfg));
      document.body.className = "";
      if (cfg.theme !== "evergreen") document.body.classList.add("theme-" + cfg.theme);
      if (cfg.compact) document.body.classList.add("compact-mode");
      document.documentElement.style.setProperty("--font-scale", cfg.font);
      document.querySelector(".xmas-lights").style.display = cfg.lights ? "flex" : "none";
      document.querySelector(".xmas-ornaments").style.display = cfg.ornaments ? "flex" : "none";
      rebuildFlakes(cfg.snow === "off" ? 0 : (cfg.snow === "blizzard" ? 190 : 80));
    }
    function switchSetTab(t) {
      ["look", "xmas", "chat"].forEach(k => {
        document.getElementById("spane-" + k).style.display = k === t ? "block" : "none";
        document.getElementById("stab-" + k).className = k === t ? "btn sm gold" : "btn sm";
      });
    }
    function openSettings() {
      document.getElementById("setTheme").value = cfg.theme;
      document.getElementById("setSnow").value = cfg.snow;
      document.getElementById("setFont").value = cfg.font;
      document.getElementById("setCompact").checked = cfg.compact;
      document.getElementById("setLights").checked = cfg.lights;
      document.getElementById("setOrnaments").checked = cfg.ornaments;
      document.getElementById("setTimes").checked = cfg.times;
      document.getElementById("setSpoilers").checked = cfg.spoilers;
      openModal("settingsModal");
    }

    function togglePlusMenu(e) { e.stopPropagation(); document.getElementById("plusMenu").classList.toggle("open"); }
    function closePlusMenu() { document.getElementById("plusMenu").classList.remove("open"); }
    document.addEventListener("click", closePlusMenu);

    function esc(s) { return String(s || "").replace(/[&<>"']/g, c => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;" }[c])); }
    function formatMsgBody(raw) {
      let safe = esc(raw);
      safe = safe.replace(/\|\|(.+?)\|\|/g, (_, inner) => `<span class="spoiler ${cfg.spoilers ? "revealed" : ""}" onclick="this.classList.toggle('revealed')">${inner}</span>`);
      safe = safe.replace(/(^|\s)@([a-zA-Z0-9_]+)/g, (_, sp, u) => `${sp}<span class="mention-chip">@${u}</span>`);
      return safe;
    }
    function fmtTime(iso) {
      if (!cfg.times || !iso) return "";
      const d = new Date(iso);
      return isNaN(d) ? "" : " • " + d.toLocaleString([], { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" });
    }
    function openModal(id) { document.getElementById(id).classList.add("open"); }
    function closeModal(id) { document.getElementById(id).classList.remove("open"); }
    function meName() { return (!user || user.anon) ? "Anonymous" : user.username; }
    function isMine(a) { const m = meName(); return m !== "Anonymous" && a && a.toLowerCase() === m.toLowerCase(); }

    function showSection(sec) {
      ["bulletins", "groups", "dms"].forEach(s => {
        document.getElementById("sec-" + s).style.display = s === sec ? "block" : "none";
        document.getElementById("nav-" + s).classList.toggle("active", s === sec);
      });
      if (sec === "bulletins") loadForums();
      if (sec === "groups") loadGroups();
      if (sec === "dms") loadFriendsAndDMs();
    }

    function selectBulletin(b) {
      currentBulletin = b;
      document.getElementById("bulletinTitle").textContent = b + " Bulletin";
      document.getElementById("bulletinSubtitle").textContent = b === "International"
        ? "Main Parlour • Global forums, replies, edit & delete"
        : `${b} Board • Forums, replies, edit & delete`;
      document.getElementById("nfBulletin").value = b;
      ALL_BULLETINS.forEach(x => {
        const pill = document.getElementById("bp-" + x);
        if (pill) pill.className = x === b ? "btn sm gold" : "btn sm";
        const sb = document.getElementById("sb-" + x);
        if (sb) sb.classList.toggle("active", x === b);
      });
      showSection("bulletins");
      loadForums();
    }

    function renderProfile() {
      const pfp = document.getElementById("sidePfp"), nm = document.getElementById("sideName"), hd = document.getElementById("sideHandle"), st = document.getElementById("sideStatus"), bi = document.getElementById("sideBio");
      if (!user) { pfp.textContent = "G"; nm.textContent = "Guest"; hd.textContent = "@guest"; st.textContent = "Status: Browsing"; bi.textContent = "Sign in via + → Account to add friends and post forums."; }
      else if (user.anon) { pfp.textContent = "A"; nm.textContent = "Anonymous"; hd.textContent = "@anon"; st.textContent = "Status: Incognito"; bi.textContent = "Anonymous mode."; }
      else { pfp.innerHTML = user.pfp ? `<img src="${esc(user.pfp)}" alt="pfp">` : esc((user.username || "U")[0].toUpperCase()); nm.textContent = user.display_name || user.username; hd.textContent = "@" + user.username; st.textContent = "Status: " + (user.status || "Online"); bi.textContent = user.bio || "Parlour member."; }
    }

    function openAccount() {
      const reg = user && !user.anon;
      document.getElementById("authBox").style.display = reg ? "none" : "block";
      document.getElementById("profBox").style.display = reg ? "block" : "none";
      if (reg) {
        document.getElementById("edDisplay").value = user.display_name || "";
        document.getElementById("edStatus").value = user.status || "";
        document.getElementById("edPfp").value = user.pfp || "";
        document.getElementById("edBio").value = user.bio || "";
      }
      openModal("accountModal");
    }
    function setAnon() { user = { anon: true, username: "Anonymous" }; localStorage.setItem("schw_v8_user", JSON.stringify(user)); renderProfile(); closeModal("accountModal"); loadGroups(); }
    function logoutUser() { user = null; localStorage.removeItem("schw_v8_user"); renderProfile(); closeModal("accountModal"); loadGroups(); }

    async function authSubmit(mode) {
      const username = document.getElementById("inUser").value.trim(), password = document.getElementById("inPass").value;
      const r = await fetch("/api/auth/" + mode, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username, password }) });
      const d = await r.json(); if (!r.ok) return alert(d.error || "Auth failed");
      user = d.user; localStorage.setItem("schw_v8_user", JSON.stringify(user)); renderProfile(); closeModal("accountModal"); loadGroups(); loadFriendsAndDMs();
    }
    async function saveProfile() {
      const r = await fetch("/api/profile", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ old_username: user.username, display_name: document.getElementById("edDisplay").value.trim(), status: document.getElementById("edStatus").value.trim(), pfp: document.getElementById("edPfp").value.trim(), bio: document.getElementById("edBio").value.trim() }) });
      const d = await r.json(); if (!r.ok) return alert(d.error || "Failed");
      user = d.user; localStorage.setItem("schw_v8_user", JSON.stringify(user)); renderProfile(); closeModal("accountModal");
    }

    // FORUMS
    async function createForum() {
      const bulletin = document.getElementById("nfBulletin").value;
      const title = document.getElementById("nfTitle").value.trim();
      const flair = document.getElementById("nfTag").value.trim() || "Forum";
      const body = document.getElementById("nfBody").value.trim();
      if (!body) return alert("Write something first");
      await fetch("/api/posts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ author: meName(), bulletin, title, flair, body }) });
      document.getElementById("nfTitle").value = ""; document.getElementById("nfTag").value = ""; document.getElementById("nfBody").value = "";
      closeModal("forumModal"); selectBulletin(bulletin);
    }
    async function editForum(pid, oldTitle, oldBody) {
      const title = prompt("Edit forum title:", oldTitle); if (title === null) return;
      const body = prompt("Edit forum post:", oldBody); if (body === null || !body.trim()) return;
      await fetch(`/api/posts/${pid}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ author: meName(), title: title.trim(), body: body.trim() }) });
      loadForums();
    }
    async function deleteForum(pid) {
      if (!confirm("Delete this forum?")) return;
      await fetch(`/api/posts/${pid}?author=${encodeURIComponent(meName())}`, { method: "DELETE" }); loadForums();
    }
    async function addReply(pid) {
      const inp = document.getElementById("rep-" + pid); const body = inp.value.trim(); if (!body) return;
      await fetch(`/api/posts/${pid}/replies`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ author: meName(), body }) });
      inp.value = ""; loadForums();
    }
    async function editReply(pid, rid, oldBody) {
      const body = prompt("Edit reply:", oldBody); if (body === null || !body.trim()) return;
      await fetch(`/api/posts/${pid}/replies/${rid}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ author: meName(), body: body.trim() }) });
      loadForums();
    }
    async function deleteReply(pid, rid) {
      await fetch(`/api/posts/${pid}/replies/${rid}?author=${encodeURIComponent(meName())}`, { method: "DELETE" }); loadForums();
    }

    async function loadForums() {
      const q = encodeURIComponent(document.getElementById("searchBox").value.trim());
      const sort = encodeURIComponent(document.getElementById("sortSelect").value);
      const b = encodeURIComponent(currentBulletin);
      const r = await fetch(`/api/posts?bulletin=${b}&q=${q}&sort=${sort}`);
      const items = await r.json();
      document.getElementById("forumsBox").innerHTML = items.length ? items.map(p => `
        <article class="card-item">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px;flex-wrap:wrap">
            <div>
              <span class="flair">${esc(p.flair || "Forum")}</span>
              <h3 class="vic-title" style="margin:7px 0 3px">${esc(p.title || "Untitled Forum")}</h3>
              <div class="meta">by <b>@${esc(p.author)}</b>${fmtTime(p.created_at)}${p.edited ? " • (edited)" : ""}</div>
            </div>
            ${isMine(p.author) ? `<div style="display:flex;gap:6px"><button class="btn sm" onclick="editForum('${p.id}',${JSON.stringify(p.title||'')},${JSON.stringify(p.body||'')})">Edit</button><button class="btn sm crimson" onclick="deleteForum('${p.id}')">Delete</button></div>` : ""}
          </div>
          <p style="margin:11px 0;white-space:pre-wrap;line-height:1.5">${formatMsgBody(p.body)}</p>
          <div class="replies-wrap">
            <div class="meta" style="margin-bottom:7px">Replies (${(p.replies || []).length})</div>
            ${(p.replies || []).map(rep => `
              <div class="reply-entry">
                <div><b>@${esc(rep.author)}</b> <span class="meta">${fmtTime(rep.created_at)}${rep.edited ? " (edited)" : ""}</span><div style="margin-top:2px">${formatMsgBody(rep.body)}</div></div>
                ${isMine(rep.author) ? `<div style="display:flex;gap:4px"><button class="btn sm" onclick="editReply('${p.id}','${rep.id}',${JSON.stringify(rep.body||'')})">Edit</button><button class="btn sm crimson" onclick="deleteReply('${p.id}','${rep.id}')">Delete</button></div>` : ""}
              </div>
            `).join("")}
            <div style="display:flex;gap:7px;margin-top:8px">
              <input id="rep-${p.id}" placeholder="Write a reply..." style="margin:0;flex:1" onkeydown="if(event.key==='Enter')addReply('${p.id}')" />
              <button class="btn sm pine" onclick="addReply('${p.id}')">Reply</button>
            </div>
          </div>
        </article>
      `).join("") : `<div class="meta">No forums in ${esc(currentBulletin)} yet.</div>`;
    }

    // GROUPS (FULL FEATURES)
    async function createGroup() {
      const me = meName(), visibility = document.getElementById("cgVis").value;
      if (visibility === "private" && me === "Anonymous") return alert("Log in to create a private group.");
      const name = document.getElementById("cgName").value.trim(), topic = document.getElementById("cgTopic").value.trim();
      const slowmode = Number(document.getElementById("cgSlow").value || "0");
      const membersRaw = document.getElementById("cgMembers").value.trim();
      if (!name) return alert("Enter a group name");
      const r = await fetch("/api/groups", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, topic, visibility, slowmode, owner: me, members: membersRaw ? membersRaw.split(",").map(x=>x.trim()).filter(Boolean) : [] }) });
      const g = await r.json(); if (!r.ok) return alert(g.error || "Failed");
      document.getElementById("cgName").value = ""; document.getElementById("cgTopic").value = ""; document.getElementById("cgMembers").value = "";
      closeModal("createGroupModal"); await loadGroups(); openGroup(g.id);
    }
    async function joinByCode() {
      const me = meName(); if (me === "Anonymous") return alert("Log in to join private groups.");
      const code = document.getElementById("joinCodeInput").value.trim();
      const r = await fetch("/api/groups/join-code", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: me, code }) });
      const d = await r.json(); if (!r.ok) return alert(d.error || "Invalid code");
      document.getElementById("joinCodeInput").value = ""; closeModal("joinPrivateModal"); await loadGroups(); openGroup(d.id);
    }
    async function inviteMemberToGroup(gid) {
      const target = prompt("Username to invite:"); if (!target || !target.trim()) return;
      await fetch(`/api/groups/${gid}/invite`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ owner: meName(), username: target.trim() }) });
      openGroup(gid);
    }
    async function deleteGroup(gid) {
      if (!confirm("Delete this group?")) return;
      await fetch(`/api/groups/${gid}?owner=${encodeURIComponent(meName())}`, { method: "DELETE" });
      activeGroupId = null; document.getElementById("activeGroupView").style.display = "none"; loadGroups();
    }
    async function loadGroups() {
      const r = await fetch("/api/groups?user=" + encodeURIComponent(meName()));
      const groups = await r.json();
      document.getElementById("sidebarGroups").innerHTML = groups.map(g => `<button class="nav-btn" onclick="showSection('groups');openGroup('${g.id}')"><span># ${esc(g.name)}</span><span class="vis-badge">${esc(g.visibility)}</span></button>`).join("");
      document.getElementById("groupCardsList").innerHTML = groups.length ? groups.map(g => `<div class="card-item" style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;padding:12px 15px"><div><strong># ${esc(g.name)}</strong><span class="vis-badge" style="margin-left:6px">${esc(g.visibility)}</span><div class="meta" style="margin-top:3px">${esc(g.topic || "")}</div></div><button class="btn sm pine" onclick="openGroup('${g.id}')">Open Chat</button></div>`).join("") : `<div class="meta">No groups yet.</div>`;
    }
    async function openGroup(gid) {
      activeGroupId = gid; clearChatReply();
      const r = await fetch("/api/groups?user=" + encodeURIComponent(meName()));
      const groups = await r.json(); const g = groups.find(x => x.id === gid); if (!g) return;
      document.getElementById("activeGroupView").style.display = "block";
      document.getElementById("agTitle").textContent = "# " + g.name;
      document.getElementById("agVisBadge").textContent = g.visibility === "private" ? `Private • Code: ${g.invite_code}` : "Public";
      document.getElementById("agSlowBadge").textContent = g.slowmode ? `Slowmode ${g.slowmode}s` : "No Slowmode";
      document.getElementById("agTopic").textContent = g.topic || "";
      const isOwner = meName() !== "Anonymous" && g.owner && g.owner.toLowerCase() === meName().toLowerCase();
      document.getElementById("agOwnerControls").innerHTML = `<button class="btn sm" onclick="openModal('pollModal')">+ Poll</button>${isOwner && g.visibility === "private" ? `<button class="btn sm pine" onclick="inviteMemberToGroup('${g.id}')">+ Invite</button>` : ""}${isOwner ? `<button class="btn sm crimson" onclick="deleteGroup('${g.id}')">Delete</button>` : ""}`;
      const pinned = g.pinned || []; const pBox = document.getElementById("pinnedBox");
      pBox.style.display = pinned.length ? "block" : "none";
      if (pinned.length) pBox.innerHTML = `<strong style="color:var(--accent)">Pinned:</strong> ` + pinned.map(p => `@${esc(p.author)}: "${esc(p.body)}"`).join(" • ");
      document.getElementById("pollsBox").innerHTML = (g.polls || []).map(pl => {
        const total = (pl.options || []).reduce((a, o) => a + (o.votes || []).length, 0);
        return `<div class="poll-card"><strong style="color:var(--accent)">Poll: ${esc(pl.question)}</strong> <span class="meta">(${total} votes)</span><div style="display:flex;gap:7px;flex-wrap:wrap;margin-top:8px">${(pl.options || []).map((o, oi) => `<button class="btn sm" onclick="votePoll('${g.id}','${pl.id}',${oi})">${esc(o.label)} (${(o.votes || []).length})</button>`).join("")}</div></div>`;
      }).join("");
      document.getElementById("agMembersList").innerHTML = (g.members && g.members.length) ? g.members.map(m => `<div>• @${esc(m)}</div>`).join("") : `<div>Open to all</div>`;
      const emojis = ["👍", "❤️", "😂", "🎄"];
      document.getElementById("chatStream").innerHTML = (g.messages || []).length ? g.messages.map((m, idx) => {
        const rx = m.reactions || {};
        return `<div class="msg-row">${m.forwarded_from ? `<div class="reply-quote">Forwarded from @${esc(m.forwarded_from)}</div>` : ""}${m.reply_to ? `<div class="reply-quote">Replying to @${esc(m.reply_to.author)}: ${esc(m.reply_to.body)}</div>` : ""}<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px;flex-wrap:wrap"><div><b style="color:var(--accent)">@${esc(m.author)}</b><span class="meta">${fmtTime(m.created_at)}${m.edited ? " (edited)" : ""}${m.silent ? " • silent" : ""}</span><div style="margin-top:3px;line-height:1.45">${formatMsgBody(m.body)}</div></div><div style="display:flex;gap:4px;flex-wrap:wrap"><button class="btn sm" onclick="setChatReply(${JSON.stringify(m.author)},${JSON.stringify(m.body)})">Reply</button><button class="btn sm" onclick="forwardMsg(${JSON.stringify(m.author)},${JSON.stringify(m.body)})">Forward</button><button class="btn sm" onclick="pinGroupMsg('${g.id}',${idx})">Pin</button>${isMine(m.author) ? `<button class="btn sm" onclick="editGroupMsg('${g.id}',${idx},${JSON.stringify(m.body)})">Edit</button><button class="btn sm crimson" onclick="deleteGroupMsg('${g.id}',${idx})">Del</button>` : ""}</div></div><div style="display:flex;gap:5px;flex-wrap:wrap;margin-top:6px">${emojis.map(em => { const cnt=(rx[em]||[]).length; const act=(rx[em]||[]).includes(meName()); return `<button class="react-pill ${act?"active":""}" onclick="toggleReaction('${g.id}',${idx},'${em}')">${em} ${cnt||""}</button>`; }).join("")}</div></div>`;
      }).join("") : `<div class="meta">No messages yet.</div>`;
    }
    async function createPoll() {
      const question = document.getElementById("pollQ").value.trim(), options = document.getElementById("pollOpts").value.split(",").map(s=>s.trim()).filter(Boolean);
      if (!question || options.length < 2) return alert("Enter question and 2+ options.");
      await fetch(`/api/groups/${activeGroupId}/polls`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question, options }) });
      document.getElementById("pollQ").value = ""; document.getElementById("pollOpts").value = ""; closeModal("pollModal"); openGroup(activeGroupId);
    }
    async function votePoll(gid, pollId, optIdx) { await fetch(`/api/groups/${gid}/polls/${pollId}/vote`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ voter: meName(), option_index: optIdx }) }); openGroup(gid); }
    async function toggleReaction(gid, idx, emoji) { await fetch(`/api/groups/${gid}/messages/${idx}/react`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user: meName(), emoji }) }); openGroup(gid); }
    async function forwardMsg(fromAuthor, body) {
      if (!user || user.anon) return alert("Log in to forward messages.");
      const to = prompt("Forward to friend username:"); if (!to || !to.trim()) return;
      await fetch("/api/dms", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ from: user.username, to: to.trim(), body, forwarded_from: fromAuthor }) });
      alert("Forwarded to DMs.");
    }
    function setChatReply(author, body) { replyToMsg = { author, body: body.slice(0, 80) }; document.getElementById("replyingBar").style.display = "flex"; document.getElementById("replyingText").textContent = `Replying to @${author}: "${replyToMsg.body}"`; }
    function clearChatReply() { replyToMsg = null; document.getElementById("replyingBar").style.display = "none"; }
    async function sendGroupMessage() {
      if (!activeGroupId) return;
      const body = document.getElementById("chatInput").value.trim(), silent = document.getElementById("silentSend").checked;
      if (!body) return;
      const r = await fetch(`/api/groups/${activeGroupId}/messages`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ author: meName(), body, reply_to: replyToMsg, silent }) });
      const d = await r.json(); if (!r.ok) return alert(d.error || "Send failed");
      document.getElementById("chatInput").value = ""; document.getElementById("silentSend").checked = false; clearChatReply(); openGroup(activeGroupId);
    }
    async function pinGroupMsg(gid, idx) { await fetch(`/api/groups/${gid}/pin/${idx}`, { method: "POST" }); openGroup(gid); }
    async function editGroupMsg(gid, idx, oldBody) { const body = prompt("Edit message:", oldBody); if (body === null || !body.trim()) return; await fetch(`/api/groups/${gid}/messages/${idx}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ author: meName(), body: body.trim() }) }); openGroup(gid); }
    async function deleteGroupMsg(gid, idx) { await fetch(`/api/groups/${gid}/messages/${idx}?author=${encodeURIComponent(meName())}`, { method: "DELETE" }); openGroup(gid); }

    async function createRoom() {
      const name = document.getElementById("rmNameInput").value.trim(), visibility = document.getElementById("rmVisInput").value;
      if (!name) return alert("Enter room name");
      await fetch("/api/rooms", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, visibility, host: meName() }) });
      document.getElementById("rmNameInput").value = ""; closeModal("roomModal"); alert("Game room saved!");
    }

    // FRIENDS & DMs
    async function addFriend() {
      if (!user || user.anon) return alert("Log in via + → Account first.");
      const friend = document.getElementById("addFriendInput").value.trim(); if (!friend) return;
      const r = await fetch("/api/friends", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: user.username, friend }) });
      const d = await r.json(); if (!r.ok) return alert(d.error || "Could not add friend");
      document.getElementById("addFriendInput").value = ""; selectedFriend = d.friend; loadFriendsAndDMs();
    }
    async function removeFriend(f) {
      if (!user || user.anon) return;
      await fetch(`/api/friends?user=${encodeURIComponent(user.username)}&friend=${encodeURIComponent(f)}`, { method: "DELETE" });
      if (selectedFriend === f) selectedFriend = null;
      loadFriendsAndDMs();
    }
    async function loadFriendsAndDMs() {
      if (!user || user.anon) {
        document.getElementById("friendsListBox").innerHTML = `<div class="meta">Log in to add friends.</div>`;
        document.getElementById("dmMessagesBox").innerHTML = `<div class="meta">Sign in to send DMs.</div>`;
        return;
      }
      const r = await fetch("/api/friends?user=" + encodeURIComponent(user.username));
      const friends = await r.json();
      if (!selectedFriend && friends.length) selectedFriend = friends[0];
      document.getElementById("friendsListBox").innerHTML = friends.length ? friends.map(f => `
        <div class="friend-row ${selectedFriend === f ? "active" : ""}" onclick="selectedFriend='${esc(f)}';loadFriendsAndDMs()">
          <span><span class="status-dot"></span>@${esc(f)}</span>
          <button class="btn sm crimson" onclick="event.stopPropagation();removeFriend('${esc(f)}')">x</button>
        </div>
      `).join("") : `<div class="meta">No friends added yet.</div>`;
      document.getElementById("dmChatTitle").textContent = selectedFriend ? ("@" + selectedFriend) : "Select a friend";
      document.getElementById("dmChatSub").textContent = selectedFriend ? "Direct Message" : "";
      if (!selectedFriend) return;
      const dmR = await fetch(`/api/dms?user=${encodeURIComponent(user.username)}&with=${encodeURIComponent(selectedFriend)}`);
      const msgs = await dmR.json();
      document.getElementById("dmMessagesBox").innerHTML = msgs.length ? msgs.map(m => {
        const mine = m.from.toLowerCase() === user.username.toLowerCase();
        return `<div class="dm-bubble ${mine ? "mine" : ""}">${m.forwarded_from ? `<div class="reply-quote">Forwarded from @${esc(m.forwarded_from)}</div>` : ""}<b>@${esc(m.from)}</b> <span class="meta">${fmtTime(m.created_at)}</span><div style="margin-top:3px">${formatMsgBody(m.body)}</div></div>`;
      }).join("") : `<div class="meta">No messages with @${esc(selectedFriend)} yet.</div>`;
    }
    async function sendFriendDM() {
      if (!user || user.anon) return alert("Log in first.");
      if (!selectedFriend) return alert("Pick a friend first.");
      const body = document.getElementById("dmMsgInput").value.trim(); if (!body) return;
      await fetch("/api/dms", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ from: user.username, to: selectedFriend, body }) });
      document.getElementById("dmMsgInput").value = ""; loadFriendsAndDMs();
    }

    const es = new EventSource("/api/stream");
    es.addEventListener("post_updated", () => loadForums());
    es.addEventListener("dm_sent", () => loadFriendsAndDMs());
    es.addEventListener("group_updated", () => { loadGroups(); if (activeGroupId) openGroup(activeGroupId); });

    document.getElementById("setTheme").value = cfg.theme;
    document.getElementById("setSnow").value = cfg.snow;
    document.getElementById("setFont").value = cfg.font;
    document.getElementById("setCompact").checked = cfg.compact;
    document.getElementById("setLights").checked = cfg.lights;
    document.getElementById("setOrnaments").checked = cfg.ornaments;
    document.getElementById("setTimes").checked = cfg.times;
    document.getElementById("setSpoilers").checked = cfg.spoilers;
    applySettings();
    renderProfile();
    loadForums();
    loadGroups();
  </script>
</body>
</html>
"""


@app.get("/")
def index():
    return render_template_string(HTML_UI)


@app.get("/api/stream")
def stream():
    q = queue.Queue(maxsize=50)
    with SUB_LOCK:
        SUBSCRIBERS.append(q)

    def gen():
        try:
            yield ": connected\n\n"
            while True:
                yield q.get()
        finally:
            with SUB_LOCK:
                if q in SUBSCRIBERS:
                    SUBSCRIBERS.remove(q)

    return Response(gen(), mimetype="text/event-stream")


@app.post("/api/auth/register")
def auth_register():
    data = request.get_json(force=True) or {}
    u, p = (data.get("username") or "").strip(), data.get("password") or ""
    if not u or not p:
        return jsonify({"error": "Username and password required"}), 400
    state = read_state()
    if u.lower() in {k.lower() for k in state["users"]}:
        return jsonify({"error": "Username taken"}), 409
    doc = {"username": u, "display_name": u, "status": "Online", "pfp": "", "bio": "", "esf": START_ESF, "password": p}
    state["users"][u] = doc
    state["friends"].setdefault(u, [])
    write_state(state)
    return jsonify({"user": {k: v for k, v in doc.items() if k != "password"}}), 201


@app.post("/api/auth/login")
def auth_login():
    data = request.get_json(force=True) or {}
    u, p = (data.get("username") or "").strip(), data.get("password") or ""
    state = read_state()
    doc = state["users"].get(u)
    if not doc or doc.get("password") != p:
        return jsonify({"error": "Invalid login"}), 401
    return jsonify({"user": {k: v for k, v in doc.items() if k != "password"}})


@app.put("/api/profile")
def api_profile():
    data = request.get_json(force=True) or {}
    u = (data.get("old_username") or "").strip()
    state = read_state()
    doc = state["users"].get(u)
    if not doc:
        return jsonify({"error": "User not found"}), 404
    doc["display_name"] = (data.get("display_name") or u).strip()
    doc["status"] = (data.get("status") or "Online").strip()
    doc["pfp"] = (data.get("pfp") or "").strip()
    doc["bio"] = (data.get("bio") or "").strip()
    write_state(state)
    return jsonify({"user": {k: v for k, v in doc.items() if k != "password"}})


@app.get("/api/posts")
def api_get_posts():
    bulletin = (request.args.get("bulletin") or "International").strip()
    q = (request.args.get("q") or "").strip().lower()
    sort = request.args.get("sort") or "new"
    state = read_state()
    items = [p for p in state["posts"].values() if (p.get("bulletin") or "International").lower() == bulletin.lower()]
    if q:
        items = [p for p in items if q in (p.get("title", "") + " " + p.get("flair", "") + " " + p.get("body", "") + " " + p.get("author", "")).lower()]
    items.sort(key=lambda x: len(x.get("replies", [])), reverse=True) if sort == "discussed" else items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return jsonify(items)


@app.post("/api/posts")
def api_create_post():
    data = request.get_json(force=True) or {}
    body = (data.get("body") or "").strip()
    if not body:
        return jsonify({"error": "Body required"}), 400
    pid = uuid.uuid4().hex[:10]
    post = {"id": pid, "author": (data.get("author") or "Anonymous").strip(), "bulletin": (data.get("bulletin") or "International").strip(), "title": (data.get("title") or "").strip(), "flair": (data.get("flair") or "Forum").strip(), "body": body, "replies": [], "edited": False, "created_at": now_iso()}
    state = read_state()
    state["posts"][pid] = post
    write_state(state)
    broadcast("post_updated", {"id": pid})
    return jsonify(post), 201


@app.put("/api/posts/<pid>")
def api_edit_post(pid):
    data = request.get_json(force=True) or {}
    author = (data.get("author") or "").strip()
    state = read_state()
    post = state["posts"].get(pid)
    if not post or not author or author == "Anonymous" or post.get("author", "").lower() != author.lower():
        return jsonify({"error": "Forbidden"}), 403
    if "title" in data:
        post["title"] = (data.get("title") or "").strip()
    if data.get("body"):
        post["body"] = data["body"].strip()
    post["edited"] = True
    write_state(state)
    broadcast("post_updated", {"id": pid})
    return jsonify(post)


@app.delete("/api/posts/<pid>")
def api_delete_post(pid):
    author = (request.args.get("author") or "").strip()
    state = read_state()
    post = state["posts"].get(pid)
    if not post or not author or author == "Anonymous" or post.get("author", "").lower() != author.lower():
        return jsonify({"error": "Forbidden"}), 403
    del state["posts"][pid]
    write_state(state)
    broadcast("post_updated", {"id": pid})
    return jsonify({"ok": True})


@app.post("/api/posts/<pid>/replies")
def api_add_reply(pid):
    data = request.get_json(force=True) or {}
    body = (data.get("body") or "").strip()
    if not body:
        return jsonify({"error": "Reply required"}), 400
    state = read_state()
    post = state["posts"].get(pid)
    if not post:
        return jsonify({"error": "Not found"}), 404
    rep = {"id": uuid.uuid4().hex[:8], "author": (data.get("author") or "Anonymous").strip(), "body": body, "edited": False, "created_at": now_iso()}
    post.setdefault("replies", []).append(rep)
    write_state(state)
    broadcast("post_updated", {"id": pid})
    return jsonify(rep), 201


@app.put("/api/posts/<pid>/replies/<rid>")
def api_edit_reply(pid, rid):
    data = request.get_json(force=True) or {}
    author = (data.get("author") or "").strip()
    state = read_state()
    post = state["posts"].get(pid)
    if not post:
        return jsonify({"error": "Not found"}), 404
    for rep in post.get("replies", []):
        if rep.get("id") == rid:
            if not author or author == "Anonymous" or rep.get("author", "").lower() != author.lower():
                return jsonify({"error": "Forbidden"}), 403
            if data.get("body"):
                rep["body"] = data["body"].strip()
                rep["edited"] = True
            write_state(state)
            broadcast("post_updated", {"id": pid})
            return jsonify(rep)
    return jsonify({"error": "Not found"}), 404


@app.delete("/api/posts/<pid>/replies/<rid>")
def api_delete_reply(pid, rid):
    author = (request.args.get("author") or "").strip()
    state = read_state()
    post = state["posts"].get(pid)
    if not post:
        return jsonify({"error": "Not found"}), 404
    replies = post.get("replies", [])
    for i, rep in enumerate(replies):
        if rep.get("id") == rid:
            if not author or author == "Anonymous" or rep.get("author", "").lower() != author.lower():
                return jsonify({"error": "Forbidden"}), 403
            replies.pop(i)
            write_state(state)
            broadcast("post_updated", {"id": pid})
            return jsonify({"ok": True})
    return jsonify({"error": "Not found"}), 404


@app.get("/api/friends")
def api_get_friends():
    u = (request.args.get("user") or "").strip()
    state = read_state()
    return jsonify(state["friends"].get(u, []))


@app.post("/api/friends")
def api_add_friend():
    data = request.get_json(force=True) or {}
    u = (data.get("username") or "").strip()
    f = (data.get("friend") or "").strip()
    if not u or u == "Anonymous" or not f:
        return jsonify({"error": "Invalid friend request"}), 400
    state = read_state()
    lst = state["friends"].setdefault(u, [])
    if f.lower() not in {x.lower() for x in lst}:
        lst.append(f)
    write_state(state)
    return jsonify({"friend": f, "friends": lst})


@app.delete("/api/friends")
def api_remove_friend():
    u = (request.args.get("user") or "").strip()
    f = (request.args.get("friend") or "").strip()
    state = read_state()
    lst = state["friends"].get(u, [])
    state["friends"][u] = [x for x in lst if x.lower() != f.lower()]
    write_state(state)
    return jsonify({"ok": True})


@app.get("/api/dms")
def api_get_dms():
    u = (request.args.get("user") or "").strip().lower()
    other = (request.args.get("with") or "").strip().lower()
    state = read_state()
    items = list(state["dms"].values())
    items.sort(key=lambda x: x.get("created_at", ""))
    if u and other:
        items = [m for m in items if {m.get("from", "").lower(), m.get("to", "").lower()} == {u, other}]
    elif u:
        items = [m for m in items if m.get("from", "").lower() == u or m.get("to", "").lower() == u]
    return jsonify(items)


@app.post("/api/dms")
def api_send_dm():
    data = request.get_json(force=True) or {}
    sender, to, body = (data.get("from") or "").strip(), (data.get("to") or "").strip(), (data.get("body") or "").strip()
    if not sender or sender == "Anonymous" or not to or not body:
        return jsonify({"error": "Invalid DM"}), 400
    did = uuid.uuid4().hex[:10]
    dm = {"id": did, "from": sender, "to": to, "body": body, "forwarded_from": data.get("forwarded_from"), "created_at": now_iso()}
    state = read_state()
    state["dms"][did] = dm
    write_state(state)
    broadcast("dm_sent", dm)
    return jsonify(dm), 201


@app.get("/api/groups")
def api_groups():
    u = (request.args.get("user") or "").strip()
    state = read_state()
    return jsonify([g for g in state["groups"].values() if can_access_group(g, u)])


@app.post("/api/groups")
def api_create_group():
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    vis = "private" if data.get("visibility") == "private" else "public"
    owner = (data.get("owner") or "Anonymous").strip()
    if not name:
        return jsonify({"error": "Name required"}), 400
    if vis == "private" and (not owner or owner == "Anonymous"):
        return jsonify({"error": "Login required for private groups"}), 400
    gid = uuid.uuid4().hex[:10]
    members = [owner] if owner != "Anonymous" else []
    for m in data.get("members") or []:
        if m and m.lower() not in {x.lower() for x in members}:
            members.append(m)
    g = {"id": gid, "name": name, "topic": (data.get("topic") or "").strip(), "visibility": vis, "invite_code": uuid.uuid4().hex[:6].upper(), "slowmode": int(data.get("slowmode") or 0), "owner": owner, "members": members, "pinned": [], "polls": [], "messages": [], "created_at": now_iso()}
    state = read_state()
    state["groups"][gid] = g
    write_state(state)
    broadcast("group_updated", {"id": gid})
    return jsonify(g), 201


@app.post("/api/groups/join-code")
def api_join_group_code():
    data = request.get_json(force=True) or {}
    u, code = (data.get("username") or "").strip(), (data.get("code") or "").strip().upper()
    if not u or u == "Anonymous" or not code:
        return jsonify({"error": "Login and code required"}), 400
    state = read_state()
    for g in state["groups"].values():
        if g.get("visibility") == "private" and g.get("invite_code", "").upper() == code:
            if u.lower() not in {m.lower() for m in g.get("members", [])}:
                g.setdefault("members", []).append(u)
                write_state(state)
                broadcast("group_updated", {"id": g["id"]})
            return jsonify(g)
    return jsonify({"error": "Code not found"}), 404


@app.post("/api/groups/<gid>/invite")
def api_invite_group(gid):
    data = request.get_json(force=True) or {}
    owner, target = (data.get("owner") or "").strip(), (data.get("username") or "").strip()
    state = read_state()
    g = state["groups"].get(gid)
    if not g or g.get("owner", "").lower() != owner.lower():
        return jsonify({"error": "Forbidden"}), 403
    if target and target.lower() not in {m.lower() for m in g.get("members", [])}:
        g.setdefault("members", []).append(target)
        write_state(state)
        broadcast("group_updated", {"id": gid})
    return jsonify(g)


@app.delete("/api/groups/<gid>")
def api_delete_group(gid):
    owner = (request.args.get("owner") or "").strip()
    state = read_state()
    g = state["groups"].get(gid)
    if not g or g.get("owner", "").lower() != owner.lower():
        return jsonify({"error": "Forbidden"}), 403
    del state["groups"][gid]
    write_state(state)
    broadcast("group_updated", {"id": gid})
    return jsonify({"ok": True})


@app.post("/api/groups/<gid>/messages")
def api_group_msg(gid):
    data = request.get_json(force=True) or {}
    body, author = (data.get("body") or "").strip(), (data.get("author") or "Anonymous").strip()
    if not body:
        return jsonify({"error": "Message required"}), 400
    state = read_state()
    g = state["groups"].get(gid)
    if not g or not can_access_group(g, author):
        return jsonify({"error": "No access"}), 403
    slow = int(g.get("slowmode") or 0)
    if slow > 0 and g.get("messages"):
        last = g["messages"][-1]
        if last.get("author", "").lower() == author.lower() and last.get("created_at"):
            try:
                elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(last["created_at"])).total_seconds()
                if elapsed < slow:
                    return jsonify({"error": f"Slowmode ({int(slow - elapsed)}s left)"}), 429
            except Exception:
                pass
    if author != "Anonymous" and author.lower() not in {m.lower() for m in g.get("members", [])}:
        g.setdefault("members", []).append(author)
    msg = {"author": author, "body": body, "reply_to": data.get("reply_to"), "silent": bool(data.get("silent")), "reactions": {}, "edited": False, "created_at": now_iso()}
    g.setdefault("messages", []).append(msg)
    write_state(state)
    broadcast("group_updated", {"id": gid})
    return jsonify(msg), 201


@app.post("/api/groups/<gid>/messages/<int:idx>/react")
def api_react_msg(gid, idx):
    data = request.get_json(force=True) or {}
    emoji, u = data.get("emoji"), (data.get("user") or "Anonymous").strip()
    state = read_state()
    g = state["groups"].get(gid)
    if not g or idx < 0 or idx >= len(g.get("messages", [])):
        return jsonify({"error": "Not found"}), 404
    lst = g["messages"][idx].setdefault("reactions", {}).setdefault(emoji, [])
    lst.remove(u) if u in lst else lst.append(u)
    write_state(state)
    broadcast("group_updated", {"id": gid})
    return jsonify({"ok": True})


@app.post("/api/groups/<gid>/polls")
def api_create_poll(gid):
    data = request.get_json(force=True) or {}
    q, opts = (data.get("question") or "").strip(), [o.strip() for o in (data.get("options") or []) if o and o.strip()]
    if not q or len(opts) < 2:
        return jsonify({"error": "Invalid poll"}), 400
    state = read_state()
    g = state["groups"].get(gid)
    if not g:
        return jsonify({"error": "Not found"}), 404
    poll = {"id": uuid.uuid4().hex[:8], "question": q, "options": [{"label": o, "votes": []} for o in opts]}
    g.setdefault("polls", []).append(poll)
    write_state(state)
    broadcast("group_updated", {"id": gid})
    return jsonify(poll), 201


@app.post("/api/groups/<gid>/polls/<pid>/vote")
def api_vote_poll(gid, pid):
    data = request.get_json(force=True) or {}
    voter, oi = (data.get("voter") or "Anonymous").strip(), int(data.get("option_index", -1))
    state = read_state()
    g = state["groups"].get(gid)
    if not g:
        return jsonify({"error": "Not found"}), 404
    for pl in g.get("polls", []):
        if pl.get("id") == pid and 0 <= oi < len(pl.get("options", [])):
            for opt in pl["options"]:
                if voter in opt["votes"]:
                    opt["votes"].remove(voter)
            pl["options"][oi]["votes"].append(voter)
            write_state(state)
            broadcast("group_updated", {"id": gid})
            return jsonify(pl)
    return jsonify({"error": "Not found"}), 404


@app.post("/api/groups/<gid>/pin/<int:idx>")
def api_pin_msg(gid, idx):
    state = read_state()
    g = state["groups"].get(gid)
    if not g or idx < 0 or idx >= len(g.get("messages", [])):
        return jsonify({"error": "Not found"}), 404
    msg = g["messages"][idx]
    g["pinned"] = [{"author": msg["author"], "body": msg["body"]}]
    write_state(state)
    broadcast("group_updated", {"id": gid})
    return jsonify({"ok": True})


@app.put("/api/groups/<gid>/messages/<int:idx>")
def api_edit_group_msg(gid, idx):
    data = request.get_json(force=True) or {}
    author = (data.get("author") or "").strip()
    state = read_state()
    g = state["groups"].get(gid)
    if not g or idx < 0 or idx >= len(g.get("messages", [])):
        return jsonify({"error": "Not found"}), 404
    msg = g["messages"][idx]
    if not author or author == "Anonymous" or msg.get("author", "").lower() != author.lower():
        return jsonify({"error": "Forbidden"}), 403
    if data.get("body"):
        msg["body"] = data["body"].strip()
        msg["edited"] = True
    write_state(state)
    broadcast("group_updated", {"id": gid})
    return jsonify(msg)


@app.delete("/api/groups/<gid>/messages/<int:idx>")
def api_delete_group_msg(gid, idx):
    author = (request.args.get("author") or "").strip()
    state = read_state()
    g = state["groups"].get(gid)
    if not g or idx < 0 or idx >= len(g.get("messages", [])):
        return jsonify({"error": "Not found"}), 404
    msg = g["messages"][idx]
    if not author or author == "Anonymous" or msg.get("author", "").lower() != author.lower():
        return jsonify({"error": "Forbidden"}), 403
    g["messages"].pop(idx)
    write_state(state)
    broadcast("group_updated", {"id": gid})
    return jsonify({"ok": True})


@app.post("/api/rooms")
def api_create_room():
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Name required"}), 400
    rid = uuid.uuid4().hex[:10]
    rm = {"id": rid, "name": name, "visibility": data.get("visibility") or "public", "host": (data.get("host") or "Anonymous").strip(), "created_at": now_iso()}
    state = read_state()
    state["rooms"][rid] = rm
    write_state(state)
    return jsonify(rm), 201


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True, debug=False)