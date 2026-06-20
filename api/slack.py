# api/slack.py
import os
import json
import random
import asyncio
import datetime as dt
from typing import List, Tuple, Optional, Set

from fastapi import FastAPI, Request, HTTPException
from dotenv import load_dotenv

from slack_bolt import App, BoltRequest
from slack_bolt.adapter.starlette.handler import to_starlette_response
from slack_sdk.errors import SlackApiError
from starlette.responses import Response


# ------------------ Config ------------------
load_dotenv()

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET")

# Target pairing channel(s) - "#name" or channel ID, multiple via comma-separated PAIRING_CHANNELS
PAIRING_CHANNEL = os.getenv("PAIRING_CHANNEL", "#fritter")
PAIRING_CHANNELS = os.getenv("PAIRING_CHANNELS") # optional, comma-separated

NO_REPEAT_WEEKS = int(os.getenv("NO_REPEAT_WEEKS", "8"))
GROUP_SIZE = int(os.getenv("GROUP_SIZE", "2")) # 2 or 3
NO_REPEAT_MODE = os.getenv("NO_REPEAT_MODE", "weeks").lower() # "weeks" or "ever"

# Slack channel used as a tiny history for persistence 
STORAGE_CHANNEL = os.getenv("STORAGE_CHANNEL", "#fritter-storage")

# Cron auth: the secret must come via query param ?secret=...
CRON_SECRET = os.getenv("CRON_SECRET")

# Biweekly controls: run on "even" or "odd" ISO weeks (default: "even")
BIWEEKLY_PARITY = (os.getenv("BIWEEKLY_PARITY", "even").strip().lower() or "even")

# ------------------ Slack App (HTTP mode for Vercel) ------------------
bolt_app = App(token=SLACK_BOT_TOKEN, signing_secret=SLACK_SIGNING_SECRET)
api = FastAPI()


def _ephemeral(text: str) -> dict:
    return {"response_type": "ephemeral", "text": text}


def _dispatch_slack(body: bytes, query, headers) -> Response:
    bolt_req = BoltRequest(
        body=body.decode("utf-8"),
        query=query,
        headers=headers,
    )
    return to_starlette_response(bolt_app.dispatch(bolt_req))


async def _handle_slack(req: Request) -> Response:
    body = await req.body()
    return await asyncio.to_thread(_dispatch_slack, body, req.query_params, req.headers)

# ------------------ Slack-backed Storage ------------------
PAIR_MARKER = "FRITTER_HISTORY_V1"
OPT_MARKER = "FRITTER_OPT_V1"
META_MARKER = "FRITTER_META_V1" # tracks last-run week

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
    oldest = str(int(since.timestamp()))
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
    all_opt_messages = [] # Collect all messages first
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
            all_opt_messages.append(data)
        cursor = res.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    
    # Sort messages chronologically (oldest first)
    all_opt_messages.sort(key=lambda x: x['when'])

    state = {} # user_id -> is_in
    for data in all_opt_messages:
        u = data.get("user")
        is_in = bool(data.get("is_in", True))
        state[u] = is_in # Later messages overwrite earlier ones

    return {u for u, is_in in state.items() if not is_in}

# ---------- Last-run week for biweekly cadence ----------
def storage_record_last_week(client, week: int) -> None:
    payload = {"marker": META_MARKER, "week": int(week), "when": _now_utc_iso()}
    scid = _storage_channel_id(client)
    text = f"{META_MARKER} ```{json.dumps(payload, separators=(',', ':'))}```"
    client.chat_postMessage(channel=scid, text=text)

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
def _pair_history_cutoff(no_repeat_weeks: int) -> dt.datetime:
    if NO_REPEAT_MODE == "ever":
        return dt.datetime(1970, 1, 1)
    return dt.datetime.utcnow() - dt.timedelta(weeks=no_repeat_weeks)


def recently_paired(a: str, b: str, recent: Set[Tuple[str, str]]) -> bool:
    return _norm(a, b) in recent


