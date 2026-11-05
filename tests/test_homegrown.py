"""Self-check for the store and tool handlers. Runs without Hermes: python3 tests/test_homegrown.py"""

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

import homegrown  # noqa: E402
from homegrown.store import CSP, Store, StoreError, inject_runtime  # noqa: E402

HTML = "<!doctype html><html><head><title>x</title></head><body>hi</body></html>"


def raises(fn, *a):
    try:
        fn(*a)
    except StoreError:
        return True
    return False


def test_store(root: Path):
    s = Store(root)
    a = s.create("Expense Tracker!", HTML, "money")
    assert a["slug"] == "expense-tracker" and a["version"] == 1
    assert s.create("Expense tracker", HTML)["slug"] == "expense-tracker-2"

    # versions: update appends, restore re-publishes the old one as newest
    s.update("expense-tracker", html=HTML.replace("hi", "v2"), note="v2")
    assert "v2" in s.get("expense-tracker")["html"]
    m = s.restore("expense-tracker", 1)
    assert m["version"] == 3 and "hi" in s.get("expense-tracker")["html"]

    # data
    r = s.add("expense-tracker", "expenses", {"amount": 12, "cat": "food", "id": "spoofed"})
    assert r["id"] != "spoofed" and r["amount"] == 12
    s.add("expense-tracker", "expenses", {"amount": 5, "cat": "bus"})
    assert len(s.rows("expense-tracker", "expenses")) == 2
    assert [x["amount"] for x in s.rows("expense-tracker", "expenses", {"cat": "food"})] == [12]
    u = s.update_row("expense-tracker", "expenses", r["id"], {"amount": 13})
    assert u["amount"] == 13 and u["cat"] == "food"
    assert s.collections("expense-tracker") == {"expenses": 2}
    s.remove_row("expense-tracker", "expenses", r["id"])
    assert len(s.rows("expense-tracker", "expenses")) == 1

    # data is per app: the other app sees none of it
    assert s.rows("expense-tracker-2", "expenses") == []

    # refusals at the trust boundary
    assert raises(s.get, "../etc")
    assert raises(s.get, "nope")
    assert raises(s.add, "expense-tracker", "bad name", {})
    assert raises(s.add, "expense-tracker", "x", "not a dict")
    assert raises(s.create, "x", "")
    assert raises(s.remove_row, "expense-tracker", "expenses", "missing")

    # delete is recoverable
    where = Path(s.delete("expense-tracker-2"))
    assert (where / "app.json").is_file() and len(s.list()) == 1


def test_inject():
    out = inject_runtime(HTML)
    assert out.startswith("<!doctype html><html><head><meta http-equiv=\"Content-Security-Policy\"")
    assert "window.app" in out and "connect-src" not in CSP and "default-src 'none'" in CSP
    bare = inject_runtime("<p>hi</p>")
    assert bare.startswith("<meta") and bare.endswith("<p>hi</p>")
    assert inject_runtime("<!DOCTYPE html><p>x</p>").startswith("<!DOCTYPE html><meta")


def test_tools(root: Path):
    homegrown._store = lambda: Store(root)
    events = []
    homegrown.notify = lambda e, p: events.append((e, p))
    call = lambda fn, **a: json.loads(fn(a))

    made = call(homegrown.create_app, name="Reading list", html=HTML)
    slug = made["app"]["slug"]
    assert made["app"]["show_in_desktop"] == '::homegrown-app{slug="reading-list"}' and made["how_to_show"]
    call(homegrown.app_data, slug=slug, action="add", collection="books", records=[{"t": "a"}, {"t": "b"}])
    listed = call(homegrown.app_data, slug=slug, action="list", collection="books")["records"]
    assert [b["t"] for b in listed] == ["a", "b"]
    assert ("data.changed", {"slug": slug, "collection": "books"}) in events
    assert "error" in call(homegrown.app_data, slug=slug, action="explode", collection="books")
    assert "error" in call(homegrown.update_app, slug="missing", html=HTML)
    assert call(homegrown.list_apps)["apps"][0]["collections"] == {"books": 2}
    assert "html" not in call(homegrown.read_app, slug=slug, include_html=False)["app"]


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
        test_store(Path(a))
        test_inject()
        test_tools(Path(b))
    print("ok")
