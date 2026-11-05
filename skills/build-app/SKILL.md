---
name: build-app
description: How to build, change and use Homegrown apps — small persistent apps (trackers, logs, CRMs, checklists, dashboards) the user keeps and Hermes can act on from any chat.
---

# Building a Homegrown app

Use Homegrown when the user wants something they will come back to: "track my expenses", "a reading list", "a simple CRM for my leads", "log my runs". For a one-off answer, just answer.

## The document

Write one complete, self-contained HTML document and pass it to `homegrown_create_app`.

- Inline all CSS and JS. External URLs (CDNs, fonts, images, APIs) are blocked by the app's Content-Security-Policy and will silently fail. Draw charts with inline SVG or canvas.
- No `fetch`, `localStorage`, `alert`, `confirm` or popups: the app runs in a sandboxed frame. Store everything with `window.app`.
- Keep it small and calm: system font stack, generous spacing, works from 360px wide. Support dark mode with `@media (prefers-color-scheme: dark)`.
- Render from data on load and on every change, so updates made by Hermes from another chat appear live.

## window.app

`window.app` is injected before your scripts run. Every call returns a Promise. Records are plain JSON objects in named collections (`[A-Za-z0-9_-]`); each comes back with `id`, `created_at` and `updated_at` (Unix seconds).

```js
await app.list('expenses')                         // all records, oldest first
await app.list('expenses', { where: { category: 'food' }, limit: 50 })
const rec = await app.add('expenses', { amount: 12.5, category: 'food', note: 'lunch' })
await app.update('expenses', rec.id, { amount: 13 })   // merges fields
await app.remove('expenses', rec.id)
app.onChange(collection => render())               // fires when anyone changes this app's data
await app.ask('Summarise my spending this month')  // sends a prompt to Hermes in the user's chat
```

Handle a rejected Promise by showing a short inline message, never by losing what the user typed.

A minimal pattern:

```html
<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<style>body{font:14px system-ui;margin:16px}</style></head><body>
<form id="f"><input name="item" required><button>Add</button></form><ul id="list"></ul>
<script>
const render = async () => {
  const items = await app.list('items')
  list.replaceChildren(...items.map(i => Object.assign(document.createElement('li'), { textContent: i.item })))
}
f.onsubmit = async e => { e.preventDefault(); await app.add('items', { item: f.item.value }); f.reset() }
app.onChange(render); render()
</script></body></html>
```

## After creating or changing an app

In Hermes Desktop, show it inline by writing the `show_in_desktop` directive from the tool result as its own paragraph, exactly as returned:

::homegrown-app{slug="expense-tracker"}

It also appears on the Apps page in the sidebar. On messaging platforms (Telegram, Slack and so on) don't write the directive; tell the user it is on the Apps page in Hermes Desktop.

## Changing an app

Call `homegrown_read_app` first, edit the HTML, then `homegrown_update_app` with the full new document and a short `note`. Data is untouched by code changes, so keep collection and field names stable; if you rename a field, migrate existing records with `homegrown_app_data`. To undo a bad change, `homegrown_update_app` with `restore_version`.

## Using an app's data from chat

When the user says something an app should hold ("spent $12 on coffee", "add Dana from Acme as a lead"), find the app with `homegrown_list_apps` and write the record with `homegrown_app_data` using the same collection and field names the app's code uses (read the app if unsure). Answer questions about their data by listing records and computing the answer yourself.

Ask before `homegrown_delete_app`, and before deleting records the user didn't name.
