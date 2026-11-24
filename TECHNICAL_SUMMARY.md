# Technical Summary – Retail AI Transaction Webhook Integration

## Setup and Configuration

The integration runs entirely on Python 3.10+ standard library with zero external dependencies. Configuration is loaded from environment variables via a `.env` file. The system consists of two HTTP servers:

- **Listener** (port 8765): Receives signed transaction events, validates them, reconciles against the SQLite database, and forwards Slack-compatible notifications.
- **Receiver** (port 8766): A local stand-in for Slack incoming webhooks that stores payloads to `logs/received.jsonl` for verification.

## Signature and Security Design

Every incoming event must include an `X-Event-Signature` header in the format `sha256=<hex_digest>`. The listener computes `HMAC-SHA256(EVENT_SIGNING_SECRET, raw_request_body)` and compares it to the provided signature using `hmac.compare_digest` (constant-time comparison) to prevent timing attacks. Requests with missing or invalid signatures are rejected with HTTP 401 before any further processing.

No secrets, signing keys, or customer identifiers are included in source code, outbound notifications, or log output.

## JSON Validation and Transaction Reconciliation

The listener enforces a strict two-layer schema:

- **Top-level**: `event_id`, `event_type`, `created_at`, `data` (all required)
- **Data object**: `transaction_id`, `order_id`, `customer_ref`, `amount_minor`, `currency`, `status` (all required)

Missing fields return HTTP 400 with a descriptive error listing which fields are absent.

After schema validation, the listener looks up the `transaction_id` in the SQLite database. If the transaction does not exist, HTTP 404 is returned. If found, every reconciliation field (`order_id`, `customer_ref`, `amount_minor`, `currency`, `status`) is compared. Any mismatch returns HTTP 409 identifying the conflicting field.

## Slack-Style Payload Mapping

Only events with `status: "success"` that pass all validation and reconciliation checks produce a notification. The outbound payload follows the structure in `EXPECTED_SLACK_FORMAT.json`:

```json
{
  "text": "Payment successful: TXN-XXXX | INR XXXX.XX | Order ORD-XXXX",
  "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "..."}}]
}
```

The `customer_ref` field is intentionally excluded from the outgoing message to prevent leaking customer identifiers.

## Duplicate Protection and Retry Strategy

**Duplicate Prevention**: Processed event IDs are stored in a persistent SQLite database (`processed_events.sqlite3`) with a unique constraint on `event_id`. This survives listener restarts. Duplicate events are detected before any notification attempt and return a `{"status": "duplicate"}` response.

**Retry Strategy**: If the receiver is unavailable, the listener retries up to 3 times with exponential backoff (1s, 2s, 3s delays). Each attempt uses a 5-second timeout. After all retries are exhausted, the event is NOT marked as processed, so it can be retried on the next delivery attempt. This ensures at-least-once delivery without duplicate notifications.

## Test Scenarios and Results

| Case | Scenario | Expected | Result |
|------|----------|----------|--------|
| T01 | Valid success event | Notify once | PASS |
| T02 | Duplicate event | No duplicate notification | PASS |
| T03 | Restart + repeat | No duplicate notification | PASS |
| T04 | Failed transaction | No notification | PASS |
| T05 | Pending transaction | No notification | PASS |
| T06 | Missing signature | HTTP 401 | PASS |
| T07 | Wrong signature | HTTP 401 | PASS |
| T08 | Malformed JSON | HTTP 400 | PASS |
| T09 | Missing required fields | HTTP 400 | PASS |
| T10 | Unknown transaction | HTTP 404 | PASS |
| T11 | Mismatch against DB | HTTP 409 | PASS |
| T12 | Receiver unavailable | Retryable failure (502) | PASS |
| T13 | Receiver recovery | Delivered once after retry | PASS |
| T14 | Key masking | No secrets/customer IDs in alerts | PASS |

## Outstanding Issues and Maintenance Guidance

- The retry mechanism is synchronous within the request handler. For production use, an async task queue would be more appropriate.
- The dedup database grows indefinitely; in production, implement periodic cleanup of old event IDs.
- No TLS is configured for local development; production deployments should use HTTPS.
