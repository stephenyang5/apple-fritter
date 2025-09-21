# api/slack.py
import os
import json
import random
import datetime as dt
from typing import List, Tuple, Optional, Set

from fastapi import FastAPI, Request, HTTPException
from dotenv import load_dotenv

from slack_bolt import App
from slack_bolt.adapter.fastapi import SlackRequestHandler
from slack_sdk.errors import SlackApiError

import pytz

# ------------------ Config ------------------
load_dotenv()

SLACK_BOT_TOKEN      = os.getenv("SLACK_BOT_TOKEN")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET")

# Target pairing channel(s): "#name" or channel ID; multiple via comma-separated PAIRING_CHANNELS
PAIRING_CHANNEL   = os.getenv("PAIRING_CHANNEL", "#coffee-intros")
PAIRING_CHANNELS  = os.getenv("PAIRING_CHANNELS")  # optional, comma-separated

NO_REPEAT_WEEKS   = int(os.getenv("NO_REPEAT_WEEKS", "8"))
GROUP_SIZE        = int(os.getenv("GROUP_SIZE", "2"))  # 2 or 3
TIMEZONE          = os.getenv("TIMEZONE", "America/New_York")
NO_REPEAT_MODE    = os.getenv("NO_REPEAT_MODE", "weeks").lower()  # "weeks" or "ever"

# Slack channel used as a tiny "ledger" for persistence (must invite the bot)
STORAGE_CHANNEL   = os.getenv("STORAGE_CHANNEL", "#fritter-storage")

# Cron auth: the secret must come via query param ?secret=...
CRON_SECRET       = os.getenv("CRON_SECRET")

# Biweekly controls: run on "even" or "odd" ISO weeks (default: "even")
BIWEEKLY_PARITY   = (os.getenv("BIWEEKLY_PARITY", "even").strip().lower() or "even")

tz = pytz.timezone(TIMEZONE)

# ------------------ Slack App (HTTP mode for Vercel) ------------------
bolt_app = App(token=SLACK_BOT_TOKEN, signing_secret=SLACK_SIGNING_SECRET)
api = FastAPI()
handler = SlackRequestHandler(bolt_app)

# ------------------ Slack-backed Storage ------------------
PAIR_MARKER = "FRITTER_HISTORY_V1"
OPT_MARKER  = "FRITTER_OPT_V1"
META_MARKER = "FRITTER_META_V1"  # tracks last-run week

def _now_utc_iso() -> str:
    return dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc).isoformat()

def _norm(a: str, b: str) -> Tuple[str, str]:
    return (a, b) if a <= b else (b, a)

def _resolve_channel_id(client, name_or_id: str) -> Optional[str]:
    if not name_or_id or not name_or_id.startswith("#"):
        return name_or_id
    cursor = None
    try:
        while True:
            res = client.conversations_list(
                limit=1000,
                cursor=cursor,
                exclude_archived=True,
                types="public_channel,private_channel",
            )
            for ch in res["channels"]:
                if f"#{ch['name']}" == name_or_id:
                    return ch["id"]
            cursor = res.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
    except SlackApiError:
        return None
    return None

def _storage_channel_id(client) -> str:
    ch = _resolve_channel_id(client, STORAGE_CHANNEL)
    if not ch:
        raise RuntimeError(f"Storage channel {STORAGE_CHANNEL} not found or the bot isn't a member.")
    return ch

# ---------- Pair history ----------
def storage_record_pairs(client, channel_id: str, groups: List[List[str]]) -> None:
    pairs = []
    for g in groups:
        for i in range(len(g)):
            for j in range(i + 1, len(g)):
                a, b = _norm(g[i], g[j])
                pairs.append({"a": a, "b": b})
    if not pairs:
        return
    payload = {
        "marker": PAIR_MARKER,
        "channel": channel_id,
        "paired_at": _now_utc_iso(),
        "pairs": pairs,
    }
    scid = _storage_channel_id(client)
    text = f"{PAIR_MARKER} ```{json.dumps(payload, separators=(',', ':'))}```"
    client.chat_postMessage(channel=scid, text=text)

