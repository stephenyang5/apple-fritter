# Apple Fritter

Apple Fritter is a Slack bot that automatically pairs people in a channel for 1:1 social introductions! It was built for CSCI0150 at Brown for the TA staff to get to know each other better outside of work. Apple Fritter runs on [Vercel](https://vercel.com) as a serverless Python app (FastAPI + Slack Bolt).

---

## What you'll end up with

Once set up, Fritter will:

1. Run a pairing round in your chosen channel(s) at scheduled intervals.
2. Match channel members into pairs (or trios), skipping anyone who opted out and avoiding recent repeat pairs.
3. Post an announcement in the pairing channel and DM each group with scheduling tips.
4. Respond to slash commands so members can opt in, opt out, check status, or (admins only) trigger a round immediately.

---

## Before you start

You will need:

| Requirement | Why |
|---|---|
| A Slack workspace | Where the bot lives |
| Workspace Admin or Owner access | Required for installing the app |
| A [Vercel](https://vercel.com) account | Free tier only! |
| [Node.js](https://nodejs.org) (for the Vercel CLI) | Used only to deploy — the app itself is Python |

---

## Setup overview

The order matters because Slack needs a live URL to verify your bot:

```
1. Create Slack channels
2. Create the Slack app
3. Deploy this repo to Vercel
4. Add environment variables on Vercel and redeploy
5. Put your cron secret in vercel.json and redeploy
6. Point Slack slash commands at your Vercel URL
7. Test with /fritter-join and /fritter-now
```

---

## Step 1 — Create Slack channels

Create two channels and invite the Fritter bot to both after you install the app in Step 2.

| Channel | Visibility | Purpose |
|---|---|---|
| `#fritter` | Public (or private) | Pairing announcements are posted here |
| `#fritter-storage` | Private | Hidden ledger — stores pairing history and in/out status |

You can use different names; if you do, set `PAIRING_CHANNEL` and `STORAGE_CHANNEL` in Step 4 to match.

> **Important:** The bot must be a member of both channels. After installing the app, open each channel and call `/invite` and install your application

---

## Step 2 — Create the Slack app

### 2a. Create the app

1. Go to [api.slack.com/apps](https://api.slack.com/apps).
2. Click Create New App / From scratch.
3. Name it (e.g. `Apple Fritter`) and select your workspace.

### 2b. Add bot token scopes

Go to OAuth & Permissions / Scopes / Bot Token Scopes and add:

| Scope | Used for |
|---|---|
| `channels:history` | Read pairing history from the storage channel |
| `groups:history` | Same, for private channels |
| `channels:read` | Look up channel IDs by name |
| `groups:read` | Same, for private channels |
| `chat:write` | Post announcements and DMs |
| `im:write` | Open 1:1 DMs |
| `mpim:write` | Open group DMs |
| `users:read` | Filter out bots and deactivated accounts |
| `commands` | Slash commands |

### 2c. Install the app to your workspace

1. Stay in OAuth & Permissions, click Install to Workspace and approve.
2. Copy the Bot User OAuth Token (`xoxb-…`) — you'll need it in Step 4.
3. Go to Basic Information / App Credentials and copy the Signing Secrets — you'll need this too.

### 2d. Create slash commands

Go to Slash Commands / Create New Command and create all four:

| Command | Description | Usage hint |
|---|---|---|
| `/fritter-join` | Opt in to matching | *N/A* |
| `/fritter-leave` | Opt out of matching | *N/A* |
| `/fritter-status` | Check your opt-in status | *N/A* |
| `/fritter-now` | Trigger a round now (admins only) | `[#channel]` |
 
For Request URL on each command, use a placeholder for now — you'll update it in Step 6 after deploy:

```
https://placeholder.vercel.app/api/slack/commands
```

`/fritter-now` accepts an optional channel, e.g. `/fritter-now #general`.
>**Note:** You must actually type `/fritter-now #general` if you want to run the command. `/fritter-now general` will not work. 
---

### 2e. Make a custome Fritter emoji!
Some of the App messages send a `:fritter:` emoji to the slack channel! 
Free slack workspaces support custom emojis:

1. Click the smiley face icon in any message field to open the emoji menu
2. Click Add Emoji (usually at the bottom of the menu).
3. Click Upload Image and select your prepared file.
4. Under "Give it a name," write `:fritter:`.

Make it something fun!!

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

When finished, note your deployment URL, e.g. `https://apple-fritter.vercel.app`.

> **Note:** The app won't start until you add environment variables in Step 4. A 500 error on `/api/health` before then is expected — skip ahead to Step 4.

---

## Step 4 — Set environment variables

In the [Vercel dashboard](https://vercel.com) go to: *Your Project* / Settings / Environment Variables, add:

### Required

| Variable | Value |
|---|---|
| `SLACK_BOT_TOKEN` | Bot token from Step 2c (`xoxb-…`) |
| `SLACK_SIGNING_SECRET` | Signing secret from Step 2c |
| `CRON_SECRET` | Any random string you choose |

### Optional

| Variable | Default | Description |
|---|---|---|
| `PAIRING_CHANNEL` | `#fritter` | Channel where announcements are posted |
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

Then verify the deployment — open `https://<your-vercel-domain>/api/health` in a browser. You should see `{"ok": true}`.

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

The default schedule `0 18 * * 1` means every Monday at 6:00 PM UTC. Change it using [cron syntax](https://crontab.guru) if you want a different day or time (times are always UTC).

Redeploy after editing:

```bash
vercel --prod
```

> **Note:** Vercel Cron sends a **GET** request to that path. The endpoint also accepts POST if you want to trigger it manually with `curl`.

---

## Step 6 — Connect Slack to your Vercel URL

Go back to [api.slack.com/apps](https://api.slack.com/apps) / your app / Slash Commands.

For each of the four commands, set Request URL to:

```
https://<your-vercel-domain>/api/slack/commands
```

Save each command.

### Invite the bot to your channels

If you haven't already:

1. Open `#fritter` and use `/invite` to invite the bot
2. Open `#fritter-storage` and perform the same steps.

---

## Step 7 — Test it

Run these in your Slack workspace:

| Step | Action | Expected result |
|---|---|---|
| 1 | In `#fritter`, run `/fritter-status` | Shows your opt-in status (new members default to IN) |
| 2 | Run `/fritter-leave`, then `/fritter-status` | Status changes to OUT |
| 3 | Run `/fritter-join` | Status back to IN |
| 4 | As a workspace admin, run `/fritter-now` | Bot posts pairings in `#fritter` and DMs each group |

If `/fritter-now` works, you're done. The cron job will run automatically on the schedule you set in `vercel.json`.

---

Persistence: Instead of a database, Fritter writes structured JSON messages to `#fritter-storage`. Each message is tagged with a marker (`FRITTER_HISTORY_V1`, `FRITTER_OPT_V1`, etc.) so the bot can read its own history back.

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

By default, `BIWEEKLY_PARITY=even` means Fritter only runs on even-numbered ISO weeks. The cron job still fires every Monday, but the app skips "off" weeks internally.

| Setting | Behavior |
|---|---|
| `BIWEEKLY_PARITY=even` | Runs on even ISO weeks (weeks 2, 4, 6, …) |
| `BIWEEKLY_PARITY=odd` | Runs on odd ISO weeks (weeks 1, 3, 5, …) |

To pair every Monday instead of every other week, remove or bypass the parity check in `api/slack.py` (the `should_run` block in the `cron_run_round` handler).

---

## API endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/slack/commands` | Slack slash commands |
| `GET` | `/api/health` | Health check — returns `{"ok": true}` |
| `GET` or `POST` | `/api/slack/run_round?secret=…` | Cron / manual pairing trigger (requires `CRON_SECRET`) |

---

## Dependencies

```
fastapi==0.115.0
slack-bolt==1.22.0
python-dotenv==1.0.1
```
