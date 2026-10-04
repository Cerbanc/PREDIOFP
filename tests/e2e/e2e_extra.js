// Segunda prueba de punta a punta (programa ya con datos): cargar un Excel por la pantalla, historial viejo, dos ventanas, programa caído.
const { chromium } = require(process.env.PLAYWRIGHT_PATH || '/opt/node22/lib/node_modules/playwright');
const assert = require('assert');
const URL = `http://127.0.0.1:${process.env.PORT}/`;
const XLSX_PATH = process.env.XLSX_PATH;
const TOTAL = +process.env.TOTAL_VENTAS, EN_VENTANA = +process.env.EN_VENTANA;

(async () => {
  const b = await chromium.launch({ args: ['--no-sandbox'] });
  const ctx = await b.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const errs = [];
  page.on('pageerror', e => errs.push('PAGEERROR ' + e.message));
  const ev = f => page.evaluate(f);
  await page.goto(URL);
  await page.waitForFunction(() => window.__pos && window.__pos.DB);

  // ---- 1. la pantalla sólo cargó los últimos días; los reportes y tickets viejos se piden solos
  const cargadas = await ev(() => __pos.DB.ventas.length);
  assert.strictEqual(cargadas, EN_VENTANA, 'cargó sólo la ventana reciente');
  assert.ok(await ev(() => !!VENTANA_DESDE), 'sabe desde qué día tiene cargado');
  await ev(() => { UI.view = 'tickets'; UI.tkDesde = dayKey(Date.now() - 80 * 864e5); UI.tkHasta = dayKey(Date.now()); render(); });
  const despues = await ev(() => __pos.DB.ventas.length);
  assert.ok(despues > cargadas, `al pedir tickets viejos se sumaron (${cargadas} -> ${despues})`);
  assert.strictEqual(despues, TOTAL, 'quedan todas las ventas');
  const orden = await ev(() => __pos.DB.ventas.map(v => v.nro));
  assert.deepStrictEqual(orden, orden.slice().sort((a, b) => a - b), 'las ventas viejas quedan antes, en orden');
  // con las viejas cargadas, un cambio chico no manda nada de más ni borra lo que no estaba a la vista
  const antes = await ev(() => __pos.REV);
  await ev(() => { const r = __pos.tx(() => { DB.negocio = 'Predio Test'; }); return r.ok; });
  const srv = await ev(async () => (await fetch('/api/estado?t=' + encodeURIComponent(PREDIO.token))).json());
  assert.strictEqual(srv.rev, antes + 1);
  assert.strictEqual(srv.db.ventas.length, EN_VENTANA, 'el servidor sigue mostrando sólo la ventana al abrir');
  const completo = await ev(async () => (await fetch('/api/historia?desde=2000-01-01&hasta=2100-01-01&t=' + encodeURIComponent(PREDIO.token))).json());
  assert.strictEqual(completo.ventas.length, TOTAL, 'en la base están todas');

  // ---- 2. cargar un Excel por la pantalla
  await ev(() => { UI.view = 'admin'; UI.adminUntil = Date.now() + 300000; UI.admTab = 'planilla'; UI.pl = null; render(); });
  await page.waitForSelector('#pl-body');
  // pegar con títulos: "Stock mínimo" no puede pisar al stock, y la columna ID une con el producto aunque cambie el nombre
  await page.locator('#pl-body [data-c=nombre]').first().focus();
  await page.evaluate(() => {
    const dt = new DataTransfer(); const id = UI.pl.filas.find(f => f.v.nombre === 'Coca-Cola 500 ml').id;
    dt.setData('text/plain', `ID\tProducto\tStock actual\tStock mínimo\n${id}\tCoca 500 renombrada\t33\t7`);
    document.activeElement.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
  });
  const pg = await ev(() => { const f = UI.pl.filas.find(x => x.v.nombre === 'Coca 500 renombrada'); return f && { stock: f.v.stock, min: f.v.minimo, id: !!f.id, n: UI.pl.filas.filter(x => x.v.nombre.startsWith('Coca')).length }; });
  assert.deepStrictEqual(pg, { stock: '33', min: '7', id: true, n: 1 }, 'el pegado con ID renombra sin duplicar: ' + JSON.stringify(pg));
  await ev(() => { UI.pl = null; views.admin(); });
  await page.locator('#pl-file').setInputFiles(XLSX_PATH);
  await page.waitForFunction(() => /Se leyeron/.test(document.body.innerText));
  const nombres = await page.locator('#pl-body [data-c=nombre]').evaluateAll(els => els.map(e => e.value).filter(Boolean));
  assert.ok(nombres.includes('Gatorade 500 ml') && nombres.includes('Coca-Cola 500 ml'), 'cargó las filas del Excel');
  const coca = await ev(() => { const f = UI.pl.filas.find(x => x.v.nombre === 'Coca-Cola 500 ml'); return { precio: f.v.precio, stock: f.v.stock, id: f.id }; });
  assert.strictEqual(coca.precio, '2500'); assert.ok(coca.id, 'la fila del Excel se unió con la Coca que ya existía');
  assert.ok(/nuevo/.test(await page.locator('#pl-body tr', { has: page.locator('[data-c=nombre][value="Gatorade 500 ml"]') }).locator('.pl-st').innerText()));
  await page.locator('#pl-guardar').click(); await page.locator('#mact-1').click();
  await page.waitForFunction(() => __pos.DB.productos.some(p => p.nombre === 'Gatorade 500 ml'));
  assert.strictEqual(await ev(() => __pos.DB.productos.find(p => p.nombre === 'Gatorade 500 ml').stock), 18);
  assert.strictEqual(await ev(() => __pos.DB.productos.find(p => p.nombre.startsWith('Coca')).precio), 2500);

  // ---- 3. dos ventanas: la que quedó vieja no puede pisar a la otra
  const page2 = await ctx.newPage();
  await page2.goto(URL);
  await page2.waitForFunction(() => window.__pos && window.__pos.DB);
  await page2.evaluate(() => { __pos.tx(() => { DB.negocio = 'Desde la segunda ventana'; }); });
  await ev(() => { try { __pos.tx(() => { DB.negocio = 'Desde la primera (vieja)'; }); } catch (e) { /* ya mostró el aviso */ } });
  await page.waitForSelector('#pantalla-vieja');
  const n = await page2.evaluate(async () => (await (await fetch('/api/estado?t=' + encodeURIComponent(PREDIO.token))).json()).db.negocio);
  assert.strictEqual(n, 'Desde la segunda ventana', 'la ventana vieja no pisó lo nuevo');
  await page2.close();

  // ---- 4. si el programa se cae, se avisa y no se da nada por guardado
  const p3 = await ctx.newPage();
  await p3.goto(URL);
  await p3.waitForFunction(() => window.__pos && window.__pos.DB);
  await p3.route('**/api/guardar', route => route.abort());
  const r = await p3.evaluate(() => { const res = __pos.tx(() => { DB.negocio = 'No se guardó'; }); return { ok: res.ok, negocio: DB.negocio, ok2: ESTADO_GUARDADO.ok }; });
  assert.strictEqual(r.ok, false, 'la operación se cancela'); assert.notStrictEqual(r.negocio, 'No se guardó', 'y la pantalla vuelve atrás');
  assert.strictEqual(r.ok2, false); assert.ok(/NO SE ESTÁ GUARDANDO/.test(await p3.locator('#estado-guardado').innerText()));
  await p3.unroute('**/api/guardar');
  await p3.evaluate(() => persist());           // al volver el programa, se reintenta solo
  assert.ok(await p3.evaluate(() => ESTADO_GUARDADO.ok), 'se recupera');
  await p3.close();

  assert.deepStrictEqual(errs, [], JSON.stringify(errs));
  console.log('E2E EXTRA OK');
  await b.close();
})().catch(e => { console.error('E2E EXTRA FALLÓ:', e.message); process.exit(1); });
