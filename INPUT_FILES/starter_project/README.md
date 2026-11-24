# Retail AI Services – Transaction Webhook Integration

## Prerequisites

- Python 3.10+
- No pip packages required (stdlib only)

## Quick Start

```
cd INPUT_FILES/starter_project
copy .env.example .env
```

Edit `.env` and set `EVENT_SIGNING_SECRET` to your chosen secret (default: `local-signing-secret`).

**Terminal 1 – Receiver:**
```
python receiver.py
```

**Terminal 2 – Listener:**
```
python listener.py
```

**Terminal 3 – Tests:**
```
python test_starter.py
python test_integration.py
```

## Sending Individual Events

```
set EVENT_SIGNING_SECRET=local-signing-secret
python send_event.py ../fixtures/event_01_success.json
```

## Project Structure

```
INPUT_FILES/
├── transactions.sqlite3        # 16 pre-populated transactions
├── fixtures/                   # 8 event fixtures + expected Slack format
│   ├── event_01_success.json
│   ├── event_02_failed.json
│   ├── event_03_pending.json
│   ├── event_04_success.json
│   ├── event_05_success.json
│   ├── event_06_success.json
│   ├── event_07_bad_schema.json
│   ├── event_08_unknown_transaction.json
│   └── EXPECTED_SLACK_FORMAT.json
├── reference/                  # Specs and test scenarios
└── starter_project/
    ├── listener.py             # Webhook listener (implemented)
    ├── receiver.py             # Local Slack-compatible receiver
    ├── send_event.py           # HMAC-signed event sender
    ├── test_starter.py         # DB and fixture validation
    ├── test_integration.py     # Full end-to-end test suite
    ├── processed_events.sqlite3 # Auto-created dedup store
    ├── .env.example
    └── .env
```

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `EVENT_SIGNING_SECRET` | `local-signing-secret` | HMAC SHA-256 signing key |
| `RECEIVER_URL` | `http://127.0.0.1:8766/slack/incoming` | Slack-compatible receiver endpoint |
| `DB_PATH` | `../transactions.sqlite3` | Path to transaction database |
| `LISTENER_PORT` | `8765` | Listener HTTP port |
| `RECEIVER_PORT` | `8766` | Receiver HTTP port |

## API Endpoints

### Listener (port 8765)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Health check |
| `POST` | `/events/transaction` | Receive signed transaction events |

### Receiver (port 8766)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Health check |
| `GET` | `/deliveries` | View all received notifications |
| `POST` | `/slack/incoming` | Accept Slack-format JSON payloads |
