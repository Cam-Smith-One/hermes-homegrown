"""Homegrown app store: app files, versions and per-app data, all plain files under one root.

Layout (backup = copy the folder):
    <root>/apps/<slug>/app.json          metadata + version history
    <root>/apps/<slug>/versions/<n>.html every version ever written
    <root>/apps/<slug>/data.db           the app's records (SQLite, WAL)
    <root>/trash/<slug>-<ts>/            deleted apps, recoverable by moving back

Stdlib only so it can be tested without Hermes.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import time
import uuid
from pathlib import Path

MAX_HTML_BYTES = 1_000_000
MAX_RECORD_BYTES = 100_000
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
_COLLECTION_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# The app runs in an opaque-origin sandboxed frame. This policy also takes its network away:
# everything it stores or reads goes through window.app, scoped to this app by the Desktop half.
CSP = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "img-src data: blob:; font-src data:; media-src data: blob:; form-action 'none'; base-uri 'none'"
)

RUNTIME_JS = """(()=>{let n=0;const pending=new Map(),subs=[];
addEventListener('message',e=>{if(e.source!==parent)return;const m=e.data;if(!m||typeof m!=='object')return;
if(m.type==='homegrown:reply'){const p=pending.get(m.id);if(!p)return;pending.delete(m.id);m.ok?p[0](m.result):p[1](new Error(m.error||'failed'))}
else if(m.type==='homegrown:changed'){for(const f of subs){try{f(m.collection)}catch(err){console.error(err)}}}});
const call=(op,args)=>new Promise((res,rej)=>{const id=++n;pending.set(id,[res,rej]);parent.postMessage({type:'homegrown',id,op,args},'*');
setTimeout(()=>{if(pending.delete(id))rej(new Error('Homegrown did not answer'))},15000)});
window.app=Object.freeze({
list:(collection,opts={})=>call('list',{collection,where:opts.where,limit:opts.limit}),
add:(collection,data)=>call('add',{collection,data}),
update:(collection,id,data)=>call('update',{collection,id,data}),
remove:(collection,id)=>call('remove',{collection,id}),
onChange:f=>{subs.push(f);return()=>{const i=subs.indexOf(f);if(i>=0)subs.splice(i,1)}},
ask:text=>call('ask',{text:String(text)})});})();"""


class StoreError(ValueError):
    """A request the store refuses; the message is safe to show the model."""


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40].strip("-")
    return slug or "app"


def inject_runtime(html: str) -> str:
    """Put the CSP and window.app runtime first in <head>, after any doctype so standards mode holds."""
    prelude = f'<meta http-equiv="Content-Security-Policy" content="{CSP}"><script>{RUNTIME_JS}</script>'
    head = re.search(r"<head[^>]*>", html, re.I)
    if head:
        return html[: head.end()] + prelude + html[head.end():]
    doctype = re.match(r"\s*<!doctype[^>]*>", html, re.I)
    at = doctype.end() if doctype else 0
    return html[:at] + prelude + html[at:]


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.apps = self.root / "apps"

    # ---- apps -------------------------------------------------------------

    def _dir(self, slug: str) -> Path:
        if not isinstance(slug, str) or not _SLUG_RE.fullmatch(slug):
            raise StoreError(f"invalid app slug: {slug!r}")
        d = self.apps / slug
        if not (d / "app.json").is_file():
            raise StoreError(f"no app named {slug!r}; call homegrown_list_apps to see what exists")
        return d

    def _meta(self, d: Path) -> dict:
        return json.loads((d / "app.json").read_text("utf-8"))

    def _write_meta(self, d: Path, meta: dict) -> None:
        tmp = d / "app.json.tmp"
        tmp.write_text(json.dumps(meta, indent=2), "utf-8")
        tmp.replace(d / "app.json")

    @staticmethod
    def _check_html(html: str) -> None:
        if not isinstance(html, str) or not html.strip():
            raise StoreError("html is empty")
        if len(html.encode("utf-8")) > MAX_HTML_BYTES:
            raise StoreError(f"html is over {MAX_HTML_BYTES} bytes; keep apps self-contained and small")

    def create(self, name: str, html: str, description: str = "") -> dict:
        name = (name or "").strip()
        if not name:
            raise StoreError("name is required")
        self._check_html(html)
        base = slugify(name)
        slug, i = base, 2
        while (self.apps / slug).exists():
            slug, i = f"{base}-{i}", i + 1
        d = self.apps / slug
        (d / "versions").mkdir(parents=True)
        now = time.time()
        (d / "versions" / "1.html").write_text(html, "utf-8")
        meta = {"slug": slug, "name": name, "description": description.strip(), "version": 1,
                "created": now, "updated": now, "history": [{"version": 1, "at": now, "note": "created"}]}
        self._write_meta(d, meta)
        return meta

    def update(self, slug: str, html: str | None = None, name: str | None = None,
               description: str | None = None, note: str = "") -> dict:
        d = self._dir(slug)
        meta = self._meta(d)
        if html is not None:
            self._check_html(html)
            v = max(h["version"] for h in meta["history"]) + 1
            (d / "versions" / f"{v}.html").write_text(html, "utf-8")
            meta["version"] = v
            meta["history"].append({"version": v, "at": time.time(), "note": note or "updated"})
        if name:
            meta["name"] = name.strip()
        if description is not None:
            meta["description"] = description.strip()
        meta["updated"] = time.time()
        self._write_meta(d, meta)
        return meta

    def restore(self, slug: str, version: int) -> dict:
        """Roll back by re-publishing an old version as the newest one, so nothing is lost."""
        d = self._dir(slug)
        src = d / "versions" / f"{int(version)}.html"
        if not src.is_file():
            raise StoreError(f"{slug} has no version {version}")
        return self.update(slug, html=src.read_text("utf-8"), note=f"restored version {int(version)}")

    def list(self) -> list[dict]:
        if not self.apps.is_dir():
            return []
        metas = [self._meta(d) for d in self.apps.iterdir() if (d / "app.json").is_file()]
        return sorted(metas, key=lambda m: m["updated"], reverse=True)

    def get(self, slug: str) -> dict:
        d = self._dir(slug)
        meta = self._meta(d)
        html = (d / "versions" / f"{meta['version']}.html").read_text("utf-8")
        return {**meta, "html": html, "collections": self.collections(slug)}

    def document(self, slug: str) -> str:
        return inject_runtime(self.get(slug)["html"])

    def delete(self, slug: str) -> str:
        d = self._dir(slug)
        dest = self.root / "trash" / f"{slug}-{int(time.time())}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(d), dest)
        return str(dest)

    # ---- data -------------------------------------------------------------

    def _db(self, slug: str) -> sqlite3.Connection:
        conn = sqlite3.connect(self._dir(slug) / "data.db", timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, collection TEXT NOT NULL,"
                     " data TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL)")
        conn.execute("CREATE INDEX IF NOT EXISTS records_collection ON records (collection, created)")
        return conn

    @staticmethod
    def _collection(c: str) -> str:
        if not isinstance(c, str) or not _COLLECTION_RE.fullmatch(c):
            raise StoreError(f"invalid collection name: {c!r} (letters, digits, _ and - only)")
        return c

    @staticmethod
    def _payload(data) -> dict:
        if not isinstance(data, dict):
            raise StoreError("data must be an object")
        data = {k: v for k, v in data.items() if k not in ("id", "created_at", "updated_at")}
        if len(json.dumps(data)) > MAX_RECORD_BYTES:
            raise StoreError(f"record is over {MAX_RECORD_BYTES} bytes")
        return data

    @staticmethod
    def _row(r) -> dict:
        return {"id": r[0], **json.loads(r[1]), "created_at": r[2], "updated_at": r[3]}

    def rows(self, slug: str, collection: str, where: dict | None = None, limit: int = 500) -> list[dict]:
        limit = max(1, min(int(limit or 500), 5000))
        with self._db(slug) as conn:
            cur = conn.execute("SELECT id, data, created, updated FROM records WHERE collection=? ORDER BY created",
                               (self._collection(collection),))
            out = [self._row(r) for r in cur]
        # ponytail: equality filter in Python over one app's records; push into json_extract if apps grow past ~10k rows
        if where:
            if not isinstance(where, dict):
                raise StoreError("where must be an object of field: value")
            out = [r for r in out if all(r.get(k) == v for k, v in where.items())]
        return out[-limit:]

    def add(self, slug: str, collection: str, data: dict) -> dict:
        data, now, rid = self._payload(data), time.time(), uuid.uuid4().hex[:12]
        with self._db(slug) as conn:
            conn.execute("INSERT INTO records VALUES (?,?,?,?,?)",
                         (rid, self._collection(collection), json.dumps(data), now, now))
        return {"id": rid, **data, "created_at": now, "updated_at": now}

    def update_row(self, slug: str, collection: str, rid: str, data: dict) -> dict:
        """Merge fields into one record."""
        data = self._payload(data)
        with self._db(slug) as conn:
            r = conn.execute("SELECT id, data, created, updated FROM records WHERE id=? AND collection=?",
                             (rid, self._collection(collection))).fetchone()
            if not r:
                raise StoreError(f"no record {rid!r} in {collection}")
            merged, now = {**json.loads(r[1]), **data}, time.time()
            conn.execute("UPDATE records SET data=?, updated=? WHERE id=?", (json.dumps(merged), now, rid))
        return {"id": rid, **merged, "created_at": r[2], "updated_at": now}

    def remove_row(self, slug: str, collection: str, rid: str) -> bool:
        with self._db(slug) as conn:
            cur = conn.execute("DELETE FROM records WHERE id=? AND collection=?", (rid, self._collection(collection)))
        if not cur.rowcount:
            raise StoreError(f"no record {rid!r} in {collection}")
        return True

    def collections(self, slug: str) -> dict[str, int]:
        with self._db(slug) as conn:
            return dict(conn.execute("SELECT collection, COUNT(*) FROM records GROUP BY collection"))
