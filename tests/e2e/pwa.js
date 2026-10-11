// Web instalable (reportes y turnos): cartel «Instalar como app» en celular Android, iPhone y PC, modo app, descarte recordado,
// service worker sin guardar nada y salida/entrada con el service worker andando. Corre contra la nube local (Worker + D1 simulados).
// Variables: BASE (http://127.0.0.1:puerto), CLAVE, FOTOS (carpeta opcional para guardar capturas), PLAYWRIGHT_PATH.
const { chromium, devices } = require(process.env.PLAYWRIGHT_PATH || '/opt/node22/lib/node_modules/playwright');
const assert = require('assert');
const fs = require('fs');
const BASE = process.env.BASE, CLAVE = process.env.CLAVE || 'clave123', FOTOS = process.env.FOTOS || '';
if (FOTOS) fs.mkdirSync(FOTOS, { recursive: true });

const sinTipo = (d) => { const { defaultBrowserType, ...r } = d; return r; };
const ANDROID = sinTipo(devices['Pixel 7']), IPHONE = sinTipo(devices['iPhone 13']);
const problemas = [];           // errores de la página: no tiene que haber ninguno (ni de CSP)

let browser;
async function contexto(opts = {}) {
  const { init, ...o } = opts;
  const ctx = await browser.newContext(o);
  if (init) await ctx.addInitScript(init);
  const page = await ctx.newPage();
  page.on('pageerror', (e) => problemas.push('pageerror: ' + e.message));
  page.on('console', (m) => { if (/Content Security Policy|Refused to/i.test(m.text())) problemas.push('CSP: ' + m.text()); });
  return { ctx, page };
}
// la persona abre el link, escribe la clave y entra (el formulario es el real de la web)
async function entrar(page, ruta = '/') {
  await page.goto(BASE + ruta);
  await page.fill('input[name=clave]', CLAVE);
  await Promise.all([page.waitForNavigation(), page.click('#login button')]);
  await page.waitForSelector('#app:not([hidden])');
}
// Turnos pregunta el nombre una sola vez por celular; acá ya se lo sabe (si no, esa ventana tapa todo)
const conNombre = (page) => page.goto(BASE + '/').then(() => page.evaluate(() => localStorage.setItem('quien', 'Ana')));
// lo que hace Chrome en Android cuando la web se puede instalar
const avisoDeChrome = (page, resultado = 'accepted') => page.evaluate((r) => {
  const e = new Event('beforeinstallprompt', { cancelable: true });
  e.prompt = () => { window.__prompts = (window.__prompts || 0) + 1; return Promise.resolve(); };
  e.userChoice = Promise.resolve({ outcome: r, platform: 'web' });
  window.__vioCancelado = !dispatchEvent(e);
}, resultado);
const cartel = (page) => page.evaluate(() => {
  const c = document.getElementById('pwa-cartel');
  if (!c) return null;
  const r = c.getBoundingClientRect();
  return { texto: c.innerText.replace(/\s+/g, ' ').trim(), boton: !!c.querySelector('.ins'), cerrar: !!c.querySelector('.x'), abajo: r.bottom <= innerHeight + 0.5 && r.top > innerHeight / 2, ancho: r.width };
});
const pausa = (ms) => new Promise((r) => setTimeout(r, ms));
const guardado = (page) => page.evaluate(() => localStorage.getItem('pwa'));
const foto = (page, nombre) => FOTOS ? page.screenshot({ path: `${FOTOS}/${nombre}.png` }) : null;
// «abierta desde el ícono»: Chrome lo avisa con display-mode (Android) y Safari con navigator.standalone (iPhone)
const ANDROID_APP = () => {
  const mm = window.matchMedia.bind(window), app = () => { try { return localStorage.getItem('__app') === '1'; } catch (e) { return false; } };
  window.matchMedia = (q) => /display-mode:\s*(standalone|minimal-ui|fullscreen)/.test(q) && app()
    ? { matches: /standalone/.test(q), media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} } : mm(q);
};
const IPHONE_APP = () => { try { if (localStorage.getItem('__app') === '1') Object.defineProperty(navigator, 'standalone', { get: () => true }); } catch (e) {} };

