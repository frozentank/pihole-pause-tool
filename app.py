"""
Pi-hole Controller — v6 API with session-based authentication.

Auth flow per https://docs.pi-hole.net/api/auth/:
  1. POST /api/auth {"password": "..."} -> receive SID
  2. Pass SID via X-FTL-SID header on every subsequent request
  3. DELETE /api/auth to log out when done
"""

import os
import asyncio
import httpx
import logging
import json
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Configuration — override via environment variables
# ---------------------------------------------------------------------------
DEFAULT_DISABLE_SECONDS = int(os.getenv("DEFAULT_DISABLE_SECONDS", "300"))

INSTANCES_CONFIGURATION_FILE = os.getenv("INSTANCES_CONFIGURATION_FILE", "instances.json")

with open(INSTANCES_CONFIGURATION_FILE) as f:
    INSTANCES = json.load(f)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Pi-hole Controller",
    description="Temporarily disable/enable Pi-hole filtering (v6 API)",
    version="3.0.0",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ---------------------------------------------------------------------------
# Get Logging components
# ---------------------------------------------------------------------------
#logging.basicConfig(level=logging.INFO)

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------
_re_enable_task:      Optional[asyncio.Task] = None
_disabled_until:      Optional[datetime]     = None
_disabled_for_seconds: Optional[int]         = None


# ---------------------------------------------------------------------------
# Pi-hole v6 session helpers
# ---------------------------------------------------------------------------
async def _login(client: httpx.AsyncClient, host: str, password: str) -> tuple[str, str]:
    """
    POST /api/auth and return (sid, csrf).
    Raises RuntimeError on failure.
    """
    url  = f"{host.rstrip('/')}/api/auth"
    resp = await client.post(url, json={"password": password}, timeout=10)

    if resp.status_code == 401:
        raise RuntimeError(f"Authentication failed for {host}: wrong password")
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Auth error {resp.status_code} from {host}: {resp.text[:200]}")

    data    = resp.json()
    session = data.get("session", {})

    if not session.get("valid", False):
        raise RuntimeError(f"Session not valid for {host}")

    sid  = session.get("sid",  "")
    csrf = session.get("csrf", "")
    return sid, csrf


async def _logout(client: httpx.AsyncClient, host: str, sid: str) -> None:
    """DELETE /api/auth to cleanly close the session."""
    try:
        url = f"{host.rstrip('/')}/api/auth"
        await client.delete(url, headers={"X-FTL-SID": sid}, timeout=10)
    except Exception:
        pass  # best-effort


def _auth_headers(sid: str, csrf: str = "") -> dict:
    h = {"X-FTL-SID": sid}
    if csrf:
        h["X-FTL-CSRF"] = csrf
    return h


# ---------------------------------------------------------------------------
# Per-instance operations (each opens its own short-lived session)
# ---------------------------------------------------------------------------
async def _instance_get_status(inst: dict) -> dict:
    """Fetch blocking status for one instance. Never raises."""
    host, password, name = inst["host"], inst["password"], inst["name"]
    try:
        async with httpx.AsyncClient(verify=False) as client:
            sid, csrf = await _login(client, host, password)
            try:
                url  = f"{host.rstrip('/')}/api/dns/blocking"
                resp = await client.get(url, headers=_auth_headers(sid, csrf), timeout=10)
                resp.raise_for_status()
                status = resp.json().get("blocking", None)
                #status   = "enabled" if blocking else "disabled"
            finally:
                await _logout(client, host, sid)

        return {"name": name, "host": host, "status": status, "ok": True, "error": None}
    except Exception as exc:
        return {"name": name, "host": host, "status": "unreachable", "ok": False, "error": str(exc)}


async def _instance_disable(inst: dict, seconds: int) -> dict:
    """Disable filtering on one instance for `seconds` seconds. Never raises."""
    host, password, name = inst["host"], inst["password"], inst["name"]
    try:
        async with httpx.AsyncClient(verify=False) as client:
            sid, csrf = await _login(client, host, password)
            try:
                url  = f"{host.rstrip('/')}/api/dns/blocking"
                body = {"blocking": False, "timer": seconds}
                resp = await client.post(url, json=body, headers=_auth_headers(sid, csrf), timeout=10)
                resp.raise_for_status()
            finally:
                await _logout(client, host, sid)

        return {"name": name, "ok": True, "error": None}
    except Exception as exc:
        return {"name": name, "ok": False, "error": str(exc)}


