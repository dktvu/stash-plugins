#!/usr/bin/env python3
import json, sys, urllib.error, urllib.parse, urllib.request
from stashapi.stashapp import StashInterface
from stashapi import log

SCENE_FRAGMENT = """
id
title
stash_ids { endpoint stash_id }
"""

# ---------- HTTP helpers ----------
def http_get_json(url, api_key):
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "X-Api-Key": api_key},
        method="GET",
    )
    with urllib.request.urlopen(req) as r:
        raw = r.read().decode("utf-8", "ignore")
        try:
            return r.status, json.loads(raw)
        except Exception:
            return r.status, raw

def http_post_json(url, body, api_key):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "X-Api-Key": api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read().decode("utf-8", "ignore")
            try:
                return r.status, json.loads(raw)
            except Exception:
                return r.status, raw
    except urllib.error.HTTPError as e:
        raw = (e.read() or b"").decode("utf-8", "ignore")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw


def is_already_exists_error(status, resp):
    """Return whether Whisparr rejected an add because the scene is already present.

    Whisparr may respond with HTTP 409 or 400 plus ``MovieExistsValidator``.

    Returns:
        Tuple of (is_duplicate, optional_movie_id_from_error_body).
    """
    if status == 409:
        return True, None

    if status == 400:
        errors = resp if isinstance(resp, list) else [resp] if isinstance(resp, dict) else []
        for error in errors:
            if isinstance(error, dict) and error.get("errorCode") == "MovieExistsValidator":
                return True, error.get("existingMovieId")

    return False, None


def lookup_movie_by_stash_id(whisparr_url, api_key, stashdb_id):
    """Find a Whisparr movie/scene row matching the StashDB stash_id.

    Uses ``GET /api/v3/movie?stashId=...``. Older builds may ignore the query
    param and return every movie; those responses are filtered by ``foreignId``.
    """
    encoded_id = urllib.parse.quote(stashdb_id, safe="")
    url = f"{whisparr_url}/api/v3/movie?stashId={encoded_id}"
    status, resp = http_get_json(url, api_key)

    if status != 200:
        return None

    if isinstance(resp, list):
        if len(resp) == 1:
            return resp[0]
        for movie in resp:
            if movie.get("foreignId") == stashdb_id or movie.get("stashId") == stashdb_id:
                return movie
        return None

    if isinstance(resp, dict) and resp.get("id"):
        return resp

    return None


def rescan_movie(whisparr_url, api_key, movie_id):
    """Queue a disk rescan so Whisparr picks up renamed or new files on disk."""
    url = f"{whisparr_url}/api/v3/command"
    body = {"name": "RescanMovie", "movieId": int(movie_id)}
    status, _resp = http_post_json(url, body, api_key)
    return status in (200, 201)


def trigger_rescan_for_stash_id(whisparr_url, api_key, stashdb_id, existing_movie_id=None):
    """Resolve Whisparr movie id and queue ``RescanMovie`` (used after duplicate add)."""
    movie_id = existing_movie_id
    if not movie_id:
        existing = lookup_movie_by_stash_id(whisparr_url, api_key, stashdb_id)
        movie_id = existing.get("id") if existing else None

    if not movie_id:
        log.info("Whisparr: could not resolve existing movie for rescan")
        return False

    log.info(f"Whisparr: rescanning movie id={movie_id} (stashId={stashdb_id})")
    if rescan_movie(whisparr_url, api_key, movie_id):
        log.info(f"Whisparr: RescanMovie queued for id={movie_id}")
        return True

    log.error(f"Whisparr: failed to queue RescanMovie for id={movie_id}")
    return False


def load_plugin_settings(stash: StashInterface) -> dict:
    """Return this plugin's settings using the manifest name."""
    try:
        stash_config = stash.get_configuration()
    except Exception as e:
        log.error(f"get_configuration failed: {e}")
        return {}

    plugins = (stash_config or {}).get("plugins") or {}
    settingname = "whisparr-bridge"
    cfg = plugins.get(settingname)
    if not isinstance(cfg, dict):
        log.error(f"Plugin settings not found under '{settingname}'.")
        return {}

    return cfg

# ---------- main ----------
def main():
    STASH_DATA = json.loads(sys.stdin.read())
    ARGS = STASH_DATA.get("args") or {}
    hook = ARGS.get("hookContext") or {}
    scene_id = hook.get("id")
    if not scene_id:
        log.info("No scene id in hook; exit.")
        return

    # Stash API client
    stash = StashInterface(STASH_DATA["server_connection"])

    plugin_cfg = load_plugin_settings(stash)
    if not plugin_cfg:
        return

    whisparr_url = (plugin_cfg.get("WHISPARR_URL") or "").rstrip("/")
    whisparr_key = plugin_cfg.get("WHISPARR_API_KEY") or ""
    match_substr = plugin_cfg.get("STASHDB_ENDPOINT_SUBSTR") or "stashdb.org"
    monitored = plugin_cfg.get("MONITORED", True)

    if not whisparr_url or not whisparr_key:
        log.error("Missing Whisparr settings (URL/API key).")
        return

    # Fetch scene
    try:
        scene = stash.find_scene(scene_id, fragment=SCENE_FRAGMENT)
    except Exception as e:
        log.error(f"find_scene failed: {e}")
        return

    if not scene:
        log.error(f"Scene {scene_id} not found.")
        return

    title = scene.get("title") or ""
    log.info(f"scene '{title}', id={scene_id}")

    # Extract matching StashDB id
    stashdb_id = None
    for sid in scene.get("stash_ids") or []:
        if match_substr in (sid.get("endpoint") or ""):
            stashdb_id = sid.get("stash_id")
            break

    if not stashdb_id:
        log.info("No matching StashDB id; skip.")
        return

    # Query Whisparr for defaults it requires
    s_qp, qps = http_get_json(f"{whisparr_url}/api/v3/qualityprofile", whisparr_key)
    if s_qp != 200 or not isinstance(qps, list) or not qps:
        log.error(f"Whisparr: cannot load quality profiles: {s_qp} {qps}")
        return
    quality_profile_id = int(qps[0]["id"])

    s_rf, rfs = http_get_json(f"{whisparr_url}/api/v3/rootfolder", whisparr_key)
    if s_rf != 200 or not isinstance(rfs, list) or not rfs:
        log.error(f"Whisparr: cannot load root folders: {s_rf} {rfs}")
        return
    root_folder_path = rfs[0]["path"]

    # Build add payload
    body = {
        "title": title,
        "qualityProfileId": quality_profile_id,
        "rootFolderPath": root_folder_path,
        "monitored": monitored,
        "addOptions": {
            "monitor": "movieOnly" if monitored else "none",
            "searchForMovie": False
        },
        "foreignId": stashdb_id,
        "stashId": stashdb_id
    }

    status, resp = http_post_json(f"{whisparr_url}/api/v3/movie", body, whisparr_key)
    if status in (200, 201):
        log.info(f"Whisparr add OK ({status})")
        return

    is_duplicate, movie_id_from_error = is_already_exists_error(status, resp)
    if is_duplicate:
        log.info(f"Whisparr: scene already present (HTTP {status}); scanning disk for files")
        trigger_rescan_for_stash_id(
            whisparr_url, whisparr_key, stashdb_id, existing_movie_id=movie_id_from_error
        )
        return

    log.error(f"Whisparr error {status}: {resp}")

if __name__ == "__main__":
    main()
