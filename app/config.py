"""Configuration for Word Odyssey FastAPI Backend."""
import os

# MongoDB Compass local default: mongodb://localhost:27017/
# On Render: set MONGO_URI or MONGODB_URI environment variable to MongoDB Atlas URI.
MONGO_URI = (
    os.getenv("MONGO_URI")
    or os.getenv("MONGODB_URI")
    or "mongodb://localhost:27017/"
).strip()

DB_NAME = os.getenv("MONGODB_DB", "word_odyssey").strip()

# Bearer JWT Configuration
JWT_SECRET = os.getenv("JWT_SECRET", "word-odyssey-jwt-secret-key-2026-super-secure")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_DAYS = 30

# Duo Match Turn Timer (seconds)
DUO_TURN_SECONDS = 30
STARTING_COINS = 250
