# Apple Fritter

Apple Fritter is a Slack bot that automatically pairs people in a channel for casual 1:1 social introductions. It was built for CSCI0150 at Brown and runs on [Vercel](https://vercel.com) as a serverless Python app (FastAPI + Slack Bolt).

**No database required.** Pairing history and opt-in/out state are stored as JSON messages in a private Slack channel that only the bot can write to.

---

## What you'll end up with

Once set up, Fritter will:

1. **Every Monday at 6 PM UTC** (configurable), run a pairing round in your chosen channel(s).
2. **Match channel members** into pairs (or trios), skipping anyone who opted out and avoiding recent repeat pairs.
3. **Post an announcement** in the pairing channel and **DM each group** with scheduling tips.
4. Respond to **slash commands** so members can opt in, opt out, check status, or (admins only) trigger a round immediately.

---

## Before you start

You will need:

| Requirement | Why |
|---|---|
| A Slack workspace | Where the bot lives |
| **Workspace Admin or Owner** access | Required for `/fritter-now` and installing the app |
| A [Vercel](https://vercel.com) account | Free tier works |
| [Node.js](https://nodejs.org) (for the Vercel CLI) | Used only to deploy — the app itself is Python |
| [Python 3](https://www.python.org) (optional) | Only if you want to run locally |

**Time estimate:** ~30 minutes for a first-time setup.

---

## Setup overview

The order matters because Slack needs a live URL to verify your bot:

```
1. Create Slack channels
2. Create the Slack app (save tokens; use placeholder URLs for now)
3. Deploy this repo to Vercel
4. Add environment variables on Vercel
5. Put your cron secret in vercel.json and redeploy
6. Point Slack slash commands at your Vercel URL
7. Test with /fritter-join and /fritter-now
```

---

## Step 1 — Create Slack channels

Create two channels and **invite the Fritter bot** to both after you install the app in Step 2.

| Channel | Visibility | Purpose |
|---|---|---|
| `#fritters` | Public (or private) | Pairing announcements are posted here |
| `#fritter-storage` | **Private** | Hidden ledger — stores pairing history and opt-in/out state |

You can use different names; if you do, set `PAIRING_CHANNEL` and `STORAGE_CHANNEL` in Step 4 to match.

> **Important:** The bot must be a member of both channels. After installing the app, open each channel → Integrations → Add apps → add Fritter.

---

## Step 2 — Create the Slack app

### 2a. Create the app

1. Go to [api.slack.com/apps](https://api.slack.com/apps).
2. Click **Create New App → From scratch**.
3. Name it (e.g. `Fritter`) and select your workspace.

### 2b. Add bot token scopes

Go to **OAuth & Permissions → Scopes → Bot Token Scopes** and add:

| Scope | Used for |
|---|---|
| `channels:history` | Read pairing history from the storage channel |
| `groups:history` | Same, for private channels |
| `channels:read` | Look up channel IDs by name |
| `groups:read` | Same, for private channels |
| `chat:write` | Post announcements and DMs |
| `im:write` | Open 1:1 DMs |
| `mpim:write` | Open group DMs (trios) |
| `users:read` | Filter out bots and deactivated accounts |
| `commands` | Slash commands |

### 2c. Install the app to your workspace

1. Still on **OAuth & Permissions**, click **Install to Workspace** and approve.
2. Copy the **Bot User OAuth Token** (`xoxb-…`) — you'll need it in Step 4.
3. Go to **Basic Information → App Credentials** and copy the **Signing Secret** — you'll need this too.

### 2d. Create slash commands

Go to **Slash Commands → Create New Command** and create all four:

| Command | Description | Usage hint |
|---|---|---|
| `/fritter-join` | Opt in to matching | *(leave blank)* |
| `/fritter-leave` | Opt out of matching | *(leave blank)* |
| `/fritter-status` | Check your opt-in status | *(leave blank)* |
| `/fritter-now` | Trigger a round now (admins only) | `[#channel]` |

For **Request URL** on each command, use a placeholder for now — you'll update it in Step 6 after deploy:

```
https://placeholder.vercel.app/api/slack/commands
```

`/fritter-now` accepts an optional channel, e.g. `/fritter-now #general`.

---

## Step 3 — Deploy to Vercel

### 3a. Clone this repository

```bash
git clone https://github.com/stephenyang5/apple-fritter.git
cd fritter-vercel
```

### 3b. Install the Vercel CLI and deploy

```bash
npm i -g vercel
vercel
```

Follow the prompts (link your Vercel account, confirm project settings). Vercel detects the Python app via `main.py` and deploys it.

When finished, note your deployment URL, e.g. `https://fritter-vercel.vercel.app`.

### 3c. Verify the deployment

Open in a browser:

```
https://<your-vercel-domain>/api/health
```

You should see:

```json
{"ok": true}
```

If that works, your app is running.

---

## Step 4 — Set environment variables

In the [Vercel dashboard](https://vercel.com) → your project → **Settings → Environment Variables**, add:

### Required

| Variable | Value |
|---|---|
| `SLACK_BOT_TOKEN` | Bot token from Step 2c (`xoxb-…`) |
| `SLACK_SIGNING_SECRET` | Signing secret from Step 2c |
| `CRON_SECRET` | Any long random string you choose (e.g. run `openssl rand -hex 32`) |

### Optional

| Variable | Default | Description |
|---|---|---|
| `PAIRING_CHANNEL` | `#fritters` | Channel where announcements are posted |
| `PAIRING_CHANNELS` | — | Comma-separated list to run multiple channels at once (overrides `PAIRING_CHANNEL`) |
| `STORAGE_CHANNEL` | `#fritter-storage` | Private channel used as the data store |
| `NO_REPEAT_WEEKS` | `8` | Weeks before the same two people can be re-paired |
| `NO_REPEAT_MODE` | `weeks` | `weeks` (honor `NO_REPEAT_WEEKS`) or `ever` (never re-pair the same two people) |
| `GROUP_SIZE` | `2` | `2` for pairs, `3` for trios |
| `BIWEEKLY_PARITY` | `even` | `even` or `odd` — see [Biweekly scheduling](#biweekly-scheduling) |

After adding variables, redeploy so they take effect:

```bash
vercel --prod
```

---

## Step 5 — Configure the cron job

Fritter uses [Vercel Cron](https://vercel.com/docs/cron-jobs) to trigger pairing rounds automatically.

Open `vercel.json` and replace the `secret=...` placeholder with the same value you set for `CRON_SECRET`:

```json
{
  "crons": [
    {
      "path": "/api/slack/run_round?secret=YOUR_CRON_SECRET_HERE",
      "schedule": "0 18 * * 1"
    }
  ]
}
```

The default schedule `0 18 * * 1` means **every Monday at 6:00 PM UTC**. Change it using [cron syntax](https://crontab.guru) if you want a different day or time (times are always UTC).

Redeploy after editing:

```bash
vercel --prod
```

> **Note:** Vercel Cron sends a **GET** request to that path. The endpoint also accepts POST if you want to trigger it manually with `curl`.

---

## Step 6 — Connect Slack to your Vercel URL

Go back to [api.slack.com/apps](https://api.slack.com/apps) → your app → **Slash Commands**.

For **each** of the four commands, set Request URL to:

```
https://<your-vercel-domain>/api/slack/commands
```

Save each command.

### Invite the bot to your channels

If you haven't already:

1. Open `#fritters` → **Integrations** → **Add apps** → add Fritter.
2. Open `#fritter-storage` → same steps.

---

## Step 7 — Test it

Run these in your Slack workspace:

| Step | Action | Expected result |
|---|---|---|
| 1 | In `#fritters`, run `/fritter-status` | Shows your opt-in status (new members default to **IN**) |
| 2 | Run `/fritter-leave`, then `/fritter-status` | Status changes to **OUT** |
| 3 | Run `/fritter-join` | Status back to **IN** |
| 4 | As a workspace admin, run `/fritter-now` | Bot posts pairings in `#fritters` and DMs each group |

If `/fritter-now` works, you're done. The cron job will run automatically on the schedule you set in `vercel.json`.

---

## How it works (under the hood)

```
Monday 6 PM UTC
      │
      ▼
Vercel Cron ──GET──▶ /api/slack/run_round?secret=…
      │
      ▼
Fetch members of pairing channel(s)
      │
      ▼
Remove opted-out users (via /fritter-leave)
      │
      ▼
Build pairs/trios (avoid recent matches)
      │
      ▼
Post announcement in #fritters + DM each group
      │
      ▼
Save pairing record to #fritter-storage
```

**Opt-in model:** Everyone in the pairing channel is included by default. Members run `/fritter-leave` to opt out and `/fritter-join` to opt back in.

**Persistence:** Instead of a database, Fritter writes structured JSON messages to `#fritter-storage`. Each message is tagged with a marker (`FRITTER_HISTORY_V1`, `FRITTER_OPT_V1`, etc.) so the bot can read its own history back.

---

## Slash command reference

| Command | Who can use | What it does |
|---|---|---|
| `/fritter-join` | Anyone | Opt in — included in future rounds |
| `/fritter-leave` | Anyone | Opt out — skipped until you rejoin |
| `/fritter-status` | Anyone | Shows current opt-in status |
| `/fritter-now [#channel]` | Workspace admins/owners only | Runs a pairing round immediately; optional channel argument |

---

## Biweekly scheduling

By default, `BIWEEKLY_PARITY=even` means Fritter **only runs on even-numbered ISO weeks**. The cron job still fires every Monday, but the app skips "off" weeks internally.

| Setting | Behavior |
|---|---|
| `BIWEEKLY_PARITY=even` | Runs on even ISO weeks (weeks 2, 4, 6, …) |
| `BIWEEKLY_PARITY=odd` | Runs on odd ISO weeks (weeks 1, 3, 5, …) |

To pair **every** Monday instead of every other week, remove or bypass the parity check in `api/slack.py` (the `should_run` block in the `cron_run_round` handler).

---

## API endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/slack/commands` | Slack slash commands |
| `GET` | `/api/health` | Health check — returns `{"ok": true}` |
| `GET` or `POST` | `/api/slack/run_round?secret=…` | Cron / manual pairing trigger (requires `CRON_SECRET`) |

---

## Local development

Useful for testing changes before deploying.

### 1. Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install uvicorn         # not bundled in requirements.txt
```

### 2. Create a `.env` file in the project root

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

Confirm: [http://localhost:3000/api/health](http://localhost:3000/api/health)

### 4. Expose localhost to Slack with ngrok

Slack can't reach `localhost` directly. Install [ngrok](https://ngrok.com) and run:

```bash
ngrok http 3000
```

Copy the `https://….ngrok-free.app` URL and temporarily set your Slack slash command Request URLs to:

```
https://<your-ngrok-url>/api/slack/commands
```

Remember to switch them back to your Vercel URL when done.

---

## Project structure

```
fritter-vercel/
├── api/
│   └── slack.py        # All bot logic, pairing algorithm, and HTTP routes
├── main.py             # Vercel entrypoint — exports the FastAPI app
├── requirements.txt    # Python dependencies
├── vercel.json         # Cron schedule
└── README.md
```

---

## Troubleshooting

| Problem | Likely cause | Fix |
|---|---|---|
| `/fritter-join` returns nothing or times out | Slash command URL wrong or app not deployed | Confirm Request URL is `https://<domain>/api/slack/commands` and `/api/health` works |
| `dispatch_failed` in Slack | Server error or bad signing secret | Check Vercel function logs; verify `SLACK_SIGNING_SECRET` |
| Bot can't find `#fritter-storage` | Bot not invited to the channel | Add Fritter to the private storage channel |
| Pairing runs but skips everyone | All members opted out | Run `/fritter-status`; have people run `/fritter-join` |
| Cron never runs | Wrong `CRON_SECRET` in `vercel.json` | Secret in the URL must exactly match the `CRON_SECRET` env var |
| Cron runs but pairing skipped | Biweekly parity | Check Vercel logs for `"ran": false`; see [Biweekly scheduling](#biweekly-scheduling) |
| `/fritter-now` says admins only | Not a workspace admin | Only admins/owners can trigger manual rounds |

**View logs:** Vercel dashboard → your project → **Logs** (or run `vercel logs` in the CLI).

---

## Dependencies

```
fastapi==0.115.0
slack-bolt==1.22.0
python-dotenv==1.0.1
```