const casos = [];
const caso = (nombre, f) => casos.push([nombre, f]);

caso('PC: no aparece nada, ni siquiera cuando el navegador avisa que se puede instalar', async () => {
  const { ctx, page } = await contexto({ viewport: { width: 1280, height: 800 } });
  await entrar(page);
  await avisoDeChrome(page); await pausa(300);
  assert.strictEqual(await cartel(page), null);
  assert.strictEqual(await page.evaluate(() => window.__vioCancelado), false, 'en la PC no se le saca el aviso al navegador');
  assert.strictEqual(await page.evaluate(async () => !!(await navigator.serviceWorker.getRegistration())), false, 'en la PC no se registra el service worker');
  assert.ok(await page.evaluate(() => document.querySelector('meta[name=theme-color][media]') !== null), 'en la PC no se toca el color de la barra');
  await ctx.close();
});

caso('Android: en la pantalla de la clave no aparece; después de entrar sí, con botón Instalar', async () => {
  const { ctx, page } = await contexto(ANDROID);
  await page.goto(BASE + '/');
  await page.waitForSelector('#login:not([hidden])');
  await avisoDeChrome(page); await pausa(300);
  assert.strictEqual(await cartel(page), null, 'no se muestra en la pantalla de la clave');
  await page.fill('input[name=clave]', CLAVE);
  await Promise.all([page.waitForNavigation(), page.click('#login button')]);
  await page.waitForSelector('#app:not([hidden])');
  assert.strictEqual(await cartel(page), null, 'hasta que el navegador no avise, nada');
  await avisoDeChrome(page); await page.waitForSelector('#pwa-cartel');
  const c = await cartel(page);
  assert.ok(c.boton && c.cerrar && c.abajo, JSON.stringify(c));
  assert.ok(/Instal/.test(c.texto) && /Predio/.test(c.texto), c.texto);
  assert.ok(c.ancho <= 560.5, 'chico: ' + c.ancho);
  assert.strictEqual(await page.evaluate(() => window.__vioCancelado), true, 'se frena el cartelito automático de Chrome');
  assert.strictEqual(await page.evaluate(() => document.querySelectorAll('#pwa-cartel').length), 1);
  assert.ok(await page.evaluate(() => parseInt(document.body.style.paddingBottom) > 40), 'la página deja lugar para que el cartel no tape lo último');
  assert.strictEqual(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'sin scroll horizontal');
  await foto(page, 'android_reportes_claro');
  await ctx.close();
});

caso('Android: «Instalar» abre el diálogo del navegador y, si se instala, el cartel se va y no vuelve', async () => {
  const { ctx, page } = await contexto(ANDROID);
  await entrar(page);
  await avisoDeChrome(page); await page.waitForSelector('#pwa-cartel');
  await page.click('#pwa-cartel .ins');
  await page.waitForFunction(() => !document.getElementById('pwa-cartel'));
  assert.strictEqual(await page.evaluate(() => window.__prompts), 1);
  assert.strictEqual(await guardado(page), 'app');
  assert.strictEqual(await page.evaluate(() => document.body.style.paddingBottom), '', 'se devuelve el espacio');
  await page.reload(); await avisoDeChrome(page); await pausa(400);
  assert.strictEqual(await cartel(page), null);
  await ctx.close();
});

caso('Android: si en el diálogo del navegador dice que no, el cartel no insiste', async () => {
  const { ctx, page } = await contexto(ANDROID);
  await entrar(page);
  await avisoDeChrome(page, 'dismissed'); await page.waitForSelector('#pwa-cartel');
  await page.click('#pwa-cartel .ins');
  await page.waitForFunction(() => !document.getElementById('pwa-cartel'));
  assert.strictEqual(await guardado(page), 'no');
  await page.reload(); await avisoDeChrome(page); await pausa(400);
  assert.strictEqual(await cartel(page), null);
  await ctx.close();
});