def make_groups(
    users: List[str],
    recent: Set[Tuple[str, str]],
    group_size: int,
) -> List[List[str]]:
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
                if not recently_paired(a, b, recent):
                    partner_idx = i
                    break
            if partner_idx is None:
                partner_idx = len(users) - 1
            b = users.pop(partner_idx)
            temp.append([a, b])
        if users:
            leftover = users.pop()
            best_idx = 0
            best_score = 10**9
            for i, (x, y) in enumerate(temp):
                recency = 0
                if _norm(x, leftover) in recent:
                    recency += 1
                if _norm(y, leftover) in recent:
                    recency += 1
                if recency < best_score:
                    best_score = recency
                    best_idx = i
            temp[best_idx].append(leftover)
        groups = temp
    else:
        while len(users) >= 3:
            a, b, c = users.pop(), users.pop(), users.pop()
            tries = 0
            while (
                recently_paired(a, b, recent)
                or recently_paired(a, c, recent)
                or recently_paired(b, c, recent)
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
    lines = [":fritter: New intros! I'll DM each group to help you schedule."]
    for g in groups:
        lines.append("• " + " ".join(f"<@{u}>" for u in g))
    client.chat_postMessage(channel=channel_id, text="\n".join(lines))

    # DM each group
    for g in groups:
        dm_text = (
            ":wave: You've been matched for a fritter!\n"
            f"{' '.join([f'<@{u}>' for u in g])}\n\n"
            "Tips:\n"
            "• Share a couple times that work in the next two weeks :calendar: \n"
            "• 20–30 min is perfect :star-struck:\n"
            "• Bring a fun question e.g. what's your favorite way to eat a potato? :potato:\n\n"
            "Reply here together to coordinate. Have fun! :fritter:"
        )
        resp = client.conversations_open(users=",".join(g))
        im_id = resp["channel"]["id"]
        client.chat_postMessage(channel=im_id, text=dm_text)

    storage_record_pairs(client, channel_id, groups)

def _normalize_channel(name_or_id: str) -> str:
    s = (name_or_id or "").strip()
    if not s:
        return s
    if s.startswith("#") or s[0] in "CGD":
        return s
    return f"#{s}"


def run_round_for_channel(channel_name_or_id: str) -> None:
    client = bolt_app.client
    channel_name_or_id = _normalize_channel(channel_name_or_id)
    channel_id = _resolve_channel_id(client, channel_name_or_id)
    if not channel_id:
        return

    members = get_channel_members(client, channel_id)
    opted_out = storage_get_opted_out(client)
    pool = [u for u in members if u not in opted_out]
    recent = storage_get_recent_pairs(client, channel_id, _pair_history_cutoff(NO_REPEAT_WEEKS))
    groups = make_groups(pool, recent, GROUP_SIZE)
    post_groups_and_record(client, channel_id, groups)

# ------------------ Slash Commands ------------------
@bolt_app.command("/fritter-join")
def fritter_join(ack, body):
    user_id = body["user_id"]
    try:
        storage_set_opt_in(bolt_app.client, user_id, True)
        ack(_ephemeral(f"You're in, <@{user_id}>! :fritter:"))
    except Exception as e:
        ack(_ephemeral(f"Error joining: `{e}`"))

@bolt_app.command("/fritter-leave")
def fritter_leave(ack, body):
    user_id = body["user_id"]
    try:
        storage_set_opt_in(bolt_app.client, user_id, False)
        ack(_ephemeral(f"Got it—I've opted you out, <@{user_id}>. You can rejoin anytime with `/fritter-join`."))
    except Exception as e:
        ack(_ephemeral(f"Error leaving: `{e}`"))

@bolt_app.command("/fritter-status")
def fritter_status(ack, body):
    user_id = body["user_id"]
    try:
        opted_out = storage_get_opted_out(bolt_app.client)
        status = "IN :white_check_mark:" if user_id not in opted_out else "OUT :no_entry_sign:"
        ack(_ephemeral(f"<@{user_id}> status: {status}"))
    except Exception as e:
        ack(_ephemeral(f"Error checking status: `{e}`"))

@bolt_app.command("/fritter-now")
def fritter_now(ack, body):
    user_id = body["user_id"]
    try:
        if not is_workspace_admin_or_owner(bolt_app.client, user_id):
            ack(_ephemeral(":no_entry: Only workspace admins/owners can run `/fritter-now`."))
            return
        text = (body.get("text") or "").strip()
        channel = _normalize_channel(text) if text else PAIRING_CHANNEL
        run_round_for_channel(channel)
        ack(_ephemeral(f"Done! Pairings posted in {channel}. Check your DMs for intros."))
    except Exception as e:
        ack(_ephemeral(f"Something went wrong: `{e}`"))

# ------------------ HTTP Endpoints (Vercel) ------------------
@api.post("/api/slack/events")
async def slack_events(req: Request):
    return await _handle_slack(req)

@api.post("/api/slack/commands")
async def slack_commands(req: Request):
    return await _handle_slack(req)

@api.get("/api/health")
async def health():
    return {"ok": True}

# Vercel Cron target — runs weekly, skips based on week parity for biweekly behavior
@api.get("/api/slack/run_round")
@api.post("/api/slack/run_round")
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

    # Record that we ran this week
    storage_record_last_week(bolt_app.client, week)

    return {"ok": True, "channels": channels, "week": week, "ran": True, "parity": "even" if parity_is_even else "odd"}
