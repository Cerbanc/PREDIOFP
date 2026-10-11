// Web instalable (PWA): manifest, íconos y service worker. Son públicos a propósito (el celular los pide sin la cookie de sesión)
// y no llevan ningún dato del negocio: sólo el nombre, los colores y el logo del Predio.
import { ICONOS } from "./iconos.js";

const NOSNIFF = { "x-content-type-options": "nosniff" };

// Colores = los de la web: fondo claro (--bg) y oscuro. El manifest sólo admite uno; cada página lo ajusta con <meta name="theme-color">.
const MANIFEST = JSON.stringify({
  id: "/",
  name: "Predio",
  short_name: "Predio",
  description: "Reportes y turnos del Predio",
  lang: "es-AR",
  dir: "ltr",
  start_url: "/",
  scope: "/",
  display: "standalone",
  background_color: "#f4f6f5",
  theme_color: "#f4f6f5",
  icons: [
    { src: "/icono-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
    { src: "/icono-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
    { src: "/icono-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
  ],
});

// Service worker mínimo: el celular lo pide para dejar instalar la web. NO guarda nada (ni páginas, ni datos, ni respuestas con sesión):
// todo va directo a internet y sólo si no hay conexión muestra un aviso amable en lugar del error del navegador.
const SIN_CONEXION = `<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sin conexión</title><meta name="color-scheme" content="light dark">
<script>try{var t=localStorage.getItem("tema");if(t==="dark"||t==="light")document.documentElement.dataset.theme=t}catch(e){}</script>
<style>
:root{--bg:#f4f6f5;--tx:#14211c;--mu:#5b6b64;--ac:#0a7a5a}
@media(prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#0f1714;--tx:#e8f0ec;--mu:#9bb0a7;--ac:#3fbf96}}
:root[data-theme=dark]{--bg:#0f1714;--tx:#e8f0ec;--mu:#9bb0a7;--ac:#3fbf96}
body{margin:0;background:var(--bg);color:var(--tx);font:16px/1.4 system-ui,sans-serif;min-height:100vh;display:grid;place-items:center;text-align:center;padding:16px}
h1{font-size:20px;margin:0 0 8px}p{color:var(--mu);margin:0 0 16px}
button{font:inherit;border:0;border-radius:10px;padding:10px 18px;background:var(--ac);color:#fff;cursor:pointer}
</style></head><body><main><h1>Sin conexión</h1><p>No hay internet en este momento.<br>Cuando vuelva la señal, tocá «Reintentar».</p>
<button onclick="location.reload()">Reintentar</button></main></body></html>`;

const SW = `// Predio: service worker mínimo. No guarda nada en el teléfono.
const SIN_CONEXION = ${JSON.stringify(SIN_CONEXION)};
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(
  caches.keys().then((ks) => Promise.all(ks.map((k) => caches.delete(k)))).then(() => self.clients.claim())
));
self.addEventListener("fetch", (e) => {
  if (e.request.mode !== "navigate" || e.request.method !== "GET") return;      // lo demás (datos, Excel, íconos, el formulario de la clave) va siempre directo a internet
  e.respondWith(fetch(e.request).catch(() => new Response(SIN_CONEXION, { status: 503, headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" } })));
});
`;

const decodificados = {};
function icono(nombre) {
  if (!decodificados[nombre]) decodificados[nombre] = Uint8Array.from(atob(ICONOS[nombre]), (c) => c.charCodeAt(0));
  return decodificados[nombre];
}

// Devuelve la respuesta si la ruta es una de las públicas de la app instalable; si no, null (sigue el flujo normal con clave).
export function manejarPwa(req, ruta) {
  if (req.method !== "GET" && req.method !== "HEAD") return null;
  if (ruta === "/manifest.webmanifest")
    return new Response(MANIFEST, { headers: { "content-type": "application/manifest+json; charset=utf-8", "cache-control": "public, max-age=3600", ...NOSNIFF } });
  if (ruta === "/sw.js")
    return new Response(SW, { headers: { "content-type": "text/javascript; charset=utf-8", "cache-control": "no-cache", ...NOSNIFF } });
  const nombre = ruta.slice(1);
  if (Object.prototype.hasOwnProperty.call(ICONOS, nombre))
    return new Response(icono(nombre), { headers: { "content-type": "image/png", "cache-control": "public, max-age=86400", ...NOSNIFF } });
  return null;
}