caso('Android, Turnos: la x lo cierra y se acuerda (también en Reportes)', async () => {
  const { ctx, page } = await contexto(ANDROID);
  await conNombre(page); await entrar(page, '/turnos');
  await page.waitForSelector('#cal .ccol');
  await avisoDeChrome(page); await page.waitForSelector('#pwa-cartel');
  await foto(page, 'android_turnos_claro');
  await page.click('#pwa-cartel .x');
  assert.strictEqual(await cartel(page), null);
  assert.strictEqual(await guardado(page), 'no');
  await page.reload(); await avisoDeChrome(page); await pausa(400);
  assert.strictEqual(await cartel(page), null, 'recargar no lo trae de vuelta');
  await page.goto(BASE + '/'); await page.waitForSelector('#app:not([hidden])'); await avisoDeChrome(page); await pausa(400);
  assert.strictEqual(await cartel(page), null, 'ni en la otra pantalla');
  await ctx.close();
});

caso('Android abierta desde el ícono (modo app): ni cartel ni rastro, y después tampoco en el navegador', async () => {
  const { ctx, page } = await contexto({ ...ANDROID, init: ANDROID_APP });
  await page.goto(BASE + '/'); await page.evaluate(() => localStorage.setItem('__app', '1'));
  await entrar(page);
  assert.strictEqual(await page.evaluate(() => matchMedia('(display-mode: standalone)').matches), true, 'la simulación anda');
  await avisoDeChrome(page); await pausa(400);
  assert.strictEqual(await cartel(page), null);
  assert.strictEqual(await guardado(page), 'app', 'se anota que ya se abrió como app');
  await page.evaluate(() => localStorage.removeItem('__app'));      // ahora «en el navegador»
  await page.reload(); await avisoDeChrome(page); await pausa(400);
  assert.strictEqual(await page.evaluate(() => matchMedia('(display-mode: standalone)').matches), false);
  assert.strictEqual(await cartel(page), null, 'si ya se abrió desde el ícono, no se ofrece más');
  await page.goto(BASE + '/turnos'); await page.waitForSelector('#app:not([hidden])'); await avisoDeChrome(page); await pausa(400);
  assert.strictEqual(await cartel(page), null);
  await ctx.close();
});

caso('Android sin aviso de instalación (por ejemplo Firefox): se explican los pasos', async () => {
  const { ctx, page } = await contexto({ ...ANDROID, init: () => { try { delete Object.getPrototypeOf(window).onbeforeinstallprompt; delete window.onbeforeinstallprompt; } catch (e) {} } });
  await entrar(page);
  if (await page.evaluate(() => 'onbeforeinstallprompt' in window)) { console.log('  (este Chromium no deja quitar onbeforeinstallprompt: caso salteado)'); await ctx.close(); return; }
  await page.waitForSelector('#pwa-cartel');
  const c = await cartel(page);
  assert.ok(!c.boton && /menú/.test(c.texto) && /Instalar app/.test(c.texto), c.texto);
  await ctx.close();
});

caso('iPhone: un cartel con los pasos (Compartir, Agregar a pantalla de inicio), sin botón; la x se recuerda', async () => {
  const { ctx, page } = await contexto(IPHONE);
  await page.goto(BASE + '/'); await page.waitForSelector('#login:not([hidden])'); await pausa(300);
  assert.strictEqual(await cartel(page), null, 'no en la pantalla de la clave');
  await entrar(page);
  await page.waitForSelector('#pwa-cartel');
  const c = await cartel(page);
  assert.ok(!c.boton && c.cerrar && c.abajo, JSON.stringify(c));
  assert.ok(/Compartir/.test(c.texto) && /Agregar a pantalla de inicio/.test(c.texto), c.texto);
  assert.ok(await page.evaluate(() => !!document.querySelector('#pwa-cartel svg')), 'el dibujito de Compartir');
  assert.strictEqual(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'sin scroll horizontal');
  await foto(page, 'iphone_reportes_claro');
  await page.click('#pwa-cartel .x');
  assert.strictEqual(await cartel(page), null);
  await page.reload(); await pausa(500);
  assert.strictEqual(await cartel(page), null, 'recordado');
  await ctx.close();
});

