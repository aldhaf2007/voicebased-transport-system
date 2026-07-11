const CACHE_NAME = 'voice-transport-v9';
const ASSETS = [
  '/static/css/style.css',
  '/static/js/main.js',
  '/static/manifest.json'
];

// Install Event
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => {
        console.log('Caching shell assets');
        return cache.addAll(ASSETS);
      })
      .then(() => self.skipWaiting())
  );
});

// Activate Event
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys => {
      return Promise.all(
        keys.map(key => {
          if (key !== CACHE_NAME) {
            return caches.delete(key);
          }
        })
      );
    }).then(() => self.clients.claim())
  );
});

// Fetch Event
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  // Skip intercepting navigate page loads, root URL, search API, TTS stream, admin, and authentication routes
  if (
    event.request.method !== 'GET' ||
    event.request.mode === 'navigate' ||
    url.pathname === '/' ||
    url.pathname.includes('/search') ||
    url.pathname.includes('/tts') ||
    url.pathname.includes('/admin') ||
    url.pathname.includes('/login') ||
    url.pathname.includes('/signup') ||
    url.pathname.includes('/logout')
  ) {
    return;
  }
  
  event.respondWith(
    caches.match(event.request)
      .then(cachedResponse => {
        if (cachedResponse) {
          return cachedResponse;
        }
        return fetch(event.request).then(response => {
          if (!response || response.status !== 200 || response.type !== 'basic') {
            return response;
          }
          const responseToCache = response.clone();
          caches.open(CACHE_NAME).then(cache => {
            cache.put(event.request, responseToCache);
          });
          return response;
        });
      }).catch(() => {
        // Fallback for offline if resources not in cache
        if (event.request.mode === 'navigate') {
          return caches.match('/');
        }
      })
  );
});
