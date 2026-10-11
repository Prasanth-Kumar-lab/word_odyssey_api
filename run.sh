#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
export MONGO_URI="${MONGO_URI:-mongodb://localhost:27017/}"
export MONGODB_DB="${MONGODB_DB:-word_odyssey}"
uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --reload