caso('iPhone con la web abierta desde el ícono (Safari avisa con navigator.standalone): nada', async () => {
  const { ctx, page } = await contexto({ ...IPHONE, init: IPHONE_APP });
  await page.goto(BASE + '/'); await page.evaluate(() => { localStorage.setItem('__app', '1'); localStorage.setItem('quien', 'Ana'); });
  await entrar(page, '/turnos'); await pausa(500);
  assert.strictEqual(await page.evaluate(() => navigator.standalone), true);
  assert.strictEqual(await cartel(page), null);
  await ctx.close();
});

caso('iPad que se hace pasar por Mac (Safari «sitio de escritorio»): también los pasos', async () => {
  const ipad = { userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15', viewport: { width: 820, height: 1180 }, hasTouch: true, isMobile: true };
  const { ctx, page } = await contexto({ ...ipad, init: () => Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 5 }) });
  await entrar(page);
  await page.waitForSelector('#pwa-cartel');
  assert.ok(/Compartir/.test((await cartel(page)).texto));
  const c = await cartel(page), cj = await page.evaluate(() => { const r = document.getElementById('pwa-cartel').getBoundingClientRect(); return [r.left, innerWidth - r.right]; });
  assert.ok(c.ancho <= 560.5 && Math.abs(cj[0] - cj[1]) < 1, 'en la tablet queda chico y centrado: ' + JSON.stringify([c.ancho, cj]));
  await foto(page, 'ipad_reportes_claro');
  await ctx.close();
});

caso('Dentro de otra app (navegador de Facebook/Instagram en Android o iPhone): no se ofrece nada, porque ahí no se puede instalar', async () => {
  const wv = { ...ANDROID, userAgent: ANDROID.userAgent.replace('Android 14; Pixel 7', 'Android 14; Pixel 7; wv').replace('Mobile Safari', 'Version/4.0 Mobile Safari') };
  const enApp = { ...IPHONE, userAgent: IPHONE.userAgent.replace(/ Safari\/[\d.]+$/, '') + ' Instagram 300.0' };
  assert.ok(/; wv\)/.test(wv.userAgent) && !/Safari\//.test(enApp.userAgent), 'los user agents de prueba son los esperados');
  for (const disp of [wv, enApp]) {
    const { ctx, page } = await contexto(disp);
    await entrar(page); await avisoDeChrome(page); await pausa(400);
    assert.strictEqual(await cartel(page), null, disp.userAgent);
    await ctx.close();
  }
});

caso('Modo oscuro: el cartel se ve bien (Android e iPhone) y la barra de arriba toma el color de la página', async () => {
  for (const [nombre, disp] of [['android', ANDROID], ['iphone', IPHONE]]) {
    const { ctx, page } = await contexto({ ...disp, colorScheme: 'dark' });
    await entrar(page, '/');
    if (nombre === 'android') await avisoDeChrome(page);
    await page.waitForSelector('#pwa-cartel');
    const e = await page.evaluate(() => {
      const c = document.getElementById('pwa-cartel'), cs = getComputedStyle(c), b = c.querySelector('.ins');
      return { fondo: cs.backgroundColor, texto: cs.color, boton: b && getComputedStyle(b).color, barra: document.querySelector('meta[name=theme-color]').content, pagina: getComputedStyle(document.body).backgroundColor };
    });
    assert.strictEqual(e.fondo, 'rgb(23, 34, 30)', nombre + ' ' + JSON.stringify(e));
    assert.strictEqual(e.barra, e.pagina, 'la barra del celular acompaña a la página: ' + JSON.stringify(e));
    if (nombre === 'android') assert.strictEqual(e.boton, 'rgb(15, 23, 20)', 'texto oscuro sobre el verde claro: ' + JSON.stringify(e));
    await foto(page, nombre + '_reportes_oscuro');
    // cambiando a mano con el botón Claro/Oscuro, la barra y el cartel siguen
    await page.click('#tema'); await pausa(100);
    const claro = await page.evaluate(() => ({ barra: document.querySelector('meta[name=theme-color]').content, fondo: getComputedStyle(document.getElementById('pwa-cartel')).backgroundColor }));
    assert.strictEqual(claro.barra, 'rgb(244, 246, 245)');
    assert.strictEqual(claro.fondo, 'rgb(255, 255, 255)');
    await ctx.close();
  }
  const { ctx, page } = await contexto({ ...ANDROID, colorScheme: 'dark' });
  await conNombre(page); await entrar(page, '/turnos'); await page.waitForSelector('#cal .ccol'); await avisoDeChrome(page); await page.waitForSelector('#pwa-cartel');
  await foto(page, 'android_turnos_oscuro');
  await ctx.close();
});

