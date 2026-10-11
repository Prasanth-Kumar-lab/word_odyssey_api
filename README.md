# Word Odyssey — Python FastAPI + MongoDB Backend

## Features
- **Bearer Token JWT Authentication (`Authorization: Bearer <token>`)**
- **Mobile Number OTP Login** (`POST /v1/auth/send-otp`, `POST /v1/auth/verify-otp`)
- **Animated Game Profile & Leaderboard** (`GET /v1/auth/me`, `PATCH /v1/auth/profile`, `GET /v1/leaderboard`)
- **Dynamic Solo Game Progress in MongoDB** (`GET /v1/solo/state`, `PUT /v1/solo/state`, `POST /v1/solo/new-puzzle`) — replaces local storage completely
- **Active User Presence & Auto-Matchmaking** (`POST /v1/presence/heartbeat`, `GET /v1/presence/active-users`, `POST /v1/duo/auto-match`)
- **Duo 1v1 Multiplayer with 30-Second Turn Timer & Auto-Pass** (`GET /v1/duo/{match_id}`, `POST /v1/duo/{match_id}/move`, `POST /v1/duo/{match_id}/pass-turn`)
- **Real-time In-Game Chat** (`GET /v1/duo/{match_id}/chat`, `POST /v1/duo/{match_id}/chat`)
- **WebRTC Audio & Video Call Signaling** (`GET /v1/duo/{match_id}/call`, `POST /v1/duo/{match_id}/call`, WebSocket `/v1/ws`)

## MongoDB Compass Integration
- **Connection URI**: `mongodb://localhost:27017/`
- **Database Name**: `word_odyssey`
- **Collections**:
  - `users`
  - `otps`
  - `solo_states`
  - `duo_matches`
  - `match_queue`
  - `chat_messages`
  - `call_signals`

## Run Locally
```bash
# Windows
run.bat

# macOS / Linux
./run.sh
```
Interactive Swagger UI docs: `http://localhost:8000/docs`

## Deploy to Render (Live API)
1. Push this repository to GitHub.
2. In Render, create a new **Web Service** (or use Blueprint with `render.yaml`).
   - **Root Directory**: `backend`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
3. Set Environment Variables on Render:
   - `MONGO_URI`: Your MongoDB Atlas connection string (if omitted, automatically falls back to in-memory MongoDB store so the live server never crashes).
   - `MONGODB_DB`: `word_odyssey`
   - `JWT_SECRET`: Any secret string.
4. Pass your live Render URL to Flutter with:
   `flutter run --dart-define=API_BASE_URL=https://your-service.onrender.com`
   (or switch the API server directly inside the in-app Server Settings dialog on the Login screen).
