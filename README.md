# Pi-hole Controller (Multi-Instance)

A REST API + web UI for simultaneously disabling/re-enabling filtering across **multiple Pi-hole instances** in parallel.

## Setup

You will need to create a file containing the following configuration for each cluster. The default naming pattern is `instances.json`, but there is also an environment variable `INSTANCES_CONFIGURATION_FILE` that can be set to any file location.

```json
[
  {
    "host": "http://ServerFQDNorIP",
    "password": "PiHole API Key",
    "name": "Friendly Name"
  },{
    "host": "https://10.0.0.10:8443",
    "password": "PiHole API Key",
    "name": "A Different Friendly Name"
  }
  <etc...>
]
```

## How it works

- `POST /api/disable` fans out to **all instances simultaneously** using `asyncio.gather`
- If one instance fails, the others still gets disabled — the response includes a `partial: true` flag and lists which failed
- A background task auto-re-enables both after the timer expires, even if you close the browser
- The UI shows individual status pills for each instance

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DEFAULT_DISABLE_SECONDS` | `300` | Default time that will be used when `seconds` is not provided |
| `INSTANCES_CONFIGURATION_FILE` | *instances.json* | File containing Instance configuration information |

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