caso('Service worker: se registra en el celular y no guarda nada (ni páginas ni datos)', async () => {
  const { ctx, page } = await contexto(ANDROID);
  await conNombre(page); await entrar(page);
  await page.evaluate(() => navigator.serviceWorker.ready);
  await page.reload(); await page.waitForSelector('#app:not([hidden])');
  const e = await page.evaluate(async () => ({ controla: !!navigator.serviceWorker.controller, caches: await caches.keys(), scope: (await navigator.serviceWorker.getRegistration()).scope }));
  assert.strictEqual(e.controla, true); assert.deepStrictEqual(e.caches, []); assert.strictEqual(e.scope, BASE + '/');
  // se abren las dos pantallas y se piden los datos: sigue sin guardarse nada
  await page.goto(BASE + '/turnos'); await page.waitForSelector('#cal .ccol');
  await page.evaluate(() => fetch('/api/dias').then((r) => r.text()));
  assert.deepStrictEqual(await page.evaluate(async () => caches.keys()), []);
  await ctx.close();
});

// El service worker real, con pedidos falsos: sólo toca las páginas (GET), nunca guarda nada y sin internet da el aviso.
caso('Service worker (por dentro): sólo atiende páginas, sin guardar nada; sin internet muestra «Sin conexión»', async () => {
  const codigo = await (await fetch(BASE + '/sw.js')).text();
  const oyentes = {}, usos = { caches: 0 };
  let caido = false;
  const falso = {
    self: { addEventListener: (t, f) => { oyentes[t] = f; }, skipWaiting: () => {}, clients: { claim: async () => {} } },
    caches: new Proxy({}, { get: (_, k) => { usos.caches++; return k === 'keys' ? async () => [] : async () => {}; } }),
    fetch: async (r) => { if (caido) throw new TypeError('sin red'); return new Response('desde la red:' + r.url); },
    Response, Promise, JSON,
  };
  require('vm').runInNewContext(codigo, falso);
  const pedir = async (req) => { let resp; const e = { request: req, respondWith: (p) => { resp = p; }, waitUntil: () => {} }; oyentes.fetch(e); return resp && await resp; };
  const nav = { mode: 'navigate', method: 'GET', url: BASE + '/' };
  assert.strictEqual(await (await pedir(nav)).text(), 'desde la red:' + BASE + '/', 'con internet pasa derecho');
  assert.strictEqual(await pedir({ mode: 'cors', method: 'GET', url: BASE + '/api/dias' }), undefined, 'los datos no se tocan');
  assert.strictEqual(await pedir({ mode: 'navigate', method: 'POST', url: BASE + '/entrar' }), undefined, 'el formulario de la clave tampoco');
  caido = true;
  const r = await pedir(nav);
  const t = await r.text();
  assert.ok(/Sin conexión/.test(t) && /Reintentar/.test(t) && r.headers.get('content-type').startsWith('text/html') && r.headers.get('cache-control') === 'no-store', t.slice(0, 80));
  assert.strictEqual(await pedir({ mode: 'cors', method: 'GET', url: BASE + '/api/dias' }), undefined);
  const act = []; oyentes.activate({ waitUntil: (p) => act.push(p) }); await Promise.all(act);
  assert.ok(!/caches\.(open|put|add|match)|cache\.put|\.addAll/.test(codigo), 'el código no guarda nada');
});