def storage_get_recent_pairs(client, channel_id: str, since: dt.datetime) -> Set[Tuple[str, str]]:
    scid = _storage_channel_id(client)
    oldest = str(since.timestamp())
    out: Set[Tuple[str, str]] = set()
    cursor = None
    while True:
        res = client.conversations_history(channel=scid, cursor=cursor, limit=200, oldest=oldest)
        for msg in res.get("messages", []):
            txt = msg.get("text", "")
            if PAIR_MARKER not in txt:
                continue
            try:
                blob = txt.split("```", 1)[1].rsplit("```", 1)[0]
                data = json.loads(blob)
            except Exception:
                continue
            if data.get("marker") != PAIR_MARKER or data.get("channel") != channel_id:
                continue
            for p in data.get("pairs", []):
                out.add(_norm(p["a"], p["b"]))
        cursor = res.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    return out

# ---------- Opt-in/out ----------
def storage_set_opt_in(client, user_id: str, is_in: bool) -> None:
    payload = {
        "marker": OPT_MARKER,
        "when": _now_utc_iso(),
        "user": user_id,
        "is_in": bool(is_in),
    }
    scid = _storage_channel_id(client)
    text = f"{OPT_MARKER} ```{json.dumps(payload, separators=(',', ':'))}```"
    client.chat_postMessage(channel=scid, text=text)

def storage_get_opted_out(client) -> Set[str]:
    scid = _storage_channel_id(client)
    cursor = None
    state = {}  # user_id -> is_in
    while True:
        res = client.conversations_history(channel=scid, cursor=cursor, limit=200)
        for msg in res.get("messages", []):
            txt = msg.get("text", "")
            if OPT_MARKER not in txt:
                continue
            try:
                blob = txt.split("```", 1)[1].rsplit("```", 1)[0]
                data = json.loads(blob)
            except Exception:
                continue
            if data.get("marker") != OPT_MARKER:
                continue
            u = data.get("user")
            is_in = bool(data.get("is_in", True))
            state[u] = is_in  # later messages overwrite earlier ones while scanning
        cursor = res.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    # default is IN if no record; opted-out are those explicitly False
    return {u for u, is_in in state.items() if not is_in}

# ---------- Meta: last-run week for biweekly cadence ----------
def storage_record_last_week(client, week: int) -> None:
    payload = {"marker": META_MARKER, "week": int(week), "when": _now_utc_iso()}
    scid = _storage_channel_id(client)
    text = f"{META_MARKER} ```{json.dumps(payload, separators=(',', ':'))}```"
    client.chat_postMessage(channel=scid, text=text)

def storage_get_last_week(client) -> Optional[int]:
    scid = _storage_channel_id(client)
    cursor = None
    while True:
        res = client.conversations_history(channel=scid, cursor=cursor, limit=200)
        for msg in res.get("messages", []):
            txt = msg.get("text", "")
            if META_MARKER not in txt:
                continue
            try:
                blob = txt.split("```", 1)[1].rsplit("```", 1)[0]
                data = json.loads(blob)
            except Exception:
                continue
            if data.get("marker") == META_MARKER:
                try:
                    return int(data.get("week"))
                except (TypeError, ValueError):
                    continue
        cursor = res.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    return None

# ------------------ Slack helper ops ------------------
def get_channel_members(client, channel_id: str) -> List[str]:
    members: List[str] = []
    cursor = None
    while True:
        resp = client.conversations_members(channel=channel_id, cursor=cursor, limit=200)
        members.extend(resp["members"])
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    # filter to humans
    humans: List[str] = []
    for uid in members:
        try:
            info = client.users_info(user=uid)
            if not info["user"]["is_bot"] and not info["user"]["deleted"]:
                humans.append(uid)
        except SlackApiError:
            continue
    return humans

