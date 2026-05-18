from http.server import BaseHTTPRequestHandler
import json
import os
import sys
from dotenv import load_dotenv
import datetime as dt

load_dotenv()

# Add the parent directory to the path to allow importing from api.slack
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

try:
    from api.slack import (
        run_round_for_channel,
        bolt_app,
        PAIRING_CHANNEL,
        PAIRING_CHANNELS,
        BIWEEKLY_PARITY,
        storage_record_last_week,
        CRON_SECRET
    )
    BOT_AVAILABLE = True
except ImportError as e:
    BOT_AVAILABLE = False
    print(f"Bot functions not available: {e}")

class handler(BaseHTTPRequestHandler):
    def _write_json(self, payload):
        try:
            print(json.dumps({"run_round_response": payload}, separators=(",", ":")))
        except Exception:
            pass
        self.wfile.write(json.dumps(payload).encode('utf-8'))
    def do_POST(self):
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        
        # Check secret
        secret = self.path.split('secret=')[1] if 'secret=' in self.path else None
        if not CRON_SECRET or secret != CRON_SECRET:
            self._write_json({"error": "Unauthorized"})
            return
        
        if not BOT_AVAILABLE:
            self._write_json({"error": "Bot not available"})
            return
        
        try:
            # Get current ISO week number (1..53)
            now = dt.datetime.utcnow()
            week = now.isocalendar()[1]
            parity_is_even = (week % 2 == 0)
            
            # DEBUG: Show what values we're working with
            debug_info = {
                "week": week,
                "parity_is_even": parity_is_even,
                "BIWEEKLY_PARITY": BIWEEKLY_PARITY,
                "BIWEEKLY_PARITY_type": type(BIWEEKLY_PARITY).__name__,
                "BIWEEKLY_PARITY_repr": repr(BIWEEKLY_PARITY)
            }
            
            should_run = (parity_is_even and BIWEEKLY_PARITY == "even") or ((not parity_is_even) and BIWEEKLY_PARITY == "odd")

            if not should_run:
                # Skip this week for biweekly cadence
                result = {"ok": True, "week": week, "ran": False, "parity": "even" if parity_is_even else "odd", "debug": debug_info}
                self._write_json(result)
                return

            channels = [s.strip() for s in (PAIRING_CHANNELS or PAIRING_CHANNEL).split(",") if s.strip()]
            for ch in channels:
                run_round_for_channel(ch)

            # Record that we ran this week (handy for debugging/inspection)
            storage_record_last_week(bolt_app.client, week)

            result = {"ok": True, "channels": channels, "week": week, "ran": True, "parity": "even" if parity_is_even else "odd", "debug": debug_info}
            self._write_json(result)
            
        except Exception as e:
            error_result = {"error": str(e)}
            self._write_json(error_result)

    def do_GET(self):
        # Mirror do_POST so Vercel Cron (GET) can trigger the same logic
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()

        # Optional: note whether request had the Vercel Cron header (no longer required)
        try:
            vercel_cron_header = (self.headers.get('X-Vercel-Cron') or self.headers.get('x-vercel-cron'))
            print(json.dumps({"run_round": {"vercel_cron_header": vercel_cron_header}}, separators=(",", ":")))
        except Exception:
            pass

        # Check secret from querystring
        secret = self.path.split('secret=')[1] if 'secret=' in self.path else None
        if not CRON_SECRET or secret != CRON_SECRET:
            self._write_json({"error": "Unauthorized"})
            return

        if not BOT_AVAILABLE:
            self._write_json({"error": "Bot not available"})
            return

        try:
            now = dt.datetime.utcnow()
            week = now.isocalendar()[1]
            parity_is_even = (week % 2 == 0)

            debug_info = {
                "week": week,
                "parity_is_even": parity_is_even,
                "BIWEEKLY_PARITY": BIWEEKLY_PARITY,
                "BIWEEKLY_PARITY_type": type(BIWEEKLY_PARITY).__name__,
                "BIWEEKLY_PARITY_repr": repr(BIWEEKLY_PARITY)
            }

            should_run = (parity_is_even and BIWEEKLY_PARITY == "even") or ((not parity_is_even) and BIWEEKLY_PARITY == "odd")

            if not should_run:
                result = {"ok": True, "week": week, "ran": False, "parity": "even" if parity_is_even else "odd", "debug": debug_info}
                self._write_json(result)
                return

            channels = [s.strip() for s in (PAIRING_CHANNELS or PAIRING_CHANNEL).split(",") if s.strip()]
            for ch in channels:
                run_round_for_channel(ch)

            storage_record_last_week(bolt_app.client, week)

            result = {"ok": True, "channels": channels, "week": week, "ran": True, "parity": "even" if parity_is_even else "odd", "debug": debug_info}
            self._write_json(result)
        except Exception as e:
            self._write_json({"error": str(e)})
