# React UI Build + Manual Testing Guide

This guide is for manually testing the new React UI (`web/`) against the current backend.

## 1. Prerequisites

- Node.js 20+ and npm
- Python 3.11+ (project backend)
- Repo path: `/Users/mrdnd/src/agentchattr`

## 2. Install Frontend Dependencies (one-time)

```bash
cd /Users/mrdnd/src/agentchattr/web
npm install
```

## 3. Run Backend (port 8300)

From project root:

```bash
cd /Users/mrdnd/src/agentchattr
python run.py
```

Expected:
- Backend serves at `http://localhost:8300`
- WebSocket endpoint available at `/ws`

## 4. Option A: Dev Manual Testing (recommended)

In another terminal:

```bash
cd /Users/mrdnd/src/agentchattr/web
npm run dev
```

Expected:
- Vite UI at `http://localhost:8888`
- API/WS proxied to backend (`localhost:8300`) via `vite.config.ts`

Use this mode for fast manual verification while developing.

## 5. Option B: Build Artifact Testing

Build the production bundle:

```bash
cd /Users/mrdnd/src/agentchattr/web
npm run build
```

Expected output:
- `web/dist/index.html`
- `web/dist/assets/*`

Quick local preview of built bundle:

```bash
cd /Users/mrdnd/src/agentchattr/web
npm run preview
```

Then open the preview URL shown in terminal (usually `http://localhost:4173`).

## 6. Manual Testing Smoke Checklist

Run these checks in browser:

1. Connect and load messages/channels without console errors.
2. Send message (`Enter`) and multiline (`Shift+Enter`).
3. Mention autocomplete (`@`) works with keyboard and mouse.
4. Markdown render/copy/timestamp in message items.
5. Channel actions (switch/create/rename/delete if role allows).
6. Jobs panel + job thread load and update.
7. Rules panel + settings save (`font`, `contrast`, `history_limit`, `username`).
8. Mobile layout:
   - sidebar hamburger
   - overlay panels
   - touch target sizes >= 44px for main controls
9. Agent avatars/hats/presence status display.
10. Reconnect behavior after backend restart (WS reconnect + state restore).

## 7. Troubleshooting

- Port conflict on `8300`: stop old backend process, then rerun `python run.py`.
- Port conflict on `8888`: free the port, or run `npm run dev -- --port <new_port>`.
- Blank UI in dev mode: confirm backend is running and `vite.config.ts` proxy targets `localhost:8300`.
- Build errors: run `npm run build` and fix reported TypeScript/Vite errors before manual test.

## 8. Notes

- New UI dev server: `http://localhost:8888`
- Legacy UI/backend-served UI: `http://localhost:8300`
- Use side-by-side browser windows to compare old vs new behavior during manual QA.

