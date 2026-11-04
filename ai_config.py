#!/usr/bin/env python3
"""Which AI provider and model this installation talks to -- and who chose it.

Why this is its own module
--------------------------
The base URL, model name and API key used to be three module-level constants in
`analysis.py` (`LLM_BASE`, `LLM_MODEL`, `_load_key()`), which meant the assistant
only ever worked for the person who deployed it: a second user of the same
install either borrowed the owner's key or got nothing. Both chat surfaces (live
and archive) are separate processes sharing one SQLite file, so the choice has to
live in the database and be readable/writable from either -- exactly the shape
`pwa.py` uses for the manifest.

What is stored, and what is never returned
------------------------------------------
A profile is (label, base_url, model, api_key). The key is stored so the
assistant can use it, and it is **never** sent back to a browser: the read API
returns `has_key` and a 4-character hint only. A dashboard that echoes a secret
back over HTTP has published it to every device on the LAN and to anything that
caches the response.

Precedence
----------
  1. the profile marked default (chosen in the UI),
  2. `F1_AI_BASE` / `F1_AI_MODEL` / `F1_AI_KEY`, for a self-hoster who would
     rather keep the key in their own environment than in the database,
  3. nothing -- and then the chat says exactly that instead of failing obscurely.

There is no built-in default provider. The first version of this module shipped
with the deployer's own gateway as `DEFAULT_BASE`, which meant a fresh clone
either talked to a server its owner did not choose or, worse, quietly spent the
deployer's key. An open-source install must be inert until its user points it
somewhere, so an unconfigured install resolves to an empty base URL and says so.

Every profile is an OpenAI-compatible `/chat/completions` endpoint. That is the
one interface the existing call already speaks; pretending to support anything
else would need a second code path that has never been tested.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

import store

#: No provider is baked in. Empty means "the user has not chosen one yet", and
#: the UI asks for one instead of calling somewhere arbitrary. A self-hoster can
#: still pre-seed the three values from the environment.
DEFAULT_BASE = (os.environ.get("F1_AI_BASE") or "").strip().rstrip("/")
DEFAULT_MODEL = (os.environ.get("F1_AI_MODEL") or "").strip()
#: Deliberately short and generic: one name, documented, set by the person who
#: owns the key. Anything deployment-specific here would tell every reader of
#: this repository which gateway its author happens to use.
ENV_KEY_NAMES = ("F1_AI_KEY",)

#: Where the request may and may not go. `base_url` is user input and the server
#: fetches it, so it is the textbook SSRF shape: point it at a cloud metadata
#: endpoint and read the response out of the error message. Loopback stays
#: allowed on purpose -- running Ollama or llama.cpp on 127.0.0.1 is the normal
#: local-first setup, and on the user's own machine it is not an escalation.
#: RFC1918 ranges are allowed only when the operator opts in, because on a shared
#: instance they are the internal network.
_ALLOW_PRIVATE_ENV = "F1_AI_ALLOW_PRIVATE"
#: Always refused, even with the opt-in: link-local holds the metadata services
#: (169.254.169.254 and friends), and these are never a legitimate chat endpoint.
_ALWAYS_BLOCKED_HOSTS = {"169.254.169.254", "metadata.google.internal",
                         "metadata", "instance-data"}

#: Credential shapes, for the second layer of scrub(). Deliberately broad: a
#: false positive only hides an innocent token in an error message, while a
#: false negative publishes a key. The prefix families are the ones real
#: providers issue; the tail is a long unbroken run of key-ish characters,
#: which also catches a key whose middle was cut out by truncation.
_CRED_SHAPE = re.compile(
    r"(?:sk|pk|rk|api|key|token|bearer)[-_A-Za-z0-9]{12,}"
    r"|[A-Za-z0-9_\-]{32,}",
    re.IGNORECASE)

#: Keys are stored in the same SQLite file as the archive, so the file's own
#: permissions are the protection. 0600 is applied on write; the check is here
#: (not in store.py) because this is the only module that puts a secret in it.
_DB_CHMOD = 0o600


def _env_key() -> str | None:
    """The key from the environment, if the operator set one.

    Only `F1_AI_KEY`. Nothing is read from a provider-specific variable or from
    a dotfile in the user's home: that made one deployment's private naming part
    of the public source and let an install borrow a key its user never entered.
    """
    for name in ENV_KEY_NAMES:
        v = os.environ.get(name)
        if v and v.strip():
            return v.strip()
    return None


def _allow_private() -> bool:
    return (os.environ.get(_ALLOW_PRIVATE_ENV) or "").strip().lower() in ("1", "true", "yes", "on")


def check_base_url(url: str) -> str:
    """Validate a user-supplied base URL before anything fetches it.

    Returns the normalised URL or raises ValueError with a message the settings
    dialog can show verbatim. Two layers, because either alone is bypassable:

      * the literal host, so an IP in the URL is judged without a DNS lookup;
      * the resolved addresses, because `evil.example` can point anywhere.

    Scheme is restricted to http/https: `file://` would read local files and
    `ftp://` has no business here.
    """
    raw = (url or "").strip()
    if not raw:
        raise ValueError("base_url zorunlu")
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("base_url http:// ya da https:// ile başlamalı")
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise ValueError("base_url bir sunucu adı içermeli")
    if host in _ALWAYS_BLOCKED_HOSTS:
        raise ValueError("bu adres bir bulut meta veri servisi, kullanılamaz")
    if parsed.port is not None and not (0 < parsed.port < 65536):
        raise ValueError("geçersiz port")

    _check_host(host)
    # A hostname is only as honest as its DNS answer, and the answer can differ
    # between our lookup and the connection (rebinding). Checking what it
    # resolves to right now closes the common case; the request itself is still
    # made to the same validated URL.
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except OSError:
        # Unresolvable: not an SSRF question, and a typo should surface as a
        # connection error with the user's own text, not as a policy message.
        return raw.rstrip("/")
    for info in infos:
        addr = info[4][0]
        _check_host(addr)
    return raw.rstrip("/")


def _check_host(host: str) -> None:
    """Reject the address classes a chat endpoint must never be."""
    if host in _ALWAYS_BLOCKED_HOSTS:
        raise ValueError("bu adres bir bulut meta veri servisi, kullanılamaz")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return  # a name, judged by its resolved addresses above
    if ip.is_loopback:
        return  # own machine: the expected local-first target
    if ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        raise ValueError("bu IP aralığına istek atılamaz")
    if ip.is_private and not _allow_private():
        raise ValueError("özel ağ adresi reddedildi (yerel model için "
                         f"{_ALLOW_PRIVATE_ENV}=1 ayarlayın)")


def scrub(text: str, key: str | None) -> str:
    """Remove the key from anything that may be shown or logged.

    A provider can echo the credential back in an error body ("invalid api key
    sk-..."), and that string goes straight into the settings dialog and into
    the user's terminal. Anything leaving this module passes through here.

    Two layers, because the exact-value replacement alone is not enough:

      1. the stored key, replaced verbatim;
      2. a generic credential shape (`sk-...` and similar long tokens). A body
         that was truncated somewhere else -- or a provider that echoes a key
         the caller did not pass -- leaves a fragment that layer 1 cannot match.
         Layer 2 catches it anyway.
    """
    out = text or ""
    k = (key or "").strip()
    if k:
        out = out.replace(k, "«gizli»")
    return _CRED_SHAPE.sub("«gizli»", out)


def _hint(key: str) -> str:
    """A fingerprint of the key, NOT part of it.

    The first version printed the last four characters. That is a real fragment
    of the secret: it appears in screenshots, in logs, in a proxied JSON body,
    and any leaked fragment means the key must be rotated. A short SHA-256 prefix
    identifies a key just as well (same key -> same tag, different key -> almost
    certainly a different tag) while revealing nothing: you cannot reconstruct
    even one character of the secret from it.
    """
    k = (key or "").strip()
    if len(k) < 8:
        return "—"
    return "sha:" + hashlib.sha256(k.encode("utf-8")).hexdigest()[:6]


def _row_public(row: dict) -> dict:
    """A profile as the browser may see it: never the key itself."""
    return {
        "id": int(row["id"]),
        "label": row.get("label") or "",
        "base_url": row.get("base_url") or "",
        "model": row.get("model") or "",
        "has_key": bool(row.get("api_key")),
        "key_hint": _hint(row.get("api_key") or "") if row.get("api_key") else "",
        "is_default": bool(row.get("is_default")),
        "updated_at": row.get("updated_at") or "",
    }


def profiles() -> list[dict]:
    try:
        with store.db() as c:
            rows = c.execute("SELECT * FROM ai_profiles ORDER BY is_default DESC, id").fetchall()
        return [_row_public(dict(r)) for r in rows]
    except Exception:
        return []


def _default_row() -> dict | None:
    try:
        with store.db() as c:
            row = c.execute("SELECT * FROM ai_profiles WHERE is_default=1 "
                            "ORDER BY id DESC LIMIT 1").fetchone()
            if row is None:
                row = c.execute("SELECT * FROM ai_profiles ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def resolve() -> dict:
    """The provider actually used for the next call.

    `source` says where it came from ("profile" | "env" | None) so the UI can be
    honest about whether the user picked this or inherited it.
    """
    row = _default_row()
    if row and (row.get("api_key") or "").strip():
        return {"base_url": (row.get("base_url") or "").rstrip("/"),
                "model": row.get("model") or "",
                "api_key": row["api_key"].strip(),
                "label": row.get("label") or "",
                "profile_id": int(row["id"]),
                "source": "profile"}
    key = _env_key()
    if key:
        return {"base_url": DEFAULT_BASE.rstrip("/"), "model": DEFAULT_MODEL,
                "api_key": key, "label": "ortam değişkeni",
                "profile_id": None, "source": "env"}
    # No provider, on purpose: the user must pick one. `source: None` is what the
    # UI keys off to show the setup prompt instead of a broken answer.
    return {"base_url": "", "model": "", "api_key": None,
            "label": "tanımsız", "profile_id": None, "source": None}


def save_profile(*, profile_id: int | None = None, label: str, base_url: str,
                 model: str, api_key: str | None = None,
                 make_default: bool = True) -> dict:
    """Create or update a profile. An empty `api_key` keeps the stored one.

    Keeping the old key when the field is blank is deliberate: a user editing the
    model name should not have to re-paste a secret, and a form that posts an
    empty password field is the single most common way a stored credential gets
    wiped by accident.
    """
    label = (label or "").strip()[:60]
    model = (model or "").strip()[:120]
    if not model:
        raise ValueError("model zorunlu")
    base_url = check_base_url(base_url or DEFAULT_BASE)

    with store.db() as c:
        if profile_id:
            cur = c.execute("SELECT id FROM ai_profiles WHERE id=?", (int(profile_id),)).fetchone()
            if cur is None:
                raise ValueError("profil bulunamadı")
            if api_key:
                c.execute("UPDATE ai_profiles SET label=?, base_url=?, model=?, api_key=?, "
                          "updated_at=? WHERE id=?",
                          (label, base_url, model, api_key.strip(), store.now_iso(),
                           int(profile_id)))
            else:
                c.execute("UPDATE ai_profiles SET label=?, base_url=?, model=?, "
                          "updated_at=? WHERE id=?",
                          (label, base_url, model, store.now_iso(), int(profile_id)))
            pid = int(profile_id)
        else:
            cur = c.execute(
                "INSERT INTO ai_profiles(label,base_url,model,api_key,is_default,created_at,"
                "updated_at) VALUES(?,?,?,?,0,?,?)",
                (label or model, base_url, model, (api_key or "").strip(),
                 store.now_iso(), store.now_iso()))
            pid = int(cur.lastrowid)
        if make_default:
            c.execute("UPDATE ai_profiles SET is_default=0")
            c.execute("UPDATE ai_profiles SET is_default=1 WHERE id=?", (pid,))
    _tighten_db_perms()
    return next((p for p in profiles() if p["id"] == pid), {"id": pid})


def delete_profile(pid: int) -> bool:
    with store.db() as c:
        row = c.execute("SELECT is_default FROM ai_profiles WHERE id=?", (int(pid),)).fetchone()
        if row is None:
            return False
        c.execute("DELETE FROM ai_profiles WHERE id=?", (int(pid),))
        if row["is_default"]:
            # Promote another profile rather than leaving the install with none:
            # "default" being unset silently falls back to the server key, which
            # looks like the deletion did not work.
            nxt = c.execute("SELECT id FROM ai_profiles ORDER BY id LIMIT 1").fetchone()
            if nxt is not None:
                c.execute("UPDATE ai_profiles SET is_default=1 WHERE id=?", (nxt["id"],))
    return True


def set_default(pid: int) -> bool:
    with store.db() as c:
        if c.execute("SELECT id FROM ai_profiles WHERE id=?", (int(pid),)).fetchone() is None:
            return False
        c.execute("UPDATE ai_profiles SET is_default=0")
        c.execute("UPDATE ai_profiles SET is_default=1 WHERE id=?", (int(pid),))
    return True


def _tighten_db_perms() -> None:
    """A key now lives in this file: make it owner-only.

    `-wal` and `-shm` matter as much as the main file. The database runs in WAL
    mode, so a freshly written key lands in the `-wal` sidecar first and may sit
    there until the next checkpoint: chmodding only `f1dash.db` leaves the secret
    world-readable in a sibling file. The containing directory is tightened too,
    otherwise another local user can recreate a file inside it.

    Best effort -- the files may be on a filesystem that ignores modes, and the
    archive must keep working either way. Never raises.
    """
    for path, mode in ((store.DB_PATH, _DB_CHMOD),
                       (store.DB_PATH + "-wal", _DB_CHMOD),
                       (store.DB_PATH + "-shm", _DB_CHMOD),
                       (os.path.dirname(store.DB_PATH) or ".", 0o700)):
        try:
            os.chmod(path, mode)
        except OSError:
            pass


def probe(*, base_url: str, model: str, api_key: str | None = None,
          timeout: float = 20.0) -> dict:
    """One tiny round trip to prove the settings work, before saving them.

    Saves the user from discovering a typo on their first real question, which
    costs a 6-110 second wait and returns an opaque provider error. A one-token
    completion is the cheapest call every OpenAI-compatible endpoint supports.
    """
    key = (api_key or "").strip()
    if not key:
        resolved = resolve()
        key = resolved.get("api_key") or ""
    if not key:
        return {"ok": False, "error": "API anahtarı yok"}
    try:
        safe_base = check_base_url(base_url)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    if not (model or "").strip():
        return {"ok": False, "error": "model zorunlu"}
    url = safe_base + "/chat/completions"
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": "ping"}],
                       "max_tokens": 1, "stream": False}).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
        d = json.loads(raw)
        ok = bool((d.get("choices") or []))
        return {"ok": ok, "model_echo": d.get("model"),
                "error": None if ok else "cevap 'choices' taşımıyor"}
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            # Read generously and scrub BEFORE truncating. Slicing first can cut
            # a key in half, and a partial credential no longer matches the
            # exact-value replacement in scrub(), so the fragment survives.
            detail = scrub(e.read().decode("utf-8", "replace"), key)[:200]
        except Exception:
            pass
        # The body may quote the key back ("Incorrect API key provided: sk-...").
        return {"ok": False, "error": scrub(f"HTTP {e.code} {detail}".strip(), key)}
    except Exception as e:
        return {"ok": False, "error": scrub(f"{type(e).__name__}: {e}"[:200], key)}


def public_state() -> dict:
    """What the settings dialog needs: profiles (keyless) plus the active one."""
    r = resolve()
    return {
        "profiles": profiles(),
        "active": {
            "label": r["label"], "base_url": r["base_url"], "model": r["model"],
            "source": r["source"], "profile_id": r["profile_id"],
            "has_key": bool(r["api_key"]), "key_hint": _hint(r["api_key"] or ""),
        },
        "env_default": {"base_url": DEFAULT_BASE, "model": DEFAULT_MODEL,
                        "key_present": bool(_env_key())},
    }


def install(app: FastAPI) -> None:
    """Adds the /api/ai routes to a surface.

    Mounted on BOTH chat surfaces (live and archive) because each is its own
    process: a setting saved in one is invisible to the other unless both can
    read and write the same rows, which they do through the shared database.
    """

    @app.get("/api/ai")
    async def api_ai():
        return JSONResponse(public_state())

    @app.post("/api/ai")
    async def api_ai_save(request: Request):
        body = await request.json()
        try:
            saved = save_profile(
                profile_id=body.get("id"),
                label=body.get("label") or "",
                base_url=body.get("base_url") or DEFAULT_BASE,
                model=body.get("model") or "",
                api_key=body.get("api_key") or None,
                make_default=bool(body.get("make_default", True)))
        except ValueError as e:
            return JSONResponse({"ok": False, "reason": str(e)}, status_code=400)
        return JSONResponse({"ok": True, "saved": saved, "state": public_state()})

    @app.post("/api/ai/default")
    async def api_ai_default(request: Request):
        body = await request.json()
        try:
            ok = set_default(int(body.get("id")))
        except (TypeError, ValueError):
            ok = False
        if not ok:
            return JSONResponse({"ok": False, "reason": "profil bulunamadı"}, status_code=404)
        return JSONResponse({"ok": True, "state": public_state()})

    @app.delete("/api/ai")
    async def api_ai_delete(pid: int):
        if not delete_profile(int(pid)):
            return JSONResponse({"ok": False, "reason": "profil bulunamadı"}, status_code=404)
        return JSONResponse({"ok": True, "state": public_state()})

    @app.post("/api/ai/test")
    async def api_ai_test(request: Request):
        body = await request.json()
        return JSONResponse(probe(
            base_url=body.get("base_url") or DEFAULT_BASE,
            model=body.get("model") or "",
            api_key=body.get("api_key") or None))
