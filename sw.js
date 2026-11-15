/* Service worker mínimo do Minhas Finanças Pessoais (15/11/2026).
   Existe só para o navegador oferecer "Instalar app". Não guarda nada em cache e não intercepta requisições:
   a página, os índices e os dados do Google Drive vêm sempre da rede, então a sincronização não é afetada. */
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(
  caches.keys().then(ks => Promise.all(ks.map(k => caches.delete(k)))).then(() => self.clients.claim())
));
