"""MongoDB Compass & Atlas database layer for Word Odyssey.

Creates and initializes the `word_odyssey` database with collections:
  - users           : player profiles, stats, coins, avatar, rank, active presence
  - otps            : mobile number OTP verification codes
  - solo_states     : solo puzzle progress (level, puzzle, found, revealed, bonus)
  - duo_matches     : live & completed 1v1 Duo matches with 30s turn timer
  - match_queue     : active matchmaking queue for online players
  - chat_messages   : in-game real-time chat messages per Duo match
  - call_signals    : WebRTC audio/video signaling (SDP offers/answers & ICE)
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional
from pymongo import MongoClient, ASCENDING, DESCENDING

from .config import MONGO_URI, DB_NAME, STARTING_COINS

log = logging.getLogger("word_odyssey.db")

client = None
db = None
users_col = None
otps_col = None
solo_states_col = None
duo_matches_col = None
match_queue_col = None
chat_messages_col = None
call_signals_col = None

mongo_available = False
db_backend = "none"


def now_ms() -> int:
    return int(time.time() * 1000)


def init_db() -> None:
    global client, db, users_col, otps_col, solo_states_col, duo_matches_col
    global match_queue_col, chat_messages_col, call_signals_col
    global mongo_available, db_backend

    if db is not None:
        return

    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=4000)
        client.admin.command("ping")
        db_backend = "mongodb"
        log.info("Connected to MongoDB (%s) -> database '%s'", MONGO_URI, DB_NAME)
    except Exception as exc:
        log.warning("MongoDB connection failed (%s). Falling back to mongomock.", exc)
        import mongomock

        client = mongomock.MongoClient()
        db_backend = "in-memory"

    db = client[DB_NAME]
    users_col = db["users"]
    otps_col = db["otps"]
    solo_states_col = db["solo_states"]
    duo_matches_col = db["duo_matches"]
    match_queue_col = db["match_queue"]
    chat_messages_col = db["chat_messages"]
    call_signals_col = db["call_signals"]
    mongo_available = True

    try:
        users_col.create_index([("id", ASCENDING)], unique=True)
        users_col.create_index([("phone", ASCENDING)], unique=True)
        users_col.create_index([("lastActiveAt", DESCENDING)])
        users_col.create_index([("trophies", DESCENDING)])
        otps_col.create_index([("phone", ASCENDING)], unique=True)
        solo_states_col.create_index([("userId", ASCENDING)], unique=True)
        duo_matches_col.create_index([("id", ASCENDING)], unique=True)
        duo_matches_col.create_index([("status", ASCENDING)])
        duo_matches_col.create_index([("playerIds", ASCENDING)])
        match_queue_col.create_index([("userId", ASCENDING)], unique=True)
        match_queue_col.create_index([("joinedAt", ASCENDING)])
        chat_messages_col.create_index([("matchId", ASCENDING), ("createdAt", ASCENDING)])
        call_signals_col.create_index([("matchId", ASCENDING)], unique=True)
    except Exception as idx_err:
        log.warning("Index creation warning: %s", idx_err)

    _seed_initial_data()


def _seed_initial_data() -> None:
    """Ensure the `word_odyssey` database and its collections are materialized in
    MongoDB Compass immediately, with starter hall-of-fame profiles if empty."""
    if users_col is None:
        return
    try:
        if users_col.count_documents({}) == 0:
            now = now_ms()
            hall_of_fame = [
                {
                    "id": "u_aria_sage",
                    "phone": "+919000000001",
                    "name": "Aria Wordweaver",
                    "avatar": "cosmic_scribe",
                    "title": "Odyssey Legend",
                    "bio": "Weaving galaxies one syllable at a time.",
                    "level": 18,
                    "coins": 1450,
                    "wordsTotal": 142,
                    "bonusTotal": 39,
                    "duoMatches": 24,
                    "duoWins": 19,
                    "winStreak": 5,
                    "bestStreak": 8,
                    "trophies": 1280,
                    "haptics": True,
                    "soundOn": True,
                    "musicOn": True,
                    "isBot": True,
                    "isOnline": True,
                    "inGameplay": False,
                    "lastActiveAt": now,
                    "createdAt": now - 86400000 * 7,
                },
                {
                    "id": "u_kael_rune",
                    "phone": "+919000000002",
                    "name": "Kael Runeblade",
                    "avatar": "lexicon_knight",
                    "title": "Grand Wordsmith",
                    "bio": "30 seconds is all I need.",
                    "level": 12,
                    "coins": 920,
                    "wordsTotal": 96,
                    "bonusTotal": 27,
                    "duoMatches": 18,
                    "duoWins": 13,
                    "winStreak": 3,
                    "bestStreak": 6,
                    "trophies": 960,
                    "haptics": True,
                    "soundOn": True,
                    "musicOn": True,
                    "isBot": True,
                    "isOnline": True,
                    "inGameplay": False,
                    "lastActiveAt": now,
                    "createdAt": now - 86400000 * 5,
                },
                {
                    "id": "u_lyra_spark",
                    "phone": "+919000000003",
                    "name": "Lyra Phoenix",
                    "avatar": "phoenix_bard",
                    "title": "Lexicon Master",
                    "bio": "Bonus hunter & crossword duelist.",
                    "level": 9,
                    "coins": 680,
                    "wordsTotal": 71,
                    "bonusTotal": 22,
                    "duoMatches": 14,
                    "duoWins": 9,
                    "winStreak": 2,
                    "bestStreak": 4,
                    "trophies": 740,
                    "haptics": True,
                    "soundOn": True,
                    "musicOn": True,
                    "isBot": True,
                    "isOnline": True,
                    "inGameplay": False,
                    "lastActiveAt": now,
                    "createdAt": now - 86400000 * 3,
                },
            ]
            users_col.insert_many(hall_of_fame)
            log.info("Seeded %d initial profiles into %s.users", len(hall_of_fame), DB_NAME)
    except Exception as exc:
        log.warning("Seed warning: %s", exc)


def rank_title_for(level: int, trophies: int) -> str:
    score = level * 40 + trophies
    if score >= 1500:
        return "Odyssey Legend"
    if score >= 1000:
        return "Grand Wordsmith"
    if score >= 650:
        return "Lexicon Master"
    if score >= 350:
        return "Word Weaver"
    if score >= 150:
        return "Rune Scholar"
    return "Novice Scribe"


def user_public(doc: Dict[str, Any]) -> Dict[str, Any]:
    level = int(doc.get("level", 1))
    trophies = int(doc.get("trophies", 100))
    duo_matches = int(doc.get("duoMatches", 0))
    duo_wins = int(doc.get("duoWins", 0))
    win_rate = round((duo_wins / duo_matches) * 100) if duo_matches > 0 else 0
    now = now_ms()
    last_active = int(doc.get("lastActiveAt", 0))
    is_online = bool(doc.get("isBot")) or (now - last_active < 45000)

    return {
        "id": doc["id"],
        "phone": doc.get("phone", ""),
        "name": doc.get("name", "Word Explorer"),
        "avatar": doc.get("avatar", "word_mage"),
        "title": doc.get("title") or rank_title_for(level, trophies),
        "bio": doc.get("bio", "Ready for a Word Odyssey duel!"),
        "level": level,
        "coins": int(doc.get("coins", STARTING_COINS)),
        "wordsTotal": int(doc.get("wordsTotal", 0)),
        "bonusTotal": int(doc.get("bonusTotal", 0)),
        "duoMatches": duo_matches,
        "duoWins": duo_wins,
        "winRate": win_rate,
        "winStreak": int(doc.get("winStreak", 0)),
        "bestStreak": int(doc.get("bestStreak", 0)),
        "trophies": trophies,
        "haptics": bool(doc.get("haptics", True)),
        "soundOn": bool(doc.get("soundOn", True)),
        "musicOn": bool(doc.get("musicOn", True)),
        "isOnline": is_online,
        "inGameplay": bool(doc.get("inGameplay", False)),
        "lastActiveAt": last_active,
        "createdAt": int(doc.get("createdAt", now)),
    }
