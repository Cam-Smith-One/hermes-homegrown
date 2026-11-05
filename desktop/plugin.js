// Homegrown — Desktop half. An Apps page plus a ::homegrown-app{slug="…"} chat widget.
// Each app runs in a SandboxedFrame (opaque origin, no network via CSP). Its only door out is
// postMessage to its own frame host here, which scopes every data call to that one app.
import { useEffect, useRef, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'
import {
  Button,
  EmptyState,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA,
  SandboxedFrame,
  TRANSCRIPT_DIRECTIVE_AREA,
  host,
  relativeTime
} from '@hermes/plugin-sdk'

const SLUG_RE = /^[a-z0-9][a-z0-9-]{0,47}$/
let ctx = null

const api = (path, opts) => ctx.rest(path, opts)
const enc = encodeURIComponent

function toDataUrl(html) {
  const bytes = new TextEncoder().encode(html)
  let bin = ''
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
  return `data:text/html;base64,${btoa(bin)}`
}

// Every op is scoped to `slug`: the frame names a collection, never an app.
function runOp(slug, op, args, sessionId) {
  const base = `/apps/${enc(slug)}/data/${enc(args.collection ?? '')}`
  switch (op) {
    case 'list':
      return api(`${base}/query`, { method: 'POST', body: { where: args.where, limit: args.limit } }).then(r => r.records)
    case 'add':
      return api(base, { method: 'POST', body: { data: args.data } }).then(r => r.record)
    case 'update':
      return api(`${base}/${enc(args.id)}`, { method: 'PATCH', body: { data: args.data } }).then(r => r.record)
    case 'remove':
      return api(`${base}/${enc(args.id)}`, { method: 'DELETE' }).then(() => true)
    case 'ask': {
      const text = String(args.text ?? '').slice(0, 500)
      if (!text) return Promise.reject(new Error('empty prompt'))
      const sent = host.composer.submit(sessionId ?? null, `[${slug} app] ${text}`)
      return sent ? Promise.resolve(true) : Promise.reject(new Error('Open a chat to ask Hermes'))
    }
    default:
      return Promise.reject(new Error(`unknown op ${op}`))
  }
}

function AppFrame({ slug, sessionId, height }) {
  const frame = useRef(null)
  const [doc, setDoc] = useState(null)
  const [error, setError] = useState(null)
  const [nonce, setNonce] = useState(0)

  useEffect(() => {
    let live = true
    api(`/apps/${enc(slug)}/document`)
      .then(d => live && (setDoc(d), setError(null)))
      .catch(e => live && setError(String(e?.message ?? e)))
    return () => { live = false }
  }, [slug, nonce])

  useEffect(() => {
    const post = msg => frame.current?.contentWindow?.postMessage(msg, '*')
    const onMessage = e => {
      const m = e.data
      if (!frame.current || e.source !== frame.current.contentWindow || !m || m.type !== 'homegrown') return
      runOp(slug, m.op, m.args ?? {}, sessionId).then(
        result => post({ type: 'homegrown:reply', id: m.id, ok: true, result }),
        err => post({ type: 'homegrown:reply', id: m.id, ok: false, error: String(err?.message ?? err) })
      )
    }
    window.addEventListener('message', onMessage)
    const offData = host.onEvent('plugin.homegrown.data.changed', ({ payload }) => {
      if (payload?.slug === slug) post({ type: 'homegrown:changed', collection: payload.collection })
    })
    const offApp = host.onEvent('plugin.homegrown.app.updated', ({ payload }) => {
      if (payload?.slug === slug) setNonce(n => n + 1)
    })
    return () => { window.removeEventListener('message', onMessage); offData(); offApp() }
  }, [slug, sessionId])

  if (error) return jsx('div', { className: 'p-3 text-sm text-(--ui-text-tertiary)', children: `Homegrown: ${error}` })
  if (!doc) return jsx('div', { className: 'p-3 text-sm text-(--ui-text-tertiary)', children: 'Loading app…' })
  return jsx(SandboxedFrame, {
    ref: frame,
    src: toDataUrl(doc.html),
    sandbox: 'allow-scripts allow-forms',
    title: doc.name,
    className: 'w-full rounded-md border border-(--ui-border) bg-white',
    style: { height }
  }, `${slug}@${doc.version}`)
}

function AppCard({ app, onOpen }) {
  const records = Object.values(app.collections ?? {}).reduce((a, b) => a + b, 0)
  return jsxs('button', {
    type: 'button',
    onClick: onOpen,
    className: 'flex flex-col gap-1 rounded-lg border border-(--ui-border) p-3 text-left hover:bg-(--ui-bg-hover)',
    children: [
      jsx('div', { className: 'font-medium', children: app.name }),
      jsx('div', { className: 'line-clamp-2 text-xs text-(--ui-text-secondary)', children: app.description || ' ' }),
      jsx('div', {
        className: 'mt-1 text-[0.6875rem] text-(--ui-text-tertiary)',
        children: `v${app.version} · ${records} records · ${relativeTime(app.updated * 1000)}`
      })
    ]
  })
}

function AppsPage() {
  const [apps, setApps] = useState(null)
  const [open, setOpen] = useState(null)

  useEffect(() => {
    const load = () => api('/apps').then(r => setApps(r.apps)).catch(() => setApps([]))
    load()
    const offApp = host.onEvent('plugin.homegrown.app.updated', load)
    const offData = host.onEvent('plugin.homegrown.data.changed', load)
    return () => { offApp(); offData() }
  }, [])

  const current = open && apps?.find(a => a.slug === open)
  if (current) {
    return jsxs('div', {
      className: 'flex h-full flex-col gap-2 p-3',
      children: [
        jsxs('div', {
          className: 'flex items-center gap-2',
          children: [
            jsx(Button, { variant: 'ghost', size: 'sm', onClick: () => setOpen(null), children: '← Apps' }),
            jsx('div', { className: 'font-medium', children: current.name }),
            jsx('div', { className: 'text-xs text-(--ui-text-tertiary)', children: `v${current.version}` })
          ]
        }),
        jsx('div', { className: 'min-h-0 flex-1', children: jsx(AppFrame, { slug: current.slug, sessionId: null, height: '100%' }) })
      ]
    })
  }

  return jsxs('div', {
    className: 'flex h-full flex-col gap-3 overflow-auto p-4',
    children: [
      jsx('div', { className: 'text-lg font-semibold', children: 'Apps' }),
      apps === null
        ? jsx('div', { className: 'text-sm text-(--ui-text-tertiary)', children: 'Loading apps…' })
        : apps.length === 0
        ? jsx(EmptyState, { title: 'No apps yet', description: 'Ask Hermes to build you one: "make me an expense tracker".' })
        : jsx('div', {
            className: 'grid gap-3',
            style: { gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))' },
            children: apps.map(app => jsx(AppCard, { app, onOpen: () => setOpen(app.slug) }, app.slug))
          })
    ]
  })
}

export default {
  id: 'homegrown',
  name: 'Homegrown',
  register(c) {
    ctx = c
    ctx.registerMany([
      { id: 'page', area: ROUTES_AREA, data: { path: '/homegrown' }, render: () => jsx(AppsPage, {}) },
      { id: 'nav', area: SIDEBAR_NAV_AREA, data: { path: '/homegrown', label: 'Apps', codicon: 'extensions' } },
      {
        id: 'app-directive',
        area: TRANSCRIPT_DIRECTIVE_AREA,
        data: {
          name: 'homegrown-app',
          render: ({ attrs, sessionId, streaming }) => {
            // Attributes are untrusted model output: render nothing on garbage.
            if (streaming || !SLUG_RE.test(attrs?.slug ?? '')) return null
            return jsx(AppFrame, { slug: attrs.slug, sessionId, height: '440px' })
          }
        }
      }
    ])
  }
}
