import json, os, hmac, hashlib, sqlite3, time, logging, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("DB_PATH", str(BASE.parent / "transactions.sqlite3")))
SIGNING_SECRET = os.getenv("EVENT_SIGNING_SECRET", "local-signing-secret")
RECEIVER_URL = os.getenv("RECEIVER_URL", "http://127.0.0.1:8766/slack/incoming")
LISTENER_PORT = int(os.getenv("LISTENER_PORT", "8765"))
MAX_BODY = 32768
RECEIVER_TIMEOUT = 5
MAX_RETRIES = 3
RETRY_DELAY = 1
DEDUP_DB = BASE / "processed_events.sqlite3"

REQUIRED_TOP = {"event_id", "event_type", "created_at", "data"}
REQUIRED_DATA = {"transaction_id", "order_id", "customer_ref", "amount_minor", "currency", "status"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("listener")


def _init_dedup_db():
    with sqlite3.connect(DEDUP_DB) as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS processed_events "
            "(event_id TEXT PRIMARY KEY, processed_at TEXT)"
        )
    return DEDUP_DB


def is_duplicate(event_id):
    with sqlite3.connect(_dedup_path) as db:
        row = db.execute(
            "SELECT 1 FROM processed_events WHERE event_id=?", (event_id,)
        ).fetchone()
    return row is not None


def mark_processed(event_id):
    with sqlite3.connect(_dedup_path) as db:
        db.execute(
            "INSERT OR IGNORE INTO processed_events (event_id, processed_at) VALUES (?, ?)",
            (event_id, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
        )


def lookup_transaction(transaction_id):
    with sqlite3.connect(DB_PATH) as db:
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT * FROM transactions WHERE transaction_id=?", (transaction_id,)
        ).fetchone()
    return dict(row) if row else None


def verify_signature(raw_body, header_value):
    if not header_value or not header_value.startswith("sha256="):
        return False
    expected = hmac.new(
        SIGNING_SECRET.encode(), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, header_value[7:])


def validate_payload(data):
    if not isinstance(data, dict):
        return None, "payload must be a JSON object"
    missing_top = REQUIRED_TOP - data.keys()
    if missing_top:
        return None, f"missing fields: {', '.join(sorted(missing_top))}"
    inner = data.get("data")
    if not isinstance(inner, dict):
        return None, "data must be an object"
    missing_data = REQUIRED_DATA - inner.keys()
    if missing_data:
        return None, f"missing data fields: {', '.join(sorted(missing_data))}"
    return data, None


def reconcile(event_data, db_record):
    checks = ["order_id", "customer_ref", "amount_minor", "currency", "status"]
    for field in checks:
        if event_data.get(field) != db_record.get(field):
            return False, field
    return True, None


def build_slack_payload(txn_id, order_id, amount_minor, currency):
    amount_str = f"{amount_minor / 100:.2f}"
    text = f"Payment successful: {txn_id} | {currency} {amount_str} | Order {order_id}"
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*:white_check_mark: Payment success*\n"
                    f"*Transaction:* {txn_id}\n"
                    f"*Order:* {order_id}\n"
                    f"*Amount:* {currency} {amount_str}"
                ),
            },
        }
    ]
    return {"text": text, "blocks": blocks}


def send_to_receiver(payload):
    body = json.dumps(payload).encode()
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(
                RECEIVER_URL,
                data=body,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=RECEIVER_TIMEOUT) as resp:
                return resp.status, json.loads(resp.read().decode())
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            log.warning("receiver attempt %d/%d failed: %s", attempt, MAX_RETRIES, exc)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY * attempt)
    return None, "receiver unavailable after retries"


_dedup_path = _init_dedup_db()


class Listener(BaseHTTPRequestHandler):
    def respond(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            return self.respond(200, {"ok": True, "service": "transaction-listener"})
        self.respond(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/events/transaction":
            return self.respond(404, {"error": "not found"})

        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0 or content_length > MAX_BODY:
            return self.respond(400, {"error": "invalid body size"})

        raw_body = self.rfile.read(content_length)

        sig_header = self.headers.get("X-Event-Signature", "")
        if not verify_signature(raw_body, sig_header):
            log.info("rejected: invalid signature")
            return self.respond(401, {"error": "invalid signature"})

        try:
            payload = json.loads(raw_body)
        except (json.JSONDecodeError, ValueError):
            return self.respond(400, {"error": "invalid JSON"})

        payload, err = validate_payload(payload)
        if err:
            return self.respond(400, {"error": err})

        event_id = payload["event_id"]
        data = payload["data"]
        txn_id = data["transaction_id"]

        if is_duplicate(event_id):
            log.info("duplicate event %s ignored", event_id)
            return self.respond(200, {"status": "duplicate", "event_id": event_id})

        db_record = lookup_transaction(txn_id)
        if db_record is None:
            return self.respond(404, {"error": "transaction not found", "transaction_id": txn_id})

        ok, field = reconcile(data, db_record)
        if not ok:
            return self.respond(409, {"error": "data mismatch", "field": field})

        if data["status"] != "success":
            mark_processed(event_id)
            log.info("event %s: status=%s, no notification", event_id, data["status"])
            return self.respond(
                200, {"status": "accepted", "notify": False, "reason": data["status"]}
            )

        slack_payload = build_slack_payload(
            txn_id, data["order_id"], data["amount_minor"], data["currency"]
        )

        status_code, result = send_to_receiver(slack_payload)
        if status_code is None:
            log.error("event %s: receiver delivery failed", event_id)
            return self.respond(
                502, {"error": "receiver unavailable", "event_id": event_id}
            )

        mark_processed(event_id)
        log.info("event %s: notified for %s", event_id, txn_id)
        return self.respond(
            200, {"status": "notified", "event_id": event_id, "transaction_id": txn_id}
        )

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print(f"Listener on 127.0.0.1:{LISTENER_PORT}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", LISTENER_PORT), Listener).serve_forever()
