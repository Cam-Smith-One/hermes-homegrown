"""Homegrown backend, mounted at /api/plugins/homegrown/. Serves app documents and each app's own data
to the Desktop half; the Desktop half scopes every frame to the one app it is showing."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException

router = APIRouter()

# plugin_api.py is imported by file path, not as part of the package, so load the sibling store directly.
_spec = importlib.util.spec_from_file_location("homegrown_store", Path(__file__).resolve().parent.parent / "store.py")
_store_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_store_mod)
StoreError = _store_mod.StoreError


def _store():
    from plugins.plugin_storage import plugin_data_dir
    return _store_mod.Store(plugin_data_dir("homegrown"))


def _notify(slug: str, collection: str) -> None:
    try:
        from hermes_cli.plugin_events import broadcast_plugin_event
        broadcast_plugin_event("homegrown", "data.changed", {"slug": slug, "collection": collection})
    except Exception:  # why: refresh is best-effort; the write already succeeded
        pass


def _call(fn, *args):
    try:
        return fn(*args)
    except StoreError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/apps")
async def apps():
    s = _store()
    return {"apps": [{**m, "collections": s.collections(m["slug"])} for m in s.list()]}


@router.get("/apps/{slug}/document")
async def document(slug: str):
    s = _store()
    meta = _call(s.get, slug)
    return {"slug": slug, "name": meta["name"], "version": meta["version"], "html": _store_mod.inject_runtime(meta["html"])}


@router.post("/apps/{slug}/data/{collection}/query")
async def query(slug: str, collection: str, body: dict = Body(default={})):
    return {"records": _call(_store().rows, slug, collection, body.get("where"), body.get("limit", 500))}


@router.post("/apps/{slug}/data/{collection}")
async def add(slug: str, collection: str, body: dict = Body(...)):
    rec = _call(_store().add, slug, collection, body.get("data"))
    _notify(slug, collection)
    return {"record": rec}


@router.patch("/apps/{slug}/data/{collection}/{rid}")
async def update(slug: str, collection: str, rid: str, body: dict = Body(...)):
    rec = _call(_store().update_row, slug, collection, rid, body.get("data"))
    _notify(slug, collection)
    return {"record": rec}


@router.delete("/apps/{slug}/data/{collection}/{rid}")
async def remove(slug: str, collection: str, rid: str):
    _call(_store().remove_row, slug, collection, rid)
    _notify(slug, collection)
    return {"ok": True}
