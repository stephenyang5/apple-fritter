from http.server import BaseHTTPRequestHandler
import json
import os
import urllib.parse
from dotenv import load_dotenv

load_dotenv()

# Import the bot functions
import sys
sys.path.append('/var/task')
try:
    from api.slack import (
        bolt_app, 
        OPT_MARKER, 
        _storage_channel_id,
        storage_set_opt_in,
        is_workspace_admin_or_owner, 
        run_round_for_channel
    )
    BOT_AVAILABLE = True
except ImportError as e:
    BOT_AVAILABLE = False
    print(f"Bot functions not available: {e}")

def fixed_storage_get_opted_out(client):
    """Fixed version that correctly reads the latest status for each user"""
    scid = _storage_channel_id(client)
    cursor = None
    state = {}  # user_id -> is_in
    all_messages = []
    
    # Collect all messages first
    while True:
        res = client.conversations_history(channel=scid, cursor=cursor, limit=200)
        all_messages.extend(res.get("messages", []))
        cursor = res.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    
    # Process messages in chronological order (oldest first)
    for msg in sorted(all_messages, key=lambda x: float(x.get("ts", "0"))):
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
        state[u] = is_in  # Later messages (chronologically) overwrite earlier ones
    
    # Return users who are explicitly opted out (is_in = False)
    return {u for u, is_in in state.items() if not is_in}

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            # Read the request body
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            
            # Parse form data (Slack sends commands as form data)
            form_data = post_data.decode('utf-8')
            parsed_data = urllib.parse.parse_qs(form_data)
            
            command = parsed_data.get('command', [''])[0]
            user_id = parsed_data.get('user_id', [''])[0]
            text = parsed_data.get('text', [''])[0]
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            
            response = {"response_type": "ephemeral"}
            
            if not BOT_AVAILABLE:
                response["text"] = "Bot functionality is being loaded. Please try again in a moment."
                self.wfile.write(json.dumps(response).encode('utf-8'))
                return
            
            if command == '/fritter-join':
                try:
                    storage_set_opt_in(bolt_app.client, user_id, True)
                    response["text"] = f"You're in, <@{user_id}>! :fritter:"
                except Exception as e:
                    response["text"] = f"Error joining: {str(e)}"
                
            elif command == '/fritter-leave':
                try:
                    storage_set_opt_in(bolt_app.client, user_id, False)
                    response["text"] = f"Got it—I've opted you out, <@{user_id}>. You can rejoin anytime with `/fritter-join`."
                except Exception as e:
                    response["text"] = f"Error leaving: {str(e)}"
                
            elif command == '/fritter-status':
                try:
                    opted_out = fixed_storage_get_opted_out(bolt_app.client)
                    status = "IN :white_check_mark:" if user_id not in opted_out else "OUT :no_entry_sign:"
                    response["text"] = f"<@{user_id}> status: {status}"
                except Exception as e:
                    response["text"] = f"Error checking status: {str(e)}"
                
            elif command == '/fritter-now':
                try:
                    if not is_workspace_admin_or_owner(bolt_app.client, user_id):
                        response["text"] = ":no_entry: Only workspace admins/owners can run `/fritter-now`."
                    else:
                        channel = text.strip() if text.strip() else os.getenv("PAIRING_CHANNEL", "#coffee-intros")
                        response["text"] = f"Running a round for {channel}…"
                        # Run the actual pairing
                        run_round_for_channel(channel)
                        response["text"] = "Done! Check the channel for announcements and your DMs for intros."
                except Exception as e:
                    response["text"] = f"Something went wrong: `{str(e)}`"
                
            else:
                response["text"] = "Unknown command"
            
            self.wfile.write(json.dumps(response).encode('utf-8'))
            
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode('utf-8'))