def is_workspace_admin_or_owner(client, user_id: str) -> bool:
    try:
        user = client.users_info(user=user_id)["user"]
        return bool(user.get("is_admin") or user.get("is_owner") or user.get("is_primary_owner"))
    except SlackApiError:
        return False

# ------------------ Pairing logic ------------------
def recently_paired(client, a: str, b: str, channel: str, no_repeat_weeks: int) -> bool:
    if NO_REPEAT_MODE == "ever":
        cutoff = dt.datetime(1970, 1, 1)
    else:
        cutoff = dt.datetime.utcnow() - dt.timedelta(weeks=no_repeat_weeks)
    recent = storage_get_recent_pairs(client, channel, cutoff)
    return _norm(a, b) in recent

def make_groups(client, users: List[str], channel: str, group_size: int, no_repeat_weeks: int) -> List[List[str]]:
    users = users[:]
    random.shuffle(users)
    groups: List[List[str]] = []
    if len(users) < 2:
        return groups

    if group_size == 2:
        temp: List[List[str]] = []
        while len(users) >= 2:
            a = users.pop()
            partner_idx = None
            for i, b in enumerate(users):
                if not recently_paired(client, a, b, channel, no_repeat_weeks):
                    partner_idx = i
                    break
            if partner_idx is None:
                partner_idx = len(users) - 1
            b = users.pop(partner_idx)
            temp.append([a, b])
        if users:
            # leftover → make a trio with the pair that has least overlap with leftover
            leftover = users.pop()
            best_idx = 0
            best_score = 10**9
            if NO_REPEAT_MODE == "ever":
                cutoff = dt.datetime(1970, 1, 1)
            else:
                cutoff = dt.datetime.utcnow() - dt.timedelta(weeks=no_repeat_weeks)
            hist = storage_get_recent_pairs(client, channel, cutoff)
            for i, (x, y) in enumerate(temp):
                recency = 0
                if _norm(x, leftover) in hist:
                    recency += 1
                if _norm(y, leftover) in hist:
                    recency += 1
                if recency < best_score:
                    best_score = recency
                    best_idx = i
            temp[best_idx].append(leftover)
        groups = temp
    else:
        # group_size == 3
        while len(users) >= 3:
            a, b, c = users.pop(), users.pop(), users.pop()
            tries = 0
            while (
                recently_paired(client, a, b, channel, no_repeat_weeks)
                or recently_paired(client, a, c, channel, no_repeat_weeks)
                or recently_paired(client, b, c, channel, no_repeat_weeks)
            ) and tries < 5 and len(users) >= 3:
                users.extend([a, b, c])
                random.shuffle(users)
                a, b, c = users.pop(), users.pop(), users.pop()
                tries += 1
            groups.append([a, b, c])
        if users:
            for u in users:
                best = min(range(len(groups)), key=lambda i: len(groups[i]))
                groups[best].append(u)
    return groups

def post_groups_and_record(client, channel_id: str, groups: List[List[str]]) -> None:
    if not groups:
        client.chat_postMessage(channel=channel_id, text="Not enough opted-in humans to make intros this round.")
        return
    lines = [":fritter: New intros! I’ll DM each group to help you schedule."]
    for g in groups:
        lines.append("• " + " ".join(f"<@{u}>" for u in g))
    client.chat_postMessage(channel=channel_id, text="\n".join(lines))

    # DM each group
    for g in groups:
        dm_text = (
            ":wave: You’ve been matched for a coffee chat!\n"
            f"{' '.join([f'<@{u}>' for u in g])}\n\n"
            "Tips:\n"
            "• Share a couple times that work next week.\n"
            "• 20–30 min is perfect.\n"
            "• Bring a fun question (what’s a hobby you picked up recently?).\n\n"
            "Reply here together to coordinate. Have fun! :coffee:"
        )
        resp = client.conversations_open(users=",".join(g))
        im_id = resp["channel"]["id"]
        client.chat_postMessage(channel=im_id, text=dm_text)

    storage_record_pairs(client, channel_id, groups)

