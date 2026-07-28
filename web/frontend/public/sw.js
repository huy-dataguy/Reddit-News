const CACHE_NAME = 'reddit-radar-v1'
const STATIC_ASSETS = ['/', '/index.html']

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(STATIC_ASSETS))
  )
  self.skipWaiting()
})

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    )
  )
  self.clients.claim()
})

self.addEventListener('fetch', event => {
  if (event.request.url.includes('/api/')) return
  event.respondWith(
    caches.match(event.request).then(cached => cached || fetch(event.request))
  )
})

self.addEventListener('push', event => {
  const data = event.data ? event.data.json() : { title: 'Reddit Radar', body: 'Tin mới từ radar!' }
  const options = {
    body: data.body,
    icon: '/favicon.ico',
    badge: '/favicon.ico',
    data: { url: data.url || '/' },
    actions: [
      { action: 'open', title: 'Đọc ngay' },
      { action: 'dismiss', title: 'Bỏ qua' },
    ],
  }
  event.waitUntil(self.registration.showNotification(data.title, options))
})

self.addEventListener('notificationclick', event => {
  event.notification.close()
  if (event.action === 'dismiss') return
  const url = event.notification.data?.url || '/'
  event.waitUntil(clients.openWindow(url))
})
