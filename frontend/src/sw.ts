/// <reference lib="webworker" />

import { precacheAndRoute, cleanupOutdatedCaches } from 'workbox-precaching'
import { registerRoute, NavigationRoute, setCatchHandler } from 'workbox-routing'
import type { WorkboxPlugin } from 'workbox-core/types'
import { NetworkFirst, CacheFirst } from 'workbox-strategies'
import { ExpirationPlugin } from 'workbox-expiration'
import { CacheableResponsePlugin } from 'workbox-cacheable-response'

// Helper: cast Workbox plugins to satisfy exactOptionalPropertyTypes.
// The concrete plugin classes define optional callbacks with `| undefined`
// but WorkboxPlugin expects them to be truly optional (absent).
const plugins = (...p: (CacheableResponsePlugin | ExpirationPlugin)[]): WorkboxPlugin[] =>
  p as unknown as WorkboxPlugin[]

declare let self: ServiceWorkerGlobalScope

// Precache static assets injected by vite-plugin-pwa
precacheAndRoute(self.__WB_MANIFEST)
cleanupOutdatedCaches()

// --- API Caching Strategies ---

// Stock quote API: NetworkFirst with short cache (30s for financial data freshness)
registerRoute(
  ({ url }) => url.pathname.match(/\/api\/v1\/stocks\/[^/]+\/quote/),
  new NetworkFirst({
    cacheName: 'stock-quotes',
    networkTimeoutSeconds: 5,
    plugins: plugins(
      new CacheableResponsePlugin({ statuses: [0, 200] }),
      new ExpirationPlugin({ maxEntries: 100, maxAgeSeconds: 30 }),
    ),
  })
)

// General API: NetworkFirst with 5min cache
registerRoute(
  ({ url }) => {
    if (!url.pathname.startsWith('/api/')) return false
    // Exclude SSE streaming endpoints
    if (url.pathname.includes('/stream')) return false
    // Exclude auth endpoints (security)
    if (url.pathname.startsWith('/api/v1/auth/')) return false
    return true
  },
  new NetworkFirst({
    cacheName: 'api-cache',
    networkTimeoutSeconds: 10,
    plugins: plugins(
      new CacheableResponsePlugin({ statuses: [0, 200] }),
      new ExpirationPlugin({ maxEntries: 200, maxAgeSeconds: 300 }),
    ),
  })
)

// Image caching: CacheFirst with 30-day expiry
registerRoute(
  ({ request }) => request.destination === 'image',
  new CacheFirst({
    cacheName: 'images',
    plugins: plugins(
      new CacheableResponsePlugin({ statuses: [0, 200] }),
      new ExpirationPlugin({ maxEntries: 50, maxAgeSeconds: 30 * 24 * 60 * 60 }),
    ),
  })
)

// Font caching: CacheFirst with 1-year expiry
registerRoute(
  ({ request }) => request.destination === 'font',
  new CacheFirst({
    cacheName: 'fonts',
    plugins: plugins(
      new CacheableResponsePlugin({ statuses: [0, 200] }),
      new ExpirationPlugin({ maxEntries: 20, maxAgeSeconds: 365 * 24 * 60 * 60 }),
    ),
  })
)

// --- Offline Navigation Fallback ---

// For navigation requests that fail, serve offline.html
const navigationHandler = new NavigationRoute(
  new NetworkFirst({
    cacheName: 'navigations',
    networkTimeoutSeconds: 5,
    plugins: plugins(new CacheableResponsePlugin({ statuses: [0, 200] })),
  }),
  {
    // Don't handle API or streaming requests as navigation
    denylist: [/\/api\//, /\/stream/],
  }
)
registerRoute(navigationHandler)

// Workbox catch handler: when any route fails (network + cache miss),
// serve offline.html for navigation requests, or a simple offline Response for others.
// offline.html is precached via __WB_MANIFEST, so it's always available.
setCatchHandler(async ({ request }) => {
  if (request.mode === 'navigate') {
    const cached = await caches.match('/offline.html')
    if (cached) return cached
  }
  return Response.error()
})

// --- Push Notifications ---

self.addEventListener('push', (event) => {
  if (!event.data) return

  try {
    const payload = event.data.json() as { title?: string; body?: string; url?: string }
    const title = payload.title ?? 'WebStock'
    const options: NotificationOptions & { renotify?: boolean } = {
      body: payload.body ?? '',
      icon: '/icons/icon-192x192.png',
      badge: '/icons/icon-192x192.png',
      data: { url: payload.url ?? '/' },
      tag: 'webstock-notification',
      renotify: true,
    }

    event.waitUntil(self.registration.showNotification(title, options))
  } catch {
    // Non-JSON push payload, ignore
    console.warn('[SW] Failed to parse push payload')
  }
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()

  const url = (event.notification.data as { url?: string })?.url ?? '/'

  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clients) => {
      // Focus existing window if available
      for (const client of clients) {
        if ('focus' in client) {
          client.focus()
          client.navigate(url)
          return
        }
      }
      // Open new window
      return self.clients.openWindow(url)
    })
  )
})

// --- Service Worker Lifecycle ---

self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting()
  }
})
