# Pi-hole Controller (Multi-Instance)

A REST API + web UI for simultaneously disabling/re-enabling filtering across **two Pi-hole instances** in parallel.

## Setup

```bash
pip install -r requirements.txt

# Primary Pi-hole (required)
export PIHOLE1_HOST="http://192.168.1.2"
export PIHOLE1_API_KEY="your_primary_api_key"
export PIHOLE1_NAME="Primary"         # optional display name

# Secondary Pi-hole (optional — omit PIHOLE2_HOST to run single-instance)
export PIHOLE2_HOST="http://192.168.1.3"
export PIHOLE2_API_KEY="your_secondary_api_key"
export PIHOLE2_NAME="Secondary"       # optional display name

python app.py
```

Open **http://localhost:8080** in your browser.

## How it works

- `POST /api/disable` fans out to **both instances simultaneously** using `asyncio.gather`
- If one instance fails, the other still gets disabled — the response includes a `partial: true` flag and lists which failed
- A background task auto-re-enables both after the timer expires, even if you close the browser
- The UI shows individual status pills for each instance

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PIHOLE1_HOST` | `http://pi.hole` | Primary Pi-hole URL |
| `PIHOLE1_API_KEY` | *(empty)* | Primary API token |
| `PIHOLE1_NAME` | `Primary` | Display name |
| `PIHOLE2_HOST` | *(empty)* | Secondary Pi-hole URL (omit to disable) |
| `PIHOLE2_API_KEY` | *(empty)* | Secondary API token |
| `PIHOLE2_NAME` | `Secondary` | Display name |
| `DEFAULT_DISABLE_SECONDS` | `300` | Default disable duration |

## REST API

| Method | Endpoint | Body | Description |
|--------|----------|------|-------------|
| `GET`  | `/api/status` | — | Status of all instances + countdown |
| `POST` | `/api/disable` | `{"seconds": 300}` | Disable all instances |
| `POST` | `/api/enable`  | — | Re-enable all instances |

### Example response from `/api/disable`

```json
{
  "ok": true,
  "message": "Filtering disabled for 300 seconds",
  "disabled_until": "2026-05-18T14:30:00+00:00",
  "partial": false,
  "instances": [
    {"name": "Primary",   "ok": true,  "error": null},
    {"name": "Secondary", "ok": true,  "error": null}
  ]
}
```

## Finding your Pi-hole API Key

Pi-hole admin panel → **Settings** → **API / Web interface** → **API Token**
