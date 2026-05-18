# Fritter

Fritter is a Slack bot that was developed for CSCI0150 at Brown to automatically pairs people in a slack channel for casual 1:1 social introductions! It runs on [Vercel](https://vercel.com) as a serverless Python app (FastAPI + Slack Bolt) and uses a private Slack channel as its only persistence layer.

## How it works

1. Every Monday at 6 PM UTC, Vercel Cron calls `/api/slack/run_round`.
2. Fritter fetches all members of the configured pairing channel(s) and removes anyone who has opted out.
3. It builds pairs (or trios), avoiding people who were matched together recently.
4. It posts an announcement to the channel and sends each group a DM with scheduling tips.
5. The pairing history and opt-in/out state are stored as JSON messages in a private `#fritter-storage` channel.

Biweekly scheduling is supported: set `BIWEEKLY_PARITY` to `even` or `odd` to skip alternating ISO weeks.

---

## Requirements

- A Slack workspace where you have Admin or Owner access
- A [Vercel](https://vercel.com) account (free tier is just fine)

---

## Step 1 — Create the Slack app

1. Go to [api.slack.com/apps](https://api.slack.com/apps) and click Create New App -> From scratch.
2. Name it! (e.g. `Fritter`) and choose your workspace.

### OAuth scopes

Under OAuth & Permissions -> Scopes -> Bot Token Scopes, add:

| Scope | Why |
|---|---|
| `channels:history` | Read public storage channel messages |
| `groups:history` | Read private storage channel messages |
| `channels:read` | Resolve channel names to IDs |
| `groups:read` | Resolve private channel names to IDs |
| `chat:write` | Post announcements and DMs |
| `im:write` | Open 1:1 DMs |
| `mpim:write` | Open group DMs |
| `users:read` | Filter out bots and deactivated accounts |
| `commands` | Register slash commands |
| `conversations.members:read` / `mpim:read` | List channel members |

> Slack also requires `conversations:members` (covered by `channels:read`/`groups:read`) to call `conversations.members`.

### Install the app

Under OAuth & Permissions, click Install to Workspace. After approval, copy the Bot User OAuth Token (`xoxb-…`) for later!

### Signing secret

Under Basic Information -> App Credentials, copy the Signing Secret.

### Slash commands

Under Slash Commands, create four commands. Set the Request URL for each to:

```
https://<your-vercel-domain>/api/slack/commands
```

| Command | Short description |
|---|---|
| `/fritter-join` | Opt in to Fritter matching |
| `/fritter-leave` | Opt out of Fritter matching |
| `/fritter-status` | Check your current opt status |
| `/fritter-now` | (Admins only) Trigger a round immediately |

`/fritter-now` accepts an optional channel argument, e.g. `/fritter-now #general`.

### Event subscriptions

Under Event Subscriptions, enable events and set the Request URL to:

```
https://<your-vercel-domain>/api/slack/events
```

Slack will send a verification challenge; Fritter handles it automatically once deployed.

---

## Step 2 — Create the Slack channels

Create two channels in your workspace and invite the Fritter bot to both:

| Channel | Purpose |
|---|---|
| `#fritters` | Where pairing announcements are posted |
| `#fritter-storage` (or your preferred name) | Private ledger — stores pairing history and opt state |

Make `#fritter-storage` private so only the bot and admins can see it. The channel name must match the `PAIRING_CHANNEL` and `STORAGE_CHANNEL` environment variables.

---

## Step 3 — Deploy to Vercel

### 3a. Fork or clone this repo

```bash
git clone <your-fork-url>
cd fritter-vercel
```

### 3b. Install the Vercel CLI and deploy

```bash
npm i -g vercel
vercel
```

Follow the prompts. Vercel will detect the Python project and deploy it.

### 3c. Set environment variables

In the Vercel dashboard under **Settings → Environment Variables**, add:

| Variable | Required | Default | Description |
|---|---|---|---|
| `SLACK_BOT_TOKEN` | Yes | — | Bot User OAuth Token (`xoxb-…`) |
| `SLACK_SIGNING_SECRET` | Yes | — | App signing secret from Basic Information |
| `CRON_SECRET` | Yes | — | Secret token used to authenticate the cron endpoint (choose any strong random string) |
| `PAIRING_CHANNEL` | No | `#fritters` | Channel where pairing announcements are posted |
| `PAIRING_CHANNELS` | No | — | Comma-separated list of channels to run simultaneously (overrides `PAIRING_CHANNEL` when set) |
| `STORAGE_CHANNEL` | No | `#fritter-storage` | Private channel used as a persistence ledger |
| `NO_REPEAT_WEEKS` | No | `8` | Number of weeks before the same two people can be matched again |
| `NO_REPEAT_MODE` | No | `weeks` | `weeks` (use `NO_REPEAT_WEEKS`) or `ever` (never re-pair the same two people) |
| `GROUP_SIZE` | No | `2` | `2` for pairs, `3` for trios |
| `TIMEZONE` | No | `America/New_York` | Timezone used for display timestamps |
| `BIWEEKLY_PARITY` | No | `even` | `even` or `odd` — only run on ISO weeks with this parity; omit / set to `even` for every-week runs (see [Biweekly scheduling](#biweekly-scheduling)) |

### 3d. Update the cron secret in vercel.json

Open `vercel.json` and replace `secret-password` with the same value you set for `CRON_SECRET`:

```json
{
  "crons": [
    {
      "path": "/api/slack/run_round?secret=YOUR_CRON_SECRET",
      "schedule": "0 18 * * 1"
    }
  ]
}
```

Redeploy after saving:

```bash
vercel --prod
```

---

## Step 4 — Wire Slack to your Vercel URL

Once deployed, Vercel gives you a production URL like `https://fritter-vercel.vercel.app`.

1. Slash commands — go back to each slash command in the Slack app settings and confirm the Request URL is `https://<your-domain>/api/slack/commands`.
2. Event subscriptions — confirm the Request URL is `https://<your-domain>/api/slack/events` and save. Slack will verify it immediately.

Your bot is now live. Run `/fritter-join` in your pairing channel to test opt-in, then use `/fritter-now` (as a workspace admin) to trigger a manual round.

---

## Biweekly scheduling

The cron job fires every Monday (`0 18 * * 1`) but Fritter checks the ISO week number before running:

- `BIWEEKLY_PARITY=even` → runs only on even-numbered ISO weeks
- `BIWEEKLY_PARITY=odd` → runs only on odd-numbered ISO weeks

To run **every week**, keep `BIWEEKLY_PARITY=even` and adjust the cron schedule so it fires on both even and odd weeks (the default `0 18 * * 1` already fires weekly — Fritter just skips the off-week internally).

To change the day or time, edit the `schedule` in `vercel.json` using standard cron syntax (UTC).

---

## Slash command reference

| Command | Who can use | What it does |
|---|---|---|
| `/fritter-join` | Anyone | Opt in — you will be included in future rounds |
| `/fritter-leave` | Anyone | Opt out — you will be skipped until you rejoin |
| `/fritter-status` | Anyone | Shows whether you are currently opted in or out |
| `/fritter-now [#channel]` | Workspace admins/owners only | Triggers an immediate pairing round; optionally specify a different channel |

New channel members are **opted in by default**. They must run `/fritter-leave` to opt out.

---

## API endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/slack/events` | Slack event subscriptions handler |
| `POST` | `/api/slack/commands` | Slack slash commands handler |
| `GET/POST` | `/api/slack/run_round?secret=…` | Cron trigger — runs pairing round(s) |
| `GET` | `/api/health` | Health check — returns `{"ok": true}` |

---

## Local development

### 1. Install dependencies

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Create a `.env` file

```bash
SLACK_BOT_TOKEN=xoxb-...
SLACK_SIGNING_SECRET=...
CRON_SECRET=dev-secret
PAIRING_CHANNEL=#fritters
STORAGE_CHANNEL=#fritter-storage
```

### 3. Start the server

```bash
uvicorn main:app --reload --port 3000
```

### 4. Expose it to Slack via ngrok

```bash
ngrok http 3000
```

Copy the `https://…ngrok-free.app` URL and use it as the base URL for your slash command and event subscription request URLs in the Slack app settings while developing.

---

## Project structure

```
fritter-vercel/
├── api/
│   ├── slack.py            # Core bot logic (FastAPI + Slack Bolt)
│   └── slack/
│       ├── commands.py     # Vercel file-based handler for slash commands (fallback)
│       ├── events.py       # Vercel file-based handler for events (fallback)
│       └── run_round.py    # Vercel file-based handler for the cron endpoint
├── main.py                 # Vercel entrypoint — re-exports the FastAPI app
├── requirements.txt
└── vercel.json             # Cron job definition
```

## Dependencies

```
fastapi==0.115.0
slack-bolt==1.22.0
python-dotenv==1.0.1
pytz==2024.1
```
