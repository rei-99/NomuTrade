# Remote demo access via a Cloudflare quick tunnel

Goal: open the STP platform from a phone/outside network while the stack runs
on a laptop. Verified end to end on 2026-08-04 (macOS): UI, API, WebSocket,
password login — all through a public `*.trycloudflare.com` URL.

How it works: `cloudflared tunnel --url http://localhost:5173` exposes the
**vite dev server** publicly; vite proxies `/api` and `/ws` to the backend on
localhost:8000, so one URL carries the whole app. `allowedHosts:
[".trycloudflare.com"]` in `frontend/vite.config.ts` already permits the
random tunnel hostname (committed — do not remove).

## Windows procedure (Git Bash + PowerShell)

1. **Repo & deps** (once):
   ```bash
   git clone git@github.com:rei-99/NomuTrade.git && cd NomuTrade
   git checkout develop && git pull
   python -m venv backend/.venv
   backend/.venv/Scripts/pip install -r backend/requirements.txt
   cd frontend && npm install && cd ..
   ```
2. **Config** — create `backend/.env` (gitignored, so it never travels):
   ```
   REPLAY_START=2026-08-24
   LLM_PROVIDER=openai
   LLM_API_URL=https://llm-2.echios.tech/v1
   LLM_CHAT_MODEL=claude-haiku-4-5
   LLM_EMBED_MODEL=titan-embed-v2
   DEV_AUTH=false
   ```
   `DEV_AUTH=false` is mandatory for public exposure (it disables the
   passwordless dev-login endpoint).
3. **LLM key** (never in the file): PowerShell —
   `setx LLM_API_KEY "sk-..."` (persistent; open a new terminal after), or
   `$env:LLM_API_KEY="sk-..."` in the session that starts the backend.
4. **Start the stack**: Git Bash from repo root: `./dev.sh`
   (manual: `cd backend && ../backend/.venv/Scripts/python -m uvicorn
   app.main:app --port 8000` + `cd frontend && npm run dev`).
   Sanity: `curl http://localhost:8000/api/v1/health` → `{"status":"ok"}`;
   Governance → `llm` tile should read `live: claude-haiku-4-5`.
5. **Tunnel** (new terminal):
   ```powershell
   winget install Cloudflare.cloudflared    # once; choco also works
   cloudflared tunnel --url http://localhost:5173
   ```
   Read the `https://<random>.trycloudflare.com` URL from the banner. Open it
   on the phone; log in as `trader@demo.nomura` / `demo1234`.

## Verification checklist

- `curl https://<tunnel>/api/v1/health` → `{"status":"ok"}`
- `POST /api/v1/auth/dev-login` through the tunnel → **401** (proves
  DEV_AUTH=false took effect)
- Password login works; tape and chart tick; one MARKET order → settlement
  column walks to SETTLED.

## Notes & teardown

- Quick tunnels are ephemeral (new URL each run) and carry no uptime
  guarantee — fine for demos; for anything lasting use a named tunnel with a
  Cloudflare account.
- Anyone with the URL can reach the instance while the tunnel runs — treat
  the URL as a secret for the day, and Ctrl+C the tunnel when done.
- Teardown: stop cloudflared, then the dev stack; `backend/.env` and the
  `setx` key can stay (file is gitignored; key is per-user env).

## macOS reference (same flow)

`brew install cloudflared`; `export LLM_API_KEY=...` (or `~/.zshrc`);
`./dev.sh`; `cloudflared tunnel --url http://localhost:5173`. Verified:
UI 200, health ok, dev-login 401, password login ok, full workspace
screenshot through the public URL.