async def _instance_enable(inst: dict) -> dict:
    """Enable filtering on one instance. Never raises."""
    host, password, name = inst["host"], inst["password"], inst["name"]
    try:
        async with httpx.AsyncClient(verify=False) as client:
            sid, csrf = await _login(client, host, password)
            try:
                url  = f"{host.rstrip('/')}/api/dns/blocking"
                resp = await client.post(url, json={"blocking": True}, headers=_auth_headers(sid, csrf), timeout=10)
                resp.raise_for_status()
            finally:
                await _logout(client, host, sid)

        return {"name": name, "ok": True, "error": None}
    except Exception as exc:
        return {"name": name, "ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Fan-out (all instances in parallel)
# ---------------------------------------------------------------------------
async def _all_statuses() -> list[dict]:
    return list(await asyncio.gather(*[_instance_get_status(i) for i in INSTANCES]))

async def _all_disable(seconds: int) -> list[dict]:
    return list(await asyncio.gather(*[_instance_disable(i, seconds) for i in INSTANCES]))

async def _all_enable() -> list[dict]:
    return list(await asyncio.gather(*[_instance_enable(i) for i in INSTANCES]))


# ---------------------------------------------------------------------------
# Background re-enable task
# ---------------------------------------------------------------------------
async def _schedule_re_enable(seconds: int) -> None:
    global _re_enable_task, _disabled_until, _disabled_for_seconds
    await asyncio.sleep(seconds)
    try:
        await _all_enable()
    finally:
        _disabled_until       = None
        _disabled_for_seconds = None
        _re_enable_task       = None


def _cancel_pending_task() -> None:
    global _re_enable_task
    if _re_enable_task and not _re_enable_task.done():
        _re_enable_task.cancel()
    _re_enable_task = None


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class DisableRequest(BaseModel):
    seconds: int = Field(
        default=DEFAULT_DISABLE_SECONDS,
        ge=1,
        le=86400,
        description="How many seconds to disable filtering (1-86400)",
    )


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------
@app.get("/api/status", tags=["Control"])
async def get_status():
    """Return filtering status for all configured Pi-hole instances."""
    results = await _all_statuses()

    now = datetime.now(timezone.utc)
    seconds_remaining = None
    if _disabled_until:
        delta = (_disabled_until - now).total_seconds()
        seconds_remaining = max(0, int(delta))

    any_disabled = any(r["status"] == "disabled" for r in results if r["ok"])

    return {
        "filtering_disabled": any_disabled,
        "disabled_until":     _disabled_until.isoformat() if _disabled_until else None,
        "seconds_remaining":  seconds_remaining,
        "instances":          results,
    }


@app.post("/api/disable", tags=["Control"])
async def disable_filtering(body: DisableRequest):
    """Disable filtering on all Pi-hole instances simultaneously."""
    global _re_enable_task, _disabled_until, _disabled_for_seconds

    _cancel_pending_task()

    results   = await _all_disable(body.seconds)
    successes = [r for r in results if r["ok"]]
    failures  = [r for r in results if not r["ok"]]

    if not successes:
        raise HTTPException(502, detail={"message": "All Pi-hole instances failed", "instances": results})

    _disabled_until       = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=body.seconds)
    _disabled_for_seconds = body.seconds

    loop = asyncio.get_event_loop()
    _re_enable_task = loop.create_task(_schedule_re_enable(body.seconds))

    return {
        "ok":             True,
        "message":        f"Filtering disabled for {body.seconds} seconds",
        "disabled_until": _disabled_until.isoformat(),
        "instances":      results,
        "partial":        len(failures) > 0,
        "failures":       failures if failures else None,
    }


@app.post("/api/enable", tags=["Control"])
async def enable_filtering():
    """Re-enable filtering on all Pi-hole instances immediately."""
    global _disabled_until, _disabled_for_seconds

    _cancel_pending_task()

    results   = await _all_enable()
    successes = [r for r in results if r["ok"]]
    failures  = [r for r in results if not r["ok"]]

    if not successes:
        raise HTTPException(502, detail={"message": "All Pi-hole instances failed", "instances": results})

    _disabled_until       = None
    _disabled_for_seconds = None



    return {
        "ok":       True,
        "message":  "Pi-hole filtering re-enabled",
        "instances": results,
        "partial":  len(failures) > 0,
        "failures": failures if failures else None,
    }

# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/api/health")
async def health_check():
    return {"status": "healthy"}

# ---------------------------------------------------------------------------
# Web UI
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def serve_ui():
    with open(os.path.join(os.path.dirname(__file__), "ui.html")) as f:
        return f.read()

@app.get('/favicon.ico', include_in_schema=False)
async def favicon():
    return FileResponse('favicon.ico')

@app.get('/index.css', include_in_schema=False)
async def serve_css():
    return FileResponse('index.css')

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8081, reload=False)