// Fase 1: se deja el service worker instalado en un perfil. Fase 2 (con la nube apagada): se abre la web y tiene que salir el aviso amable.
caso('Sin internet (nube apagada), con la web ya abierta antes: aviso amable en lugar del error del navegador', async () => {
  if (!process.env.PERFIL) { console.log('  (sin PERFIL: salteado)'); return; }
  const ctx = await chromium.launchPersistentContext(process.env.PERFIL, { ...ANDROID, args: ['--no-sandbox'] });
  const page = ctx.pages()[0] || await ctx.newPage();
  await page.goto(BASE + '/'); await page.fill('input[name=clave]', CLAVE);
  await Promise.all([page.waitForNavigation(), page.click('#login button')]);
  await page.waitForSelector('#app:not([hidden])');
  await page.evaluate(() => navigator.serviceWorker.ready);
  await ctx.close();
});

caso('Se puede instalar de verdad (Chrome no encuentra problemas con el manifest ni con el service worker)', async () => {
  const { ctx, page } = await contexto(ANDROID);
  await entrar(page);
  await page.evaluate(() => navigator.serviceWorker.ready); await page.reload(); await pausa(500);
  const cdp = await ctx.newCDPSession(page);
  const inst = await cdp.send('Page.getInstallabilityErrors');
  assert.deepStrictEqual(inst.installabilityErrors, [], JSON.stringify(inst));
  const m = await cdp.send('Page.getAppManifest');
  assert.deepStrictEqual(m.errors, [], JSON.stringify(m.errors));
  const d = JSON.parse(m.data);
  assert.strictEqual(d.start_url, '/'); assert.strictEqual(d.display, 'standalone'); assert.strictEqual(d.name, 'Predio');
  await ctx.close();
});

caso('Salir y volver a entrar andan con el service worker, y después no se ven datos', async () => {
  const { ctx, page } = await contexto(ANDROID);
  await entrar(page);
  await page.evaluate(() => navigator.serviceWorker.ready); await page.reload(); await page.waitForSelector('#app:not([hidden])');
  await Promise.all([page.waitForNavigation(), page.click('a[href="/salir"]')]);
  await page.waitForSelector('#login:not([hidden])');
  assert.strictEqual(await page.evaluate(() => fetch('/api/dias').then((r) => r.status)), 401);
  assert.strictEqual(await page.evaluate(() => document.getElementById('app').hidden), true);
  assert.strictEqual(await cartel(page), null);
  // clave mala: se queda en la pantalla de la clave
  await page.fill('input[name=clave]', 'equivocada');
  await Promise.all([page.waitForNavigation(), page.click('#login button')]);
  assert.ok(await page.locator('#mal:not([hidden])').count() === 1);
  await ctx.close();
});

async function sinInternet() {
  const ctx = await chromium.launchPersistentContext(process.env.PERFIL, { ...ANDROID, args: ['--no-sandbox'] });
  const page = ctx.pages()[0] || await ctx.newPage();
  for (const ruta of ['/', '/turnos']) {
    await page.goto(BASE + ruta).catch(() => {});
    const texto = await page.innerText('body');
    assert.ok(/Sin conexión/.test(texto) && /No hay internet/.test(texto), ruta + ': ' + texto.slice(0, 100));
    assert.strictEqual(await page.locator('button:has-text("Reintentar")').count(), 1);
    assert.ok(!/Reportes del Predio|Turnos del Predio/.test(texto), 'sin datos guardados');
  }
  await foto(page, 'android_sin_conexion');
  await ctx.close();
  console.log('ok  - sin internet: aviso amable en las dos pantallas');
}

(async () => {
  if (process.env.FASE === 'sin_internet') { await sinInternet(); process.exit(0); }
  browser = await chromium.launch({ args: ['--no-sandbox'] });
  let fallo = 0;
  for (const [nombre, f] of casos) {
    try { await f(); console.log('ok  - ' + nombre); } catch (e) { fallo++; console.log('FALLÓ - ' + nombre + '\n' + (e && e.stack || e)); }
  }
  await browser.close();
  if (problemas.length) { fallo++; console.log('FALLÓ - errores en la página:\n' + [...new Set(problemas)].join('\n')); }
  process.exit(fallo ? 1 : 0);
})();
