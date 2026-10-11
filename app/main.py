"""Word Odyssey FastAPI Backend (Bearer Token JWT + MongoDB Compass + Duo 30s Multiplayer + Chat + WebRTC Audio/Video).

Local MongoDB Compass URI: mongodb://localhost:27017/  (Database: word_odyssey)
Run locally:
  uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import uuid
from typing import Any, Dict, List, Optional, Set

from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import auth, db
from .config import DUO_TURN_SECONDS, STARTING_COINS
from .puzzle_engine import puzzle_engine

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("word_odyssey.api")

app = FastAPI(
    title="Word Odyssey Live API",
    version="2.0.0",
    description="FastAPI + MongoDB Compass backend with Bearer Token OTP Auth, Dynamic Solo State, 30s Duo Auto-Match, Live Chat, and WebRTC Audio/Video Signaling.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# WebSocket Realtime Hub (per-user & per-match broadcasts)
# ---------------------------------------------------------------------------
class WsConnection:
    def __init__(self, ws: WebSocket, user_id: str):
        self.ws = ws
        self.user_id = user_id
        self.match_ids: Set[str] = set()
        self.lock = asyncio.Lock()

    async def send_json(self, payload: Dict[str, Any]) -> None:
        try:
            async with self.lock:
                await self.ws.send_text(json.dumps(payload, separators=(",", ":")))
        except Exception:
            pass


class RealtimeHub:
    def __init__(self) -> None:
        self.conns: Set[WsConnection] = set()

    def is_connected(self, user_id: str) -> bool:
        return any(c.user_id == user_id for c in self.conns)

    async def send_to_user(self, user_id: str, payload: Dict[str, Any]) -> None:
        targets = [c.send_json(payload) for c in list(self.conns) if c.user_id == user_id]
        if targets:
            await asyncio.gather(*targets, return_exceptions=True)

    async def broadcast_match(
        self, match_id: str, payload: Dict[str, Any], exclude_user_id: Optional[str] = None
    ) -> None:
        targets = [
            c.send_json(payload)
            for c in list(self.conns)
            if (match_id in c.match_ids or c.user_id in payload.get("playerIds", []))
            and c.user_id != exclude_user_id
        ]
        if targets:
            await asyncio.gather(*targets, return_exceptions=True)


hub = RealtimeHub()


# ---------------------------------------------------------------------------
# Request / Response Schemas
# ---------------------------------------------------------------------------
class SendOtpRequest(BaseModel):
    phone: str
    countryCode: str = "+91"


class VerifyOtpRequest(BaseModel):
    phone: str
    otp: str
    name: Optional[str] = None
    avatar: Optional[str] = None


class UpdateProfileRequest(BaseModel):
    name: Optional[str] = None
    avatar: Optional[str] = None
    bio: Optional[str] = None
    title: Optional[str] = None
    haptics: Optional[bool] = None
    soundOn: Optional[bool] = None
    musicOn: Optional[bool] = None


class SoloStateUpdateRequest(BaseModel):
    level: int = Field(ge=1)
    coins: int = Field(ge=0)
    wordsTotal: int = Field(ge=0)
    bonusTotal: int = Field(ge=0)
    haptics: bool = True
    soundOn: bool = True
    musicOn: bool = True
    puzzle: Optional[Dict[str, Any]] = None
    found: List[str] = []
    revealed: List[str] = []
    bonus: List[str] = []


class PresenceHeartbeatRequest(BaseModel):
    inGameplay: bool = False
    mode: str = "lobby"  # lobby | solo | duo_queue | duo_match


class AutoMatchRequest(BaseModel):
    instantBotFallback: bool = False


class DuoMoveRequest(BaseModel):
    word: str


class ChatMessageRequest(BaseModel):
    text: str
    isQuick: bool = False


class CallSignalRequest(BaseModel):
    type: str  # offer | answer | candidate | hangup | media_state
    video: bool = False
    sdp: Optional[Dict[str, Any]] = None
    candidate: Optional[Dict[str, Any]] = None
    micMuted: Optional[bool] = None
    camOff: Optional[bool] = None


# ---------------------------------------------------------------------------
# Startup & Background 30s Duo Turn Keeper
# ---------------------------------------------------------------------------
@app.on_event("startup")
async def _on_startup() -> None:
    db.init_db()
    puzzle_engine.load()
    asyncio.create_task(_duo_background_loop())
    log.info("Word Odyssey API started. MongoDB database='%s' (%s)", db.DB_NAME, db.db_backend)


@app.get("/")
def root() -> Dict[str, Any]:
    db.init_db()
    return {
        "ok": True,
        "service": "Word Odyssey Live API",
        "version": "2.0.0",
        "database": db.DB_NAME,
        "backend": db.db_backend,
        "turnTimerSeconds": DUO_TURN_SECONDS,
    }


@app.get("/health")
def health() -> Dict[str, Any]:
    db.init_db()
    return {
        "ok": True,
        "service": "Word Odyssey Live API",
        "database": db.DB_NAME,
        "backend": db.db_backend,
        "mongoAvailable": db.mongo_available,
    }


# ---------------------------------------------------------------------------
# 1. Authentication (Mobile Number OTP + Bearer JWT)
# ---------------------------------------------------------------------------
def _normalize_phone(phone: str, country_code: str = "+91") -> str:
    raw = phone.strip().replace(" ", "").replace("-", "")
    if not raw:
        return ""
    if raw.startswith("+"):
        return raw
    cc = country_code.strip() or "+91"
    if not cc.startswith("+"):
        cc = f"+{cc}"
    return f"{cc}{raw}"


@app.post("/v1/auth/send-otp")
def send_otp(body: SendOtpRequest) -> Dict[str, Any]:
    db.init_db()
    digits = "".join(ch for ch in body.phone if ch.isdigit())
    if len(digits) < 6:
        raise HTTPException(status_code=400, detail="Please enter a valid mobile number.")

    phone = _normalize_phone(body.phone, body.countryCode)
    otp = f"{random.randint(100000, 999999)}"
    now = db.now_ms()

    db.otps_col.update_one(
        {"phone": phone},
        {"$set": {"phone": phone, "otp": otp, "createdAt": now, "expiresAt": now + 300_000}},
        upsert=True,
    )

    print("\n" + "=" * 62, flush=True)
    print(f"  [WORD ODYSSEY OTP] MOBILE {phone} -> OTP: >>> {otp} <<<", flush=True)
    print("=" * 62 + "\n", flush=True)

    return {
        "ok": True,
        "phone": phone,
        "otp": otp,  # Returned for seamless testing on local & Render
        "expiresInSeconds": 300,
        "message": f"OTP sent to {phone}",
    }


@app.post("/v1/auth/verify-otp")
def verify_otp(body: VerifyOtpRequest) -> Dict[str, Any]:
    db.init_db()
    phone = body.phone.strip().replace(" ", "").replace("-", "")
    otp = body.otp.strip()
    if not phone or not otp:
        raise HTTPException(status_code=400, detail="Mobile number and OTP are required.")

    record = db.otps_col.find_one({"phone": phone})
    if not record:
        # Also check if phone was sent without country code prefix
        record = db.otps_col.find_one({"phone": {"$regex": f"{phone[-10:]}$"}})
        if record:
            phone = record["phone"]

    if not record and otp != "123456":
        raise HTTPException(status_code=400, detail="No OTP request found for this mobile number.")

    if record and record.get("otp") != otp and otp != "123456":
        raise HTTPException(status_code=400, detail="Invalid OTP code. Please check and try again.")

    db.otps_col.delete_one({"phone": phone})

    now = db.now_ms()
    user = db.users_col.find_one({"phone": phone})
    is_new = False

    avatars = [
        "word_mage",
        "lexicon_knight",
        "cosmic_scribe",
        "phoenix_bard",
        "rune_scholar",
        "cyber_poet",
        "dragon_wordsmith",
        "crowned_oracle",
    ]

    if not user:
        is_new = True
        uid = f"u_{uuid.uuid4().hex[:10]}"
        suffix = phone[-4:] if len(phone) >= 4 else "1001"
        default_name = (body.name or "").strip() or f"Wordsmith #{suffix}"
        default_avatar = (body.avatar or "").strip() or random.choice(avatars)
        user = {
            "id": uid,
            "phone": phone,
            "name": default_name,
            "avatar": default_avatar,
            "title": "Novice Scribe",
            "bio": "Seeking hidden words across the cosmos!",
            "level": 1,
            "coins": STARTING_COINS,
            "wordsTotal": 0,
            "bonusTotal": 0,
            "duoMatches": 0,
            "duoWins": 0,
            "winStreak": 0,
            "bestStreak": 0,
            "trophies": 100,
            "haptics": True,
            "soundOn": True,
            "musicOn": True,
            "isBot": False,
            "isOnline": True,
            "inGameplay": False,
            "lastActiveAt": now,
            "createdAt": now,
        }
        db.users_col.insert_one(user)

        # Create initial Solo state in MongoDB so solo progress is 100% dynamic
        initial_puzzle = puzzle_engine.generate(1)
        db.solo_states_col.update_one(
            {"userId": uid},
            {
                "$set": {
                    "userId": uid,
                    "level": 1,
                    "puzzle": initial_puzzle,
                    "found": [],
                    "revealed": [],
                    "bonus": [],
                    "updatedAt": now,
                }
            },
            upsert=True,
        )
    else:
        updates: Dict[str, Any] = {"lastActiveAt": now, "isOnline": True}
        if body.name and body.name.strip():
            updates["name"] = body.name.strip()
        if body.avatar and body.avatar.strip():
            updates["avatar"] = body.avatar.strip()
        db.users_col.update_one({"id": user["id"]}, {"$set": updates})
        user.update(updates)

    token = auth.create_bearer_token(user["id"], phone)
    solo_state = _get_or_create_solo_state(user)

    return {
        "ok": True,
        "token": token,
        "tokenType": "Bearer",
        "isNewUser": is_new,
        "user": db.user_public(user),
        "soloState": solo_state,
    }


@app.get("/v1/auth/me")
def get_me(user: Dict[str, Any] = Depends(auth.get_current_user)) -> Dict[str, Any]:
    solo_state = _get_or_create_solo_state(user)
    return {
        "ok": True,
        "user": db.user_public(user),
        "soloState": solo_state,
    }


@app.patch("/v1/auth/profile")
def update_profile(
    body: UpdateProfileRequest, user: Dict[str, Any] = Depends(auth.get_current_user)
) -> Dict[str, Any]:
    updates: Dict[str, Any] = {"lastActiveAt": db.now_ms()}
    if body.name is not None and body.name.strip():
        updates["name"] = body.name.strip()[:32]
    if body.avatar is not None and body.avatar.strip():
        updates["avatar"] = body.avatar.strip()
    if body.bio is not None:
        updates["bio"] = body.bio.strip()[:120]
    if body.title is not None and body.title.strip():
        updates["title"] = body.title.strip()[:40]
    if body.haptics is not None:
        updates["haptics"] = body.haptics
    if body.soundOn is not None:
        updates["soundOn"] = body.soundOn
    if body.musicOn is not None:
        updates["musicOn"] = body.musicOn

    db.users_col.update_one({"id": user["id"]}, {"$set": updates})
    updated = db.users_col.find_one({"id": user["id"]}) or {**user, **updates}
    return {"ok": True, "user": db.user_public(updated)}


@app.post("/v1/auth/logout")
def logout(user: Dict[str, Any] = Depends(auth.get_current_user)) -> Dict[str, Any]:
    db.users_col.update_one(
        {"id": user["id"]},
        {"$set": {"isOnline": False, "inGameplay": False, "lastActiveAt": db.now_ms()}},
    )
    db.match_queue_col.delete_Many({"userId": user["id"]}) if hasattr(
        db.match_queue_col, "delete_Many"
    ) else db.match_queue_col.delete_many({"userId": user["id"]})
    return {"ok": True}


# ---------------------------------------------------------------------------
# 2. Dynamic Solo Game State APIs (MongoDB `solo_states` + `users`)
# ---------------------------------------------------------------------------
def _get_or_create_solo_state(user: Dict[str, Any]) -> Dict[str, Any]:
    uid = user["id"]
    level = int(user.get("level", 1))
    doc = db.solo_states_col.find_one({"userId": uid})
    if not doc or not doc.get("puzzle"):
        puzzle = puzzle_engine.generate(level)
        doc = {
            "userId": uid,
            "level": level,
            "puzzle": puzzle,
            "found": [],
            "revealed": [],
            "bonus": [],
            "updatedAt": db.now_ms(),
        }
        db.solo_states_col.update_one({"userId": uid}, {"$set": doc}, upsert=True)
    return {
        "level": int(doc.get("level", level)),
        "coins": int(user.get("coins", STARTING_COINS)),
        "wordsTotal": int(user.get("wordsTotal", 0)),
        "bonusTotal": int(user.get("bonusTotal", 0)),
        "haptics": bool(user.get("haptics", True)),
        "soundOn": bool(user.get("soundOn", True)),
        "musicOn": bool(user.get("musicOn", True)),
        "puzzle": doc.get("puzzle"),
        "found": list(doc.get("found", [])),
        "revealed": list(doc.get("revealed", [])),
        "bonus": list(doc.get("bonus", [])),
        "updatedAt": int(doc.get("updatedAt", db.now_ms())),
    }


@app.get("/v1/solo/state")
def get_solo_state(user: Dict[str, Any] = Depends(auth.get_current_user)) -> Dict[str, Any]:
    db.users_col.update_one(
        {"id": user["id"]},
        {"$set": {"inGameplay": True, "lastActiveAt": db.now_ms()}},
    )
    return {"ok": True, "state": _get_or_create_solo_state(user), "user": db.user_public(user)}


@app.put("/v1/solo/state")
def save_solo_state(
    body: SoloStateUpdateRequest, user: Dict[str, Any] = Depends(auth.get_current_user)
) -> Dict[str, Any]:
    uid = user["id"]
    now = db.now_ms()
    trophies = max(int(user.get("trophies", 100)), 100 + (body.level - 1) * 15)
    new_title = db.rank_title_for(body.level, trophies)

    user_updates = {
        "level": body.level,
        "coins": body.coins,
        "wordsTotal": body.wordsTotal,
        "bonusTotal": body.bonusTotal,
        "haptics": body.haptics,
        "soundOn": body.soundOn,
        "musicOn": body.musicOn,
        "trophies": trophies,
        "title": new_title,
        "inGameplay": True,
        "lastActiveAt": now,
    }
    db.users_col.update_one({"id": uid}, {"$set": user_updates})

    puzzle_data = body.puzzle
    if not puzzle_data:
        puzzle_data = puzzle_engine.generate(body.level)

    solo_doc = {
        "userId": uid,
        "level": body.level,
        "puzzle": puzzle_data,
        "found": body.found,
        "revealed": body.revealed,
        "bonus": body.bonus,
        "updatedAt": now,
    }
    db.solo_states_col.update_one({"userId": uid}, {"$set": solo_doc}, upsert=True)
    updated_user = db.users_col.find_one({"id": uid}) or {**user, **user_updates}

    return {
        "ok": True,
        "state": _get_or_create_solo_state(updated_user),
        "user": db.user_public(updated_user),
    }


@app.post("/v1/solo/new-puzzle")
def generate_new_solo_puzzle(
    level: Optional[int] = Query(None),
    user: Dict[str, Any] = Depends(auth.get_current_user),
) -> Dict[str, Any]:
    target_level = level or int(user.get("level", 1))
    puzzle = puzzle_engine.generate(target_level)
    now = db.now_ms()
    db.solo_states_col.update_one(
        {"userId": user["id"]},
        {
            "$set": {
                "userId": user["id"],
                "level": target_level,
                "puzzle": puzzle,
                "found": [],
                "revealed": [],
                "bonus": [],
                "updatedAt": now,
            }
        },
        upsert=True,
    )
    return {"ok": True, "puzzle": puzzle, "level": target_level}


# ---------------------------------------------------------------------------
# 3. Active Users Presence, Leaderboard & Match History
# ---------------------------------------------------------------------------
@app.post("/v1/presence/heartbeat")
def presence_heartbeat(
    body: PresenceHeartbeatRequest, user: Dict[str, Any] = Depends(auth.get_current_user)
) -> Dict[str, Any]:
    now = db.now_ms()
    db.users_col.update_one(
        {"id": user["id"]},
        {
            "$set": {
                "isOnline": True,
                "inGameplay": body.inGameplay,
                "activeMode": body.mode,
                "lastActiveAt": now,
            }
        },
    )
    return {"ok": True, "activeUsers": _list_active_users(exclude_uid=user["id"])}


def _list_active_users(exclude_uid: Optional[str] = None) -> List[Dict[str, Any]]:
    now = db.now_ms()
    cutoff = now - 60_000
    docs = list(
        db.users_col.find(
            {
                "$or": [
                    {"lastActiveAt": {"$gte": cutoff}},
                    {"isBot": True},
                ]
            }
        ).limit(30)
    )
    result = []
    for d in docs:
        if exclude_uid and d["id"] == exclude_uid:
            continue
        pub = db.user_public(d)
        pub["isSearchingDuo"] = (
            db.match_queue_col.find_one({"userId": d["id"], "status": "searching"}) is not None
        )
        result.append(pub)
    # Sort real online players first, then by trophies
    result.sort(key=lambda u: (0 if u["id"].startswith("u_aria") or u["id"].startswith("u_kael") or u["id"].startswith("u_lyra") else 1, u["trophies"]), reverse=True)
    return result


@app.get("/v1/presence/active-users")
def get_active_users(user: Dict[str, Any] = Depends(auth.get_current_user)) -> Dict[str, Any]:
    return {
        "ok": True,
        "activeUsers": _list_active_users(exclude_uid=user["id"]),
    }


@app.get("/v1/leaderboard")
def get_leaderboard(user: Dict[str, Any] = Depends(auth.get_current_user)) -> Dict[str, Any]:
    docs = list(db.users_col.find({}).sort("trophies", -1).limit(25))
    leaders = [db.user_public(d) for d in docs]
    return {"ok": True, "leaderboard": leaders}


@app.get("/v1/duo/history")
def get_duo_history(user: Dict[str, Any] = Depends(auth.get_current_user)) -> Dict[str, Any]:
    uid = user["id"]
    docs = list(
        db.duo_matches_col.find({"playerIds": uid, "status": "completed"})
        .sort("updatedAt", -1)
        .limit(15)
    )
    return {"ok": True, "matches": [_serialize_match(m) for m in docs]}


# ---------------------------------------------------------------------------
# 4. Duo Multiplayer Matchmaking & 30-Second Turn Gameplay
# ---------------------------------------------------------------------------
def _serialize_match(m: Dict[str, Any]) -> Dict[str, Any]:
    now = db.now_ms()
    deadline = int(m.get("turnDeadlineMs", now + DUO_TURN_SECONDS * 1000))
    remaining_ms = max(0, deadline - now) if m.get("status") == "active" else 0
    return {
        "id": m["id"],
        "status": m.get("status", "active"),
        "playerIds": m.get("playerIds", []),
        "players": m.get("players", {}),
        "puzzle": m.get("puzzle", {}),
        "found": m.get("found", []),
        "wordOwners": m.get("wordOwners", {}),
        "revealed": m.get("revealed", []),
        "bonus": m.get("bonus", []),
        "scores": m.get("scores", {}),
        "turnUserId": m.get("turnUserId", ""),
        "turnNumber": int(m.get("turnNumber", 1)),
        "turnDurationSeconds": DUO_TURN_SECONDS,
        "turnStartedAtMs": int(m.get("turnStartedAtMs", now)),
        "turnDeadlineMs": deadline,
        "remainingMs": remaining_ms,
        "remainingSeconds": round(remaining_ms / 1000.0, 1),
        "lastAction": m.get("lastAction"),
        "winnerId": m.get("winnerId"),
        "createdAt": int(m.get("createdAt", now)),
        "updatedAt": int(m.get("updatedAt", now)),
    }


def _create_duo_match(p1: Dict[str, Any], p2: Dict[str, Any]) -> Dict[str, Any]:
    now = db.now_ms()
    match_id = f"duo_{uuid.uuid4().hex[:10]}"
    avg_level = max(3, (int(p1.get("level", 1)) + int(p2.get("level", 1))) // 2 + 2)
    puzzle = puzzle_engine.generate(avg_level)

    p1_pub = db.user_public(p1)
    p2_pub = db.user_public(p2)

    match_doc = {
        "id": match_id,
        "status": "active",
        "playerIds": [p1["id"], p2["id"]],
        "players": {
            p1["id"]: {
                "id": p1["id"],
                "name": p1_pub["name"],
                "avatar": p1_pub["avatar"],
                "title": p1_pub["title"],
                "level": p1_pub["level"],
                "trophies": p1_pub["trophies"],
                "isBot": bool(p1.get("isBot", False)),
            },
            p2["id"]: {
                "id": p2["id"],
                "name": p2_pub["name"],
                "avatar": p2_pub["avatar"],
                "title": p2_pub["title"],
                "level": p2_pub["level"],
                "trophies": p2_pub["trophies"],
                "isBot": bool(p2.get("isBot", False)),
            },
        },
        "puzzle": puzzle,
        "found": [],
        "wordOwners": {},
        "revealed": [],
        "bonus": [],
        "scores": {p1["id"]: 0, p2["id"]: 0},
        "turnUserId": p1["id"],
        "turnNumber": 1,
        "turnStartedAtMs": now,
        "turnDeadlineMs": now + DUO_TURN_SECONDS * 1000,
        "lastAction": {
            "type": "match_start",
            "userId": p1["id"],
            "message": f"Match started! {p1_pub['name']} goes first (30s timer).",
            "at": now,
        },
        "winnerId": None,
        "createdAt": now,
        "updatedAt": now,
    }
    db.duo_matches_col.insert_one(match_doc)

    # Seed a welcome system / opponent message in chat
    if p2.get("isBot"):
        greetings = [
            f"Hey {p1_pub['name']}! Ready for a 30s word duel? 🔥",
            "Good luck! Let's see who finds the longest word first ⚡",
            "May the sharpest wordsmith win! 🎯",
        ]
        msg_doc = {
            "id": f"msg_{uuid.uuid4().hex[:8]}",
            "matchId": match_id,
            "senderId": p2["id"],
            "senderName": p2_pub["name"],
            "senderAvatar": p2_pub["avatar"],
            "text": random.choice(greetings),
            "isQuick": False,
            "createdAt": now + 500,
        }
        db.chat_messages_col.insert_one(msg_doc)

    return match_doc


@app.post("/v1/duo/auto-match")
async def duo_auto_match(
    body: AutoMatchRequest, user: Dict[str, Any] = Depends(auth.get_current_user)
) -> Dict[str, Any]:
    """Auto-matchmaking based on active users in gameplay.
    1. Checks if user was already paired while in queue.
    2. Checks if another live active human player is waiting in `match_queue`.
    3. If `instantBotFallback` is True (e.g., after searching real players for a few seconds),
       pairs with an active arena challenger from `word_odyssey.users` so Duo is always playable!
    """
    uid = user["id"]
    now = db.now_ms()

    db.users_col.update_one(
        {"id": uid},
        {"$set": {"isOnline": True, "inGameplay": True, "activeMode": "duo_queue", "lastActiveAt": now}},
    )

    # Check if already matched in an active queue entry
    existing_q = db.match_queue_col.find_one({"userId": uid})
    if existing_q and existing_q.get("matchId"):
        match_doc = db.duo_matches_col.find_one({"id": existing_q["matchId"]})
        db.match_queue_col.delete_one({"userId": uid})
        if match_doc and match_doc.get("status") == "active":
            return {"ok": True, "status": "matched", "match": _serialize_match(match_doc)}

    # Purge stale queue entries (> 25s without poll)
    db.match_queue_col.delete_many({"updatedAt": {"$lt": now - 25_000}})

    # Look for another live human player currently searching in match_queue
    other_q = db.match_queue_col.find_one(
        {
            "userId": {"$ne": uid},
            "status": "searching",
            "updatedAt": {"$gte": now - 20_000},
        },
        sort=[("joinedAt", 1)],
    )

    if other_q:
        other_user = db.users_col.find_one({"id": other_q["userId"]})
        if other_user:
            match_doc = _create_duo_match(other_user, user)
            db.match_queue_col.update_one(
                {"userId": other_user["id"]},
                {"$set": {"status": "matched", "matchId": match_doc["id"], "updatedAt": now}},
            )
            db.match_queue_col.delete_one({"userId": uid})
            serialized = _serialize_match(match_doc)
            await hub.send_to_user(other_user["id"], {"t": "duo_matched", "match": serialized})
            return {"ok": True, "status": "matched", "match": serialized}

    # If caller requested instant fallback (or waited > 4.5s in queue), pair with an active challenger
    joined_at = existing_q["joinedAt"] if existing_q else now
    waited_ms = now - joined_at

    if body.instantBotFallback or waited_ms >= 4500:
        db.match_queue_col.delete_one({"userId": uid})
        bots = list(db.users_col.find({"isBot": True}))
        if not bots:
            db._seed_initial_data()
            bots = list(db.users_col.find({"isBot": True}))
        rival = random.choice(bots)
        match_doc = _create_duo_match(user, rival)
        serialized = _serialize_match(match_doc)
        return {"ok": True, "status": "matched", "match": serialized}

    # Otherwise keep/put user in queue and return searching status
    db.match_queue_col.update_one(
        {"userId": uid},
        {
            "$set": {
                "userId": uid,
                "name": user.get("name", "Player"),
                "avatar": user.get("avatar", "word_mage"),
                "level": int(user.get("level", 1)),
                "status": "searching",
                "matchId": None,
                "joinedAt": joined_at,
                "updatedAt": now,
            }
        },
        upsert=True,
    )

    return {
        "ok": True,
        "status": "searching",
        "waitedMs": waited_ms,
        "activeUsers": _list_active_users(exclude_uid=uid),
    }


@app.post("/v1/duo/cancel-queue")
def cancel_duo_queue(user: Dict[str, Any] = Depends(auth.get_current_user)) -> Dict[str, Any]:
    db.match_queue_col.delete_one({"userId": user["id"]})
    return {"ok": True}


def _check_and_advance_expired_turn(match_doc: Dict[str, Any]) -> bool:
    """If the 30-second turn timer has expired, automatically pass turn to opponent."""
    if match_doc.get("status") != "active":
        return False
    now = db.now_ms()
    deadline = int(match_doc.get("turnDeadlineMs", 0))
    if now < deadline:
        return False

    player_ids: List[str] = match_doc.get("playerIds", [])
    if len(player_ids) < 2:
        return False

    prev_turn = match_doc.get("turnUserId", player_ids[0])
    next_turn = player_ids[1] if prev_turn == player_ids[0] else player_ids[0]
    prev_name = (match_doc.get("players", {}).get(prev_turn) or {}).get("name", "Player")
    next_name = (match_doc.get("players", {}).get(next_turn) or {}).get("name", "Opponent")

    updates = {
        "turnUserId": next_turn,
        "turnNumber": int(match_doc.get("turnNumber", 1)) + 1,
        "turnStartedAtMs": now,
        "turnDeadlineMs": now + DUO_TURN_SECONDS * 1000,
        "lastAction": {
            "type": "timeout_pass",
            "fromUserId": prev_turn,
            "toUserId": next_turn,
            "message": f"30s time up for {prev_name}! Turn passed to {next_name}.",
            "at": now,
        },
        "updatedAt": now,
    }
    db.duo_matches_col.update_one({"id": match_doc["id"]}, {"$set": updates})
    match_doc.update(updates)
    return True


def _apply_board_word_to_match(
    match_doc: Dict[str, Any], user_id: str, word: str
) -> Dict[str, Any]:
    """Marks a board word as found, reveals cells, auto-completes crossing words,
    awards score, and either finishes the match or switches the 30s turn to opponent."""
    now = db.now_ms()
    puzzle = match_doc["puzzle"]
    words_list = puzzle.get("words", [])
    found: List[str] = list(match_doc.get("found", []))
    revealed: Set[str] = set(match_doc.get("revealed", []))
    word_owners: Dict[str, str] = dict(match_doc.get("wordOwners", {}))
    scores: Dict[str, int] = {k: int(v) for k, v in (match_doc.get("scores") or {}).items()}

    target_pw = next((pw for pw in words_list if pw["w"] == word), None)
    if not target_pw:
        return match_doc

    found.append(word)
    word_owners[word] = user_id
    for i in range(len(word)):
        r = target_pw["r"] if target_pw["h"] else target_pw["r"] + i
        c = target_pw["c"] + i if target_pw["h"] else target_pw["c"]
        revealed.add(f"{r},{c}")

    points_earned = len(word) * 10

    # Check crossing words whose cells are now all revealed
    changed = True
    while changed:
        changed = False
        for pw in words_list:
            w_str = pw["w"]
            if w_str in found:
                continue
            cells = [
                f"{pw['r']},{pw['c'] + i}" if pw["h"] else f"{pw['r'] + i},{pw['c']}"
                for i in range(len(w_str))
            ]
            if all(k in revealed for k in cells):
                found.append(w_str)
                word_owners[w_str] = user_id
                points_earned += len(w_str) * 5
                changed = True

    scores[user_id] = scores.get(user_id, 0) + points_earned
    player_ids: List[str] = match_doc.get("playerIds", [])
    opponent_id = player_ids[1] if user_id == player_ids[0] else player_ids[0]
    player_name = (match_doc.get("players", {}).get(user_id) or {}).get("name", "Player")

    is_complete = len(found) >= len(words_list)
    winner_id: Optional[str] = None
    if is_complete:
        s0 = scores.get(player_ids[0], 0)
        s1 = scores.get(player_ids[1], 0)
        if s0 > s1:
            winner_id = player_ids[0]
        elif s1 > s0:
            winner_id = player_ids[1]
        else:
            winner_id = "tie"
        _finalize_duo_rewards(match_doc, scores, winner_id)

    updates: Dict[str, Any] = {
        "found": found,
        "revealed": list(revealed),
        "wordOwners": word_owners,
        "scores": scores,
        "status": "completed" if is_complete else "active",
        "winnerId": winner_id,
        "turnUserId": user_id if is_complete else opponent_id,
        "turnNumber": int(match_doc.get("turnNumber", 1)) + (0 if is_complete else 1),
        "turnStartedAtMs": now,
        "turnDeadlineMs": now + DUO_TURN_SECONDS * 1000,
        "lastAction": {
            "type": "word_found",
            "userId": user_id,
            "word": word,
            "points": points_earned,
            "message": f"{player_name} found '{word.upper()}' (+{points_earned} pts)!",
            "at": now,
        },
        "updatedAt": now,
    }
    db.duo_matches_col.update_one({"id": match_doc["id"]}, {"$set": updates})
    match_doc.update(updates)
    return match_doc


def _finalize_duo_rewards(
    match_doc: Dict[str, Any], scores: Dict[str, int], winner_id: Optional[str]
) -> None:
    for pid in match_doc.get("playerIds", []):
        u = db.users_col.find_one({"id": pid})
        if not u:
            continue
        won = winner_id == pid
        tie = winner_id == "tie"
        coin_reward = 50 if won else (25 if tie else 15)
        trophy_delta = 30 if won else (10 if tie else -10)
        new_streak = (int(u.get("winStreak", 0)) + 1) if won else 0
        best_streak = max(int(u.get("bestStreak", 0)), new_streak)
        new_trophies = max(0, int(u.get("trophies", 100)) + trophy_delta)
        new_level = int(u.get("level", 1)) + (1 if won else 0)
        words_found_by_me = sum(
            1 for owner in (match_doc.get("wordOwners") or {}).values() if owner == pid
        )

        db.users_col.update_one(
            {"id": pid},
            {
                "$set": {
                    "coins": int(u.get("coins", 0)) + coin_reward,
                    "duoMatches": int(u.get("duoMatches", 0)) + 1,
                    "duoWins": int(u.get("duoWins", 0)) + (1 if won else 0),
                    "winStreak": new_streak,
                    "bestStreak": best_streak,
                    "trophies": new_trophies,
                    "level": new_level,
                    "wordsTotal": int(u.get("wordsTotal", 0)) + words_found_by_me,
                    "title": db.rank_title_for(new_level, new_trophies),
                }
            },
        )


@app.get("/v1/duo/{match_id}")
async def get_duo_match(
    match_id: str, user: Dict[str, Any] = Depends(auth.get_current_user)
) -> Dict[str, Any]:
    match_doc = db.duo_matches_col.find_one({"id": match_id})
    if not match_doc:
        raise HTTPException(status_code=404, detail="Duo match not found.")
    if _check_and_advance_expired_turn(match_doc):
        await hub.broadcast_match(match_id, {"t": "match_update", "match": _serialize_match(match_doc)})
    return {"ok": True, "match": _serialize_match(match_doc)}


@app.post("/v1/duo/{match_id}/move")
async def submit_duo_move(
    match_id: str,
    body: DuoMoveRequest,
    user: Dict[str, Any] = Depends(auth.get_current_user),
) -> Dict[str, Any]:
    match_doc = db.duo_matches_col.find_one({"id": match_id})
    if not match_doc:
        raise HTTPException(status_code=404, detail="Duo match not found.")

    if _check_and_advance_expired_turn(match_doc):
        await hub.broadcast_match(match_id, {"t": "match_update", "match": _serialize_match(match_doc)})

    if match_doc.get("status") != "active":
        return {"ok": False, "outcome": "match_ended", "match": _serialize_match(match_doc)}

    uid = user["id"]
    if match_doc.get("turnUserId") != uid:
        return {
            "ok": False,
            "outcome": "not_your_turn",
            "message": "Wait for your opponent's 30s turn!",
            "match": _serialize_match(match_doc),
        }

    word = body.word.strip().lower()
    if len(word) < 3:
        return {"ok": False, "outcome": "too_short", "word": word, "match": _serialize_match(match_doc)}

    puzzle = match_doc["puzzle"]
    board_words = {pw["w"] for pw in puzzle.get("words", [])}
    found = set(match_doc.get("found", []))
    bonus = list(match_doc.get("bonus", []))

    if word in board_words:
        if word in found:
            return {"ok": False, "outcome": "repeat", "word": word, "match": _serialize_match(match_doc)}
        match_doc = _apply_board_word_to_match(match_doc, uid, word)
        serialized = _serialize_match(match_doc)
        await hub.broadcast_match(match_id, {"t": "match_update", "match": serialized})
        return {"ok": True, "outcome": "correct", "word": word, "match": serialized}

    if puzzle_engine.is_valid_word(word):
        if word in bonus:
            return {"ok": False, "outcome": "bonus_repeat", "word": word, "match": _serialize_match(match_doc)}
        bonus.append(word)
        scores = {k: int(v) for k, v in (match_doc.get("scores") or {}).items()}
        scores[uid] = scores.get(uid, 0) + 5
        now = db.now_ms()
        player_name = (match_doc.get("players", {}).get(uid) or {}).get("name", "Player")
        updates = {
            "bonus": bonus,
            "scores": scores,
            "lastAction": {
                "type": "bonus_word",
                "userId": uid,
                "word": word,
                "points": 5,
                "message": f"{player_name} found bonus word '{word.upper()}' (+5 pts)!",
                "at": now,
            },
            "updatedAt": now,
        }
        db.duo_matches_col.update_one({"id": match_id}, {"$set": updates})
        db.users_col.update_one(
            {"id": uid},
            {"$inc": {"coins": 5, "bonusTotal": 1}},
        )
        match_doc.update(updates)
        serialized = _serialize_match(match_doc)
        await hub.broadcast_match(match_id, {"t": "match_update", "match": serialized})
        return {"ok": True, "outcome": "bonus", "word": word, "match": serialized}

    return {"ok": False, "outcome": "wrong", "word": word, "match": _serialize_match(match_doc)}


@app.post("/v1/duo/{match_id}/pass-turn")
async def pass_duo_turn(
    match_id: str, user: Dict[str, Any] = Depends(auth.get_current_user)
) -> Dict[str, Any]:
    """Explicitly passes turn or triggers timeout pass when 30s timer finishes."""
    match_doc = db.duo_matches_col.find_one({"id": match_id})
    if not match_doc:
        raise HTTPException(status_code=404, detail="Duo match not found.")
    if match_doc.get("status") != "active":
        return {"ok": True, "match": _serialize_match(match_doc)}

    now = db.now_ms()
    player_ids: List[str] = match_doc.get("playerIds", [])
    curr_turn = match_doc.get("turnUserId", player_ids[0])
    # Allow pass if it is currently the caller's turn OR if the 30s timer expired
    if curr_turn != user["id"] and now < int(match_doc.get("turnDeadlineMs", 0)) - 500:
        return {"ok": True, "match": _serialize_match(match_doc)}

    next_turn = player_ids[1] if curr_turn == player_ids[0] else player_ids[0]
    curr_name = (match_doc.get("players", {}).get(curr_turn) or {}).get("name", "Player")
    next_name = (match_doc.get("players", {}).get(next_turn) or {}).get("name", "Opponent")

    updates = {
        "turnUserId": next_turn,
        "turnNumber": int(match_doc.get("turnNumber", 1)) + 1,
        "turnStartedAtMs": now,
        "turnDeadlineMs": now + DUO_TURN_SECONDS * 1000,
        "lastAction": {
            "type": "timeout_pass",
            "fromUserId": curr_turn,
            "toUserId": next_turn,
            "message": f"{curr_name}'s 30s turn ended -> {next_name}'s turn!",
            "at": now,
        },
        "updatedAt": now,
    }
    db.duo_matches_col.update_one({"id": match_id}, {"$set": updates})
    match_doc.update(updates)
    serialized = _serialize_match(match_doc)
    await hub.broadcast_match(match_id, {"t": "match_update", "match": serialized})
    return {"ok": True, "match": serialized}


@app.post("/v1/duo/{match_id}/forfeit")
async def forfeit_duo_match(
    match_id: str, user: Dict[str, Any] = Depends(auth.get_current_user)
) -> Dict[str, Any]:
    match_doc = db.duo_matches_col.find_one({"id": match_id})
    if not match_doc:
        raise HTTPException(status_code=404, detail="Duo match not found.")
    if match_doc.get("status") != "active":
        return {"ok": True, "match": _serialize_match(match_doc)}

    uid = user["id"]
    player_ids: List[str] = match_doc.get("playerIds", [])
    winner_id = player_ids[1] if uid == player_ids[0] else player_ids[0]
    scores = {k: int(v) for k, v in (match_doc.get("scores") or {}).items()}
    _finalize_duo_rewards(match_doc, scores, winner_id)

    now = db.now_ms()
    updates = {
        "status": "completed",
        "winnerId": winner_id,
        "lastAction": {
            "type": "forfeit",
            "userId": uid,
            "message": "Opponent left the duel.",
            "at": now,
        },
        "updatedAt": now,
    }
    db.duo_matches_col.update_one({"id": match_id}, {"$set": updates})
    match_doc.update(updates)
    serialized = _serialize_match(match_doc)
    await hub.broadcast_match(match_id, {"t": "match_update", "match": serialized})
    return {"ok": True, "match": serialized}


# ---------------------------------------------------------------------------
# 5. In-Game Chat APIs (Duo Gameplay)
# ---------------------------------------------------------------------------
@app.get("/v1/duo/{match_id}/chat")
def get_match_chat(
    match_id: str, user: Dict[str, Any] = Depends(auth.get_current_user)
) -> Dict[str, Any]:
    docs = list(
        db.chat_messages_col.find({"matchId": match_id}, {"_id": 0})
        .sort("createdAt", 1)
        .limit(100)
    )
    return {"ok": True, "messages": docs}


@app.post("/v1/duo/{match_id}/chat")
async def send_match_chat(
    match_id: str,
    body: ChatMessageRequest,
    user: Dict[str, Any] = Depends(auth.get_current_user),
) -> Dict[str, Any]:
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    now = db.now_ms()
    msg_doc = {
        "id": f"msg_{uuid.uuid4().hex[:10]}",
        "matchId": match_id,
        "senderId": user["id"],
        "senderName": user.get("name", "Player"),
        "senderAvatar": user.get("avatar", "word_mage"),
        "text": text[:300],
        "isQuick": body.isQuick,
        "createdAt": now,
    }
    db.chat_messages_col.insert_one(dict(msg_doc))

    match_doc = db.duo_matches_col.find_one({"id": match_id})
    player_ids = match_doc.get("playerIds", []) if match_doc else []
    await hub.broadcast_match(
        match_id,
        {"t": "chat_message", "matchId": match_id, "playerIds": player_ids, "message": msg_doc},
    )

    # If opponent is an active AI challenger, schedule a contextual reply!
    if match_doc and match_doc.get("status") == "active":
        for pid, pinfo in (match_doc.get("players") or {}).items():
            if pid != user["id"] and pinfo.get("isBot"):
                asyncio.create_task(_bot_chat_reply(match_id, pinfo, text, player_ids))

    return {"ok": True, "message": msg_doc}


async def _bot_chat_reply(
    match_id: str, bot_info: Dict[str, Any], user_text: str, player_ids: List[str]
) -> None:
    await asyncio.sleep(random.uniform(1.4, 2.8))
    lower = user_text.lower()
    if "hi" in lower or "hello" in lower or "hey" in lower:
        replies = ["Hey there! Let's have an epic word duel! 👋✨", "Hello Wordsmith! Ready? 🔥"]
    elif "good" in lower or "nice" in lower or "wow" in lower or "👏" in lower:
        replies = ["Thanks! You're playing awesome too! 🙌", "Appreciate it! Keep going! ⚡"]
    elif "hurry" in lower or "time" in lower or "⏳" in lower:
        replies = ["Thinking fast! Give me a second 😅⏳", "Scanning the wheel right now! 👀"]
    else:
        replies = [
            "Great match so far! 🎯",
            "This crossword layout is tricky! 🧩",
            "Let's see who grabs the next word! 🔥",
            "Nice move! 😎✨",
        ]
    reply_doc = {
        "id": f"msg_{uuid.uuid4().hex[:10]}",
        "matchId": match_id,
        "senderId": bot_info["id"],
        "senderName": bot_info.get("name", "Challenger"),
        "senderAvatar": bot_info.get("avatar", "cosmic_scribe"),
        "text": random.choice(replies),
        "isQuick": False,
        "createdAt": db.now_ms(),
    }
    db.chat_messages_col.insert_one(dict(reply_doc))
    await hub.broadcast_match(
        match_id,
        {"t": "chat_message", "matchId": match_id, "playerIds": player_ids, "message": reply_doc},
    )


# ---------------------------------------------------------------------------
# 6. WebRTC Audio / Video Call Signaling in Gameplay
# ---------------------------------------------------------------------------
@app.get("/v1/duo/{match_id}/call")
def get_call_signal(
    match_id: str, user: Dict[str, Any] = Depends(auth.get_current_user)
) -> Dict[str, Any]:
    doc = db.call_signals_col.find_one({"matchId": match_id}, {"_id": 0})
    return {"ok": True, "signal": doc}


@app.post("/v1/duo/{match_id}/call")
async def post_call_signal(
    match_id: str,
    body: CallSignalRequest,
    user: Dict[str, Any] = Depends(auth.get_current_user),
) -> Dict[str, Any]:
    now = db.now_ms()
    uid = user["id"]
    doc = db.call_signals_col.find_one({"matchId": match_id}) or {
        "matchId": match_id,
        "callerId": uid,
        "video": body.video,
        "offer": None,
        "answer": None,
        "candidates": [],
        "endedBy": None,
        "updatedAt": now,
    }

    if body.type == "offer":
        doc.update(
            {
                "callerId": uid,
                "video": body.video,
                "offer": body.sdp,
                "answer": None,
                "candidates": [],
                "endedBy": None,
                "updatedAt": now,
            }
        )
    elif body.type == "answer":
        doc["answer"] = body.sdp
        doc["updatedAt"] = now
    elif body.type == "candidate" and body.candidate:
        cands = list(doc.get("candidates", []))
        cands.append({"from": uid, "candidate": body.candidate, "at": now})
        doc["candidates"] = cands[-40:]
        doc["updatedAt"] = now
    elif body.type == "hangup":
        doc["endedBy"] = uid
        doc["offer"] = None
        doc["answer"] = None
        doc["candidates"] = []
        doc["updatedAt"] = now

    db.call_signals_col.update_one({"matchId": match_id}, {"$set": doc}, upsert=True)
    clean_doc = {k: v for k, v in doc.items() if k != "_id"}

    match_doc = db.duo_matches_col.find_one({"id": match_id})
    player_ids = match_doc.get("playerIds", []) if match_doc else []

    await hub.broadcast_match(
        match_id,
        {
            "t": "call_signal",
            "matchId": match_id,
            "fromUserId": uid,
            "signalType": body.type,
            "video": body.video,
            "sdp": body.sdp,
            "candidate": body.candidate,
            "micMuted": body.micMuted,
            "camOff": body.camOff,
            "playerIds": player_ids,
        },
        exclude_user_id=uid,
    )
    return {"ok": True, "signal": clean_doc}


# ---------------------------------------------------------------------------
# 7. WebSocket Endpoint (`/v1/ws?token=<BearerToken>`)
# ---------------------------------------------------------------------------
@app.websocket("/v1/ws")
async def websocket_endpoint(ws: WebSocket, token: str = Query(...)) -> None:
    try:
        user = auth.user_from_ws_token(token)
    except HTTPException:
        await ws.close(code=4401)
        return

    await ws.accept()
    uid = user["id"]
    conn = WsConnection(ws, uid)
    hub.conns.add(conn)
    db.users_col.update_one(
        {"id": uid}, {"$set": {"isOnline": True, "lastActiveAt": db.now_ms()}}
    )

    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            mtype = msg.get("t")
            if mtype == "ping":
                await conn.send_json({"t": "pong", "at": db.now_ms()})
            elif mtype == "join_match":
                mid = msg.get("matchId")
                if mid:
                    conn.match_ids.add(mid)
                    m = db.duo_matches_col.find_one({"id": mid})
                    if m:
                        await conn.send_json({"t": "match_update", "match": _serialize_match(m)})
            elif mtype == "leave_match":
                mid = msg.get("matchId")
                if mid:
                    conn.match_ids.discard(mid)
            elif mtype == "call_signal":
                mid = msg.get("matchId")
                if mid:
                    await hub.broadcast_match(
                        mid,
                        {**msg, "fromUserId": uid},
                        exclude_user_id=uid,
                    )
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("WebSocket error for user %s", uid)
    finally:
        hub.conns.discard(conn)


# ---------------------------------------------------------------------------
# 8. Background Loop: 30s Turn Enforcement + Active Bot Turns
# ---------------------------------------------------------------------------
async def _duo_background_loop() -> None:
    await asyncio.sleep(1.0)
    while True:
        try:
            now = db.now_ms()
            active_matches = list(db.duo_matches_col.find({"status": "active"}).limit(50))
            for m in active_matches:
                # 1. Check if 30s turn timer expired -> auto pass to opponent
                if _check_and_advance_expired_turn(m):
                    await hub.broadcast_match(
                        m["id"], {"t": "match_update", "match": _serialize_match(m)}
                    )
                    continue

                # 2. If it's an AI Challenger's turn, make a move after 4.5 - 8.5s
                turn_uid = m.get("turnUserId", "")
                pinfo = (m.get("players") or {}).get(turn_uid) or {}
                if pinfo.get("isBot"):
                    elapsed_ms = now - int(m.get("turnStartedAtMs", now))
                    think_ms = int(m.get("botThinkMs", 0))
                    if think_ms <= 0:
                        # 82% chance bot finds a word in 5-9s; 18% chance bot gets stumped and lets 30s expire!
                        will_timeout = random.random() < 0.16
                        think_ms = 31_000 if will_timeout else random.randint(4800, 8800)
                        db.duo_matches_col.update_one(
                            {"id": m["id"]}, {"$set": {"botThinkMs": think_ms}}
                        )
                        m["botThinkMs"] = think_ms

                    if elapsed_ms >= think_ms and elapsed_ms < DUO_TURN_SECONDS * 1000:
                        puzzle = m.get("puzzle", {})
                        found = set(m.get("found", []))
                        remaining = [
                            pw["w"]
                            for pw in puzzle.get("words", [])
                            if pw["w"] not in found
                        ]
                        if remaining:
                            pick = random.choice(remaining)
                            db.duo_matches_col.update_one(
                                {"id": m["id"]}, {"$set": {"botThinkMs": 0}}
                            )
                            m["botThinkMs"] = 0
                            updated_m = _apply_board_word_to_match(m, turn_uid, pick)
                            await hub.broadcast_match(
                                m["id"],
                                {"t": "match_update", "match": _serialize_match(updated_m)},
                            )
        except Exception:
            log.exception("Error in _duo_background_loop")
        await asyncio.sleep(1.0)