def run_round_for_channel(channel_name_or_id: str) -> None:
    channel_id = _resolve_channel_id(bolt_app.client, channel_name_or_id)
    if not channel_id:
        return
    members = get_channel_members(bolt_app.client, channel_id)
    opted_out = storage_get_opted_out(bolt_app.client)
    pool = [u for u in members if u not in opted_out]  # default IN unless explicitly OUT
    groups = make_groups(bolt_app.client, pool, channel_id, GROUP_SIZE, NO_REPEAT_WEEKS)
    post_groups_and_record(bolt_app.client, channel_id, groups)

# ------------------ Slash Commands ------------------
@bolt_app.command("/fritter-join")
def fritter_join(ack, respond, body):
    ack()
    user_id = body["user_id"]
    storage_set_opt_in(bolt_app.client, user_id, True)
    respond(f"You're in, <@{user_id}>! :fritter:")

@bolt_app.command("/fritter-leave")
def fritter_leave(ack, respond, body):
    ack()
    user_id = body["user_id"]
    storage_set_opt_in(bolt_app.client, user_id, False)
    respond(f"Got it—I've opted you out, <@{user_id}>. You can rejoin anytime with `/fritter-join`.")

@bolt_app.command("/fritter-status")
def fritter_status(ack, respond, body):
    ack()
    user_id = body["user_id"]
    opted_out = storage_get_opted_out(bolt_app.client)
    status = "IN :white_check_mark:" if user_id not in opted_out else "OUT :no_entry_sign:"
    respond(f"<@{user_id}> status: {status}")

@bolt_app.command("/fritter-now")
def fritter_now(ack, respond, body):
    ack()
    user_id = body["user_id"]
    if not is_workspace_admin_or_owner(bolt_app.client, user_id):
        respond(":no_entry: Only workspace admins/owners can run `/fritter-now`.")
        return
    text = (body.get("text") or "").strip()
    channel = text if text else PAIRING_CHANNEL
    respond(f"Running a round for {channel}…")
    try:
        run_round_for_channel(channel)
        respond("Done! Check the channel for announcements and your DMs for intros.")
    except Exception as e:
        respond(f"Something went wrong: `{e}`")

# ------------------ HTTP Endpoints (Vercel) ------------------
@api.post("/api/slack/events")
async def slack_events(req: Request):
    return await handler.handle(req)

@api.post("/api/slack/commands")
async def slack_commands(req: Request):
    return await handler.handle(req)

@api.get("/api/health")
async def health():
    return {"ok": True}

# Vercel Cron target — runs weekly, skips based on week parity for biweekly behavior
@api.post("/api/run_round")
async def cron_run_round(request: Request):
    secret = request.query_params.get("secret")
    if not CRON_SECRET or secret != CRON_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Get current ISO week number (1..53)
    now = dt.datetime.utcnow()
    week = now.isocalendar()[1]
    parity_is_even = (week % 2 == 0)
    should_run = (parity_is_even and BIWEEKLY_PARITY == "even") or ((not parity_is_even) and BIWEEKLY_PARITY == "odd")

    if not should_run:
        # Skip this week for biweekly cadence
        return {"ok": True, "week": week, "ran": False, "parity": "even" if parity_is_even else "odd"}

    channels = [s.strip() for s in (PAIRING_CHANNELS or PAIRING_CHANNEL).split(",") if s.strip()]
    for ch in channels:
        run_round_for_channel(ch)

    # Record that we ran this week (handy for debugging/inspection)
    storage_record_last_week(bolt_app.client, week)

    return {"ok": True, "channels": channels, "week": week, "ran": True, "parity": "even" if parity_is_even else "odd"}
