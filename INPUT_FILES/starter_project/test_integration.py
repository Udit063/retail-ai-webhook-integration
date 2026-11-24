import unittest
import json
import hmac
import hashlib
import os
import time
import sqlite3
import threading
import urllib.request
import urllib.error
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from listener import (
    verify_signature,
    validate_payload,
    reconcile,
    build_slack_payload,
    lookup_transaction,
    DEDUP_DB,
)

BASE = pathlib.Path(__file__).resolve().parent.parent
SECRET = os.getenv("EVENT_SIGNING_SECRET", "local-signing-secret")
LISTENER = "http://127.0.0.1:8765"
RECEIVER = "http://127.0.0.1:8766"
FIXTURES = BASE / "fixtures"


def sign(body_bytes):
    sig = hmac.new(SECRET.encode(), body_bytes, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


def post_event(body_bytes, signature=None):
    headers = {"Content-Type": "application/json"}
    if signature is not None:
        headers["X-Event-Signature"] = signature
    req = urllib.request.Request(
        f"{LISTENER}/events/transaction",
        data=body_bytes,
        method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def load_fixture(name):
    return (FIXTURES / name).read_bytes()


class TestDatabaseIntegrity(unittest.TestCase):
    def test_transaction_count(self):
        with sqlite3.connect(BASE / "transactions.sqlite3") as db:
            count = db.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        self.assertEqual(count, 16)

    def test_lookup_existing(self):
        rec = lookup_transaction("TXN-0001")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["status"], "success")
        self.assertEqual(rec["amount_minor"], 93600)

    def test_lookup_missing(self):
        self.assertIsNone(lookup_transaction("TXN-9999"))

    def test_fixture_count(self):
        fixtures = list(FIXTURES.glob("event_*.json"))
        self.assertEqual(len(fixtures), 8)


class TestSignatureValidation(unittest.TestCase):
    def test_valid_signature(self):
        body = b'{"test": true}'
        sig = sign(body)
        self.assertTrue(verify_signature(body, sig))

    def test_invalid_signature(self):
        body = b'{"test": true}'
        self.assertFalse(verify_signature(body, "sha256=0000000000"))

    def test_missing_signature(self):
        self.assertFalse(verify_signature(b"{}", ""))
        self.assertFalse(verify_signature(b"{}", None))

    def test_wrong_prefix(self):
        self.assertFalse(verify_signature(b"{}", "md5=abc"))


class TestPayloadValidation(unittest.TestCase):
    def test_valid_payload(self):
        p = {
            "event_id": "E1",
            "event_type": "transaction.status_changed",
            "created_at": "2026-01-01T00:00:00Z",
            "data": {
                "transaction_id": "T1",
                "order_id": "O1",
                "customer_ref": "C1",
                "amount_minor": 100,
                "currency": "INR",
                "status": "success",
            },
        }
        result, err = validate_payload(p)
        self.assertIsNotNone(result)
        self.assertIsNone(err)

    def test_missing_top_field(self):
        p = {"event_id": "E1", "data": {}}
        _, err = validate_payload(p)
        self.assertIsNotNone(err)

    def test_missing_data_fields(self):
        p = {
            "event_id": "E1",
            "event_type": "t",
            "created_at": "t",
            "data": {"transaction_id": "T1"},
        }
        _, err = validate_payload(p)
        self.assertIn("missing data fields", err)


class TestReconciliation(unittest.TestCase):
    def test_match(self):
        event = {
            "order_id": "ORD-0001",
            "customer_ref": "CUST-002",
            "amount_minor": 93600,
            "currency": "INR",
            "status": "success",
        }
        db_rec = lookup_transaction("TXN-0001")
        ok, field = reconcile(event, db_rec)
        self.assertTrue(ok)

    def test_mismatch(self):
        event = {
            "order_id": "ORD-WRONG",
            "customer_ref": "CUST-002",
            "amount_minor": 93600,
            "currency": "INR",
            "status": "success",
        }
        db_rec = lookup_transaction("TXN-0001")
        ok, field = reconcile(event, db_rec)
        self.assertFalse(ok)
        self.assertEqual(field, "order_id")


class TestSlackPayload(unittest.TestCase):
    def test_format_matches_expected(self):
        expected = json.loads((FIXTURES / "EXPECTED_SLACK_FORMAT.json").read_text())
        result = build_slack_payload("TXN-0001", "ORD-0001", 93600, "INR")
        self.assertEqual(result["text"], expected["text"])
        self.assertEqual(len(result["blocks"]), 1)
        self.assertEqual(result["blocks"][0]["type"], "section")

    def test_no_customer_ref_in_payload(self):
        result = build_slack_payload("TXN-0001", "ORD-0001", 93600, "INR")
        payload_str = json.dumps(result)
        self.assertNotIn("CUST-", payload_str)


class TestEndToEnd(unittest.TestCase):
    def _clear_dedup(self):
        if DEDUP_DB.exists():
            with sqlite3.connect(DEDUP_DB) as db:
                db.execute("DELETE FROM processed_events")

    def test_t01_valid_success_event(self):
        self._clear_dedup()
        body = load_fixture("event_01_success.json")
        status, resp = post_event(body, sign(body))
        self.assertEqual(status, 200)
        self.assertEqual(resp["status"], "notified")

    def test_t02_duplicate_event(self):
        body = load_fixture("event_01_success.json")
        status, resp = post_event(body, sign(body))
        self.assertEqual(status, 200)
        self.assertEqual(resp["status"], "duplicate")

    def test_t04_failed_transaction(self):
        body = load_fixture("event_02_failed.json")
        status, resp = post_event(body, sign(body))
        self.assertEqual(status, 200)
        self.assertFalse(resp.get("notify", True))

    def test_t05_pending_transaction(self):
        body = load_fixture("event_03_pending.json")
        status, resp = post_event(body, sign(body))
        self.assertEqual(status, 200)
        self.assertFalse(resp.get("notify", True))

    def test_t06_missing_signature(self):
        body = load_fixture("event_01_success.json")
        status, resp = post_event(body, signature=None)
        self.assertEqual(status, 401)

    def test_t07_wrong_signature(self):
        body = load_fixture("event_01_success.json")
        status, resp = post_event(body, "sha256=00000000deadbeef")
        self.assertEqual(status, 401)

    def test_t08_malformed_json(self):
        body = b"not valid json at all{{"
        status, resp = post_event(body, sign(body))
        self.assertEqual(status, 400)

    def test_t09_missing_required_fields(self):
        body = load_fixture("event_07_bad_schema.json")
        status, resp = post_event(body, sign(body))
        self.assertEqual(status, 400)

    def test_t10_unknown_transaction(self):
        body = load_fixture("event_08_unknown_transaction.json")
        status, resp = post_event(body, sign(body))
        self.assertEqual(status, 404)

    def test_t11_mismatch_against_db(self):
        payload = {
            "event_id": "EVT-MISMATCH-01",
            "event_type": "transaction.status_changed",
            "created_at": "2026-11-23T10:30:00Z",
            "data": {
                "transaction_id": "TXN-0001",
                "order_id": "ORD-WRONG",
                "customer_ref": "CUST-002",
                "amount_minor": 93600,
                "currency": "INR",
                "status": "success",
            },
        }
        body = json.dumps(payload).encode()
        status, resp = post_event(body, sign(body))
        self.assertEqual(status, 409)

    def test_t14_no_secrets_in_slack_payload(self):
        result = build_slack_payload("TXN-0001", "ORD-0001", 93600, "INR")
        s = json.dumps(result)
        self.assertNotIn(SECRET, s)
        self.assertNotIn("CUST-", s)

    def test_receiver_deliveries_endpoint(self):
        req = urllib.request.Request(f"{RECEIVER}/deliveries")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
        self.assertIn("deliveries", data)
        self.assertIsInstance(data["deliveries"], list)

    def test_additional_success_events(self):
        for fixture in ["event_04_success.json", "event_05_success.json", "event_06_success.json"]:
            body = load_fixture(fixture)
            status, resp = post_event(body, sign(body))
            self.assertEqual(status, 200)
            self.assertEqual(resp["status"], "notified")


if __name__ == "__main__":
    unittest.main(verbosity=2)
