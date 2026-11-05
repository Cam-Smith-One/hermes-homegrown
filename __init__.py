"""Homegrown: Hermes builds small apps that stay. You use them in Desktop; Hermes uses their data from anywhere."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .store import Store, StoreError

logger = logging.getLogger(__name__)

PLUGIN_ID = "homegrown"
TOOLSET = "homegrown"


def _store() -> Store:
    # Resolved per call: the data dir follows the active profile.
    from plugins.plugin_storage import plugin_data_dir
    return Store(plugin_data_dir(PLUGIN_ID))


def notify(event: str, payload: dict) -> None:
    """Tell open Desktop windows to refresh. A no-op where no Desktop is attached (cron, CLI, Telegram)."""
    try:
        from hermes_cli.plugin_events import broadcast_plugin_event
        broadcast_plugin_event(PLUGIN_ID, event, payload)
    except Exception:  # why: refresh is best-effort; the write itself already succeeded
        logger.debug("homegrown: broadcast %s failed", event, exc_info=True)


def directive(slug: str) -> str:
    return f'::homegrown-app{{slug="{slug}"}}'


def _ok(**result) -> str:
    return json.dumps(result, default=str)


def _guard(fn):
    def handler(args: dict, **kwargs) -> str:
        try:
            return fn(args or {})
        except StoreError as e:
            return json.dumps({"error": str(e)})
        except Exception as e:  # why: a tool must return JSON, never raise into the agent loop
            logger.exception("homegrown tool failed")
            return json.dumps({"error": f"{type(e).__name__}: {e}"})
    return handler


# Models otherwise rebuild the app as a static preview widget, which shows stale data and can't be used.
HOW_TO_SHOW = ("To show an app live in Hermes Desktop, put its show_in_desktop line in your reply as its own "
               "paragraph, exactly as given. Never rebuild an app as a preview or widget; on messaging platforms, "
               "say it is on the Apps page in Hermes Desktop instead.")


def _summary(meta: dict) -> dict:
    return {**{k: meta[k] for k in ("slug", "name", "description", "version")}, "show_in_desktop": directive(meta["slug"])}


@_guard
def create_app(args):
    meta = _store().create(args.get("name", ""), args.get("html", ""), args.get("description", ""))
    notify("app.updated", {"slug": meta["slug"]})
    return _ok(app=_summary(meta), how_to_show=HOW_TO_SHOW)


@_guard
def update_app(args):
    s, slug = _store(), args.get("slug", "")
    if args.get("restore_version"):
        meta = s.restore(slug, args["restore_version"])
    else:
        meta = s.update(slug, html=args.get("html"), name=args.get("name"),
                        description=args.get("description"), note=args.get("note", ""))
    notify("app.updated", {"slug": slug})
    return _ok(app=_summary(meta), history=meta["history"][-5:], how_to_show=HOW_TO_SHOW)


@_guard
def list_apps(args):
    s = _store()
    return _ok(apps=[{**_summary(m), "collections": s.collections(m["slug"])} for m in s.list()], how_to_show=HOW_TO_SHOW)


@_guard
def read_app(args):
    app = _store().get(args.get("slug", ""))
    if not args.get("include_html", True):
        app.pop("html")
    return _ok(app={**app, "show_in_desktop": directive(app["slug"])}, how_to_show=HOW_TO_SHOW)


@_guard
def app_data(args):
    s, slug, action, collection = _store(), args.get("slug", ""), args.get("action"), args.get("collection", "")
    if action == "list":
        return _ok(records=s.rows(slug, collection, args.get("where"), args.get("limit", 200)))
    if action == "add":
        rows = args.get("records") or [args.get("data")]
        out = [s.add(slug, collection, r) for r in rows]
    elif action == "update":
        out = [s.update_row(slug, collection, args.get("id", ""), args.get("data"))]
    elif action == "delete":
        s.remove_row(slug, collection, args.get("id", ""))
        out = []
    else:
        raise StoreError("action must be one of list, add, update, delete")
    notify("data.changed", {"slug": slug, "collection": collection})
    return _ok(ok=True, records=out)


@_guard
def delete_app(args):
    where = _store().delete(args.get("slug", ""))
    notify("app.updated", {"slug": args.get("slug")})
    return _ok(ok=True, moved_to=where, note="Recoverable: move the folder back into apps/ to restore it.")


_SLUG = {"type": "string", "description": "The app's slug, from homegrown_list_apps or the create result."}

TOOLS = [
    (create_app, {
        "name": "homegrown_create_app",
        "description": (
            "Build a new small app that persists: a tracker, log, CRM, checklist, dashboard or tool the user "
            "will come back to. Provide one self-contained HTML document (inline CSS/JS, no external URLs, no "
            "network). The app stores its data with the window.app API: load skill_view('homegrown:build-app') "
            "before writing the html. Returns the slug and a Desktop directive to show it inline."),
        "parameters": {"type": "object", "required": ["name", "html"], "properties": {
            "name": {"type": "string", "description": "Short human name, e.g. 'Expense tracker'."},
            "description": {"type": "string", "description": "One sentence on what it is for."},
            "html": {"type": "string", "description": "The complete HTML document."}}},
    }),
    (update_app, {
        "name": "homegrown_update_app",
        "description": (
            "Change an existing app. Pass new full html to publish a new version (old versions are kept), "
            "or restore_version to roll back. Read the app first with homegrown_read_app. The app's data is "
            "untouched either way."),
        "parameters": {"type": "object", "required": ["slug"], "properties": {
            "slug": _SLUG,
            "html": {"type": "string", "description": "Complete replacement HTML document."},
            "note": {"type": "string", "description": "What changed, for the version history."},
            "name": {"type": "string"}, "description": {"type": "string"},
            "restore_version": {"type": "integer", "description": "Version number to roll back to."}}},
    }),
    (list_apps, {
        "name": "homegrown_list_apps",
        "description": "List the user's apps with their collections and record counts. Use this to find an app the user mentions, and to show one: each app has a show_in_desktop line that displays it live in Desktop.",
        "parameters": {"type": "object", "properties": {}},
    }),
    (read_app, {
        "name": "homegrown_read_app",
        "description": "Read one app: metadata, version history, collections and its current HTML.",
        "parameters": {"type": "object", "required": ["slug"], "properties": {
            "slug": _SLUG,
            "include_html": {"type": "boolean", "description": "Default true. False for metadata only."}}},
    }),
    (app_data, {
        "name": "homegrown_app_data",
        "description": (
            "Read or change the records an app stores, so you can act on the user's apps from any chat "
            "(e.g. 'log $12 coffee' adds a record to their expense tracker; 'what did I spend this week' lists "
            "them). Records are JSON objects in named collections; each gets id, created_at and updated_at. "
            "Open copies of the app in Desktop refresh live."),
        "parameters": {"type": "object", "required": ["slug", "action", "collection"], "properties": {
            "slug": _SLUG,
            "action": {"type": "string", "enum": ["list", "add", "update", "delete"]},
            "collection": {"type": "string", "description": "Collection name the app uses, e.g. 'expenses'."},
            "data": {"type": "object", "description": "add: the record. update: fields to merge in."},
            "records": {"type": "array", "items": {"type": "object"}, "description": "add: several records at once."},
            "id": {"type": "string", "description": "update/delete: the record id."},
            "where": {"type": "object", "description": "list: only records whose fields equal these values."},
            "limit": {"type": "integer", "description": "list: newest N records (default 200)."}}},
    }),
    (delete_app, {
        "name": "homegrown_delete_app",
        "description": "Remove an app and its data from the user's apps (moved to trash, recoverable). Confirm with the user first.",
        "parameters": {"type": "object", "required": ["slug"], "properties": {"slug": _SLUG}},
    }),
]


def register(ctx):
    for handler, schema in TOOLS:
        ctx.register_tool(name=schema["name"], toolset=TOOLSET, schema=schema, handler=handler, emoji="🌱")
    ctx.register_skill("build-app", Path(__file__).parent / "skills" / "build-app" / "SKILL.md")
