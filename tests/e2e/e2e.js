// Prueba de punta a punta con un navegador real: planilla -> venta -> verificación contra el servidor.
// Uso: PORT=8791 FASE=1 node e2e.js   (FASE=2 vuelve a abrir la pantalla después de reiniciar el programa)
const { chromium } = require(process.env.PLAYWRIGHT_PATH || '/opt/node22/lib/node_modules/playwright');
const assert = require('assert');
const URL = `http://127.0.0.1:${process.env.PORT}/`;
const FASE = process.env.FASE || '1';
const FOTO = process.env.FOTOS || '';

(async () => {
  const b = await chromium.launch({ args: ['--no-sandbox'] });
  const ctx = await b.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const errs = [];
  page.on('pageerror', e => errs.push('PAGEERROR ' + e.message));
  page.on('console', m => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errs.push('CONSOLE ' + m.text()); });
  page.on('requestfailed', r => { if (!r.url().startsWith(URL)) errs.push('PEDIDO A INTERNET: ' + r.url()); });
  page.on('request', r => { if (!r.url().startsWith(URL) && !r.url().startsWith('data:') && !r.url().startsWith('blob:')) errs.push('PEDIDO A INTERNET: ' + r.url()); });
  const ev = f => page.evaluate(f);
  const foto = n => FOTO ? page.screenshot({ path: `${FOTO}/${n}.png` }) : null;
  await page.goto(URL);
  await page.waitForFunction(() => window.__pos && window.__pos.DB);

  if (FASE === '1') {
    // ---- base nueva: vacía, sin datos de ejemplo
    assert.strictEqual(await ev(() => __pos.DB.productos.length), 0, 'la base nueva no tiene productos');
    assert.strictEqual(await ev(() => __pos.DB.ventas.length), 0);
    assert.ok(await ev(() => __pos.DB.categorias.length >= 3 && __pos.DB.metodos.length >= 4), 'trae categorías y medios de pago');
    assert.ok(/NO SE ESTÁ GUARDANDO/.test(await page.locator('#estado-guardado').innerText()) === false);
    await page.locator('[data-act=nav][data-v=venta]').click();
    await page.locator('[data-act=abrir-planilla]').click();
    await page.locator('#pin').fill('1234'); await page.locator('#mact-1').click();
    await page.waitForSelector('#pl-body');
    assert.strictEqual(await page.locator('#pl-body tr').count(), 8, 'ocho filas vacías para empezar');
    await foto('01-planilla-vacia');

    // ---- cargar a mano
    const celda = (fila, col) => page.locator('#pl-body tr').nth(fila).locator(`[data-c=${col}]`);
    const llenar = async (fila, v) => { for (const [c, val] of Object.entries(v)) { if (c === 'tipo') await celda(fila, c).selectOption(val); else await celda(fila, c).fill(val); } };
    await llenar(0, { nombre: 'Coca-Cola 500 ml', categoria: 'Kiosco', precio: '2200', costo: '1400', stock: '36', minimo: '6', paquete: 'Bulto 6 x 12', unidades: '72' });
    await llenar(1, { nombre: 'Salchicha', categoria: 'Buffet', tipo: 'insumo', costo: '350', stock: '24', minimo: '6' });
    await llenar(2, { nombre: 'Pan de pancho', categoria: 'Buffet', tipo: 'insumo', costo: '250', stock: '30', minimo: '6' });
    await llenar(3, { nombre: 'Pancho', categoria: 'Buffet', tipo: 'receta', precio: '3500', receta: '1 Salchicha + 1 Inexistente' });
    assert.ok(/revisar/.test(await page.locator('#pl-body tr').nth(3).locator('.pl-st').innerText()), 'la receta con un ingrediente que no existe se marca');
    assert.ok(await page.locator('#pl-guardar').isDisabled(), 'con errores no se puede guardar');
    await foto('02-error-receta');
    await celda(3, 'receta').fill('1 Salchicha + 1 Pan de pancho');
    assert.ok(!(await page.locator('#pl-guardar').isDisabled()), 'corregido, se puede guardar');

    // ---- pegar desde Excel (con títulos): uno nuevo y uno que ya está en la planilla
    await celda(4, 'nombre').focus();
    await page.evaluate(() => {
      const dt = new DataTransfer();
      dt.setData('text/plain', 'Producto\tCategoría\tPrecio\tStock\nAlfajor\tKiosco\t$ 1.200\t40\nCoca-Cola 500 ml\tKiosco\t2400\t36\nCafé\tBuffet\t1800\t');
      document.activeElement.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
    });
    await page.waitForSelector('#pl-body');
    const nombres = await page.locator('#pl-body [data-c=nombre]').evaluateAll(els => els.map(e => e.value).filter(Boolean));
    assert.deepStrictEqual(nombres.sort(), ['Alfajor', 'Café', 'Coca-Cola 500 ml', 'Pan de pancho', 'Pancho', 'Salchicha'].sort(), 'el pegado agregó los nuevos y no duplicó la Coca');
    assert.strictEqual(await ev(() => UI.pl.filas.find(f => f.v.nombre === 'Coca-Cola 500 ml').v.precio), '2400', 'el pegado actualizó el precio de la que ya estaba en la tabla');
    await celda(0, 'precio').fill('2200');
    const res = await page.locator('#pl-resumen').innerText();
    assert.ok(/6 nuevos/.test(res) && /0 para revisar/.test(res), 'resumen: ' + res);
    await foto('03-planilla-cargada');

    // ---- guardar
    await page.locator('#pl-guardar').click();
    await page.locator('#mact-1').click();
    await page.waitForFunction(() => __pos.DB.productos.length === 6);
    const prods = await ev(() => __pos.DB.productos.map(p => ({ n: p.nombre, t: p.tipo, s: p.stock, p: p.precio, c: p.costo, r: p.receta.length })));
    const por = n => prods.find(p => p.n === n);
    assert.deepStrictEqual([por('Coca-Cola 500 ml').s, por('Coca-Cola 500 ml').p, por('Coca-Cola 500 ml').c], [36, 2200, 1400]);
    assert.strictEqual(por('Pancho').t, 'receta'); assert.strictEqual(por('Pancho').r, 2);
    assert.strictEqual(por('Salchicha').s, 24);
    assert.strictEqual(await ev(() => __pos.DB.movimientos.filter(m => m.tipo === 'ingreso').length), 4, 'los que tienen stock entran como ingreso inicial');
    assert.strictEqual(await ev(() => __pos.DB.categorias.length), 4, 'Kiosco y Buffet ya existían: no se duplicaron');
    // el servidor tiene lo mismo
    const srv = await ev(async () => (await fetch('/api/estado?t=' + encodeURIComponent(PREDIO.token))).json());
    assert.strictEqual(srv.db.productos.length, 6); assert.strictEqual(srv.rev, await ev(() => __pos.REV));
    await foto('04-planilla-guardada');

    // ---- editar el stock de uno que existe: queda como ajuste
    await page.locator('[data-act=adm-tab][data-v=planilla]').click();
    const fCoca = page.locator('#pl-body tr', { has: page.locator('[data-c=nombre][value="Coca-Cola 500 ml"]') });
    await fCoca.locator('[data-c=stock]').fill('30');
    assert.ok(/cambió/.test(await fCoca.locator('.pl-st').innerText()));
    await page.locator('#pl-guardar').click(); await page.locator('#mact-1').click();
    await page.waitForFunction(() => __pos.DB.productos.find(p => p.nombre === 'Coca-Cola 500 ml').stock === 30);
    const mv = await ev(() => __pos.DB.movimientos.filter(m => m.tipo === 'ajuste').map(m => [m.nombre, m.cant, m.antes, m.despues, m.motivo]));
    assert.deepStrictEqual(mv, [['Coca-Cola 500 ml', -6, 36, 30, 'Ajuste desde la planilla']]);

    // ---- abrir caja y vender
    await page.locator('[data-act=nav][data-v=venta]').click();
    await page.locator('[data-act=dest][data-id=M]').click();
    await page.locator('.tile', { hasText: 'Coca-Cola' }).click();
    await page.locator('.tile', { hasText: 'Coca-Cola' }).click();
    await page.locator('.tile', { hasText: 'Pancho' }).click();
    await page.locator('[data-act=cobrar]').click();
    await page.locator('#ca-resp').fill('Marcos'); await page.locator('#mact-1').click();       // pide abrir la caja
    await page.locator('[data-act=pg-pct][data-i="0"][data-v="100"]').click();
    await page.locator('#mact-2').click();
    await page.waitForFunction(() => __pos.DB.ventas.length === 1);
    const v = await ev(() => { const x = __pos.DB.ventas[0]; return { nro: x.nro, total: x.total, items: x.items.length, pagos: x.pagos.map(p => p.nombre + ' ' + p.monto) }; });
    assert.deepStrictEqual(v, { nro: 1, total: 7900, items: 2, pagos: ['Efectivo 7900'] });
    assert.deepStrictEqual(await ev(() => ({ coca: __pos.DB.productos.find(p => p.nombre.startsWith('Coca')).stock, salch: __pos.DB.productos.find(p => p.nombre === 'Salchicha').stock, pan: __pos.DB.productos.find(p => p.nombre === 'Pan de pancho').stock })), { coca: 28, salch: 23, pan: 29 });
    await foto('05-venta');
    await page.keyboard.press('Escape');                      // cierra el ticket que se muestra al cobrar

    // ---- una operación que falla en el medio no deja nada a medias (rollback con el servidor)
    const antes = await ev(() => JSON.stringify([__pos.DB.productos.map(p => p.stock), __pos.REV]));
    await page.locator('[data-act=nav][data-v=admin]').click();
    await page.locator('[data-act=adm-tab][data-v=datos]').click();
    await page.locator('[data-act=sim-falla]').click();
    assert.ok(/Todo quedó igual/.test(await page.locator('.modal .note').innerText()));
    await page.locator('#mact-0').click();
    assert.strictEqual(await ev(() => JSON.stringify([__pos.DB.productos.map(p => p.stock), __pos.REV])), antes);

    // ---- el servidor registró todo solo
    const aud = await ev(async () => (await fetch('/api/auditoria?limite=500&t=' + encodeURIComponent(PREDIO.token))).json());
    const textos = aud.filas.map(f => f.resumen);
    assert.ok(textos.some(t => /Ticket #1 cobrado: \$ 7\.900/.test(t)), 'auditoría de la venta');
    assert.ok(textos.some(t => /Stock de Coca-Cola 500 ml: -6 \(ajuste\)/.test(t)), 'auditoría del ajuste de stock');
    assert.ok(textos.some(t => /Caja abierta por Marcos/.test(t)));
    assert.ok(aud.filas.filter(f => f.usuario.startsWith('Marcos')).length > 0, 'quedó anotado quién fue: ' + JSON.stringify([...new Set(aud.filas.map(f => f.usuario))]));
  } else {
    // ---- fase 2: el programa se reinició; todo tiene que seguir ahí
    assert.strictEqual(await ev(() => __pos.DB.productos.length), 6, 'siguen los productos');
    assert.strictEqual(await ev(() => __pos.DB.ventas.length), 1, 'sigue la venta');
    assert.strictEqual(await ev(() => __pos.DB.productos.find(p => p.nombre.startsWith('Coca')).stock), 28);
    assert.ok(await ev(() => !!__pos.DB.cajas.find(c => !c.cerrada)), 'la caja sigue abierta');
    assert.strictEqual(await ev(() => __pos.DB.productos.find(p => p.nombre === 'Pancho').receta.length), 2);
    // el orden de las listas se conserva
    assert.deepStrictEqual(await ev(() => __pos.DB.productos.map(p => p.nombre)), ['Coca-Cola 500 ml', 'Salchicha', 'Pan de pancho', 'Pancho', 'Alfajor', 'Café']);
    // el panel de Datos y seguridad muestra la carpeta y las copias
    await page.locator('[data-act=nav][data-v=admin]').click();
    await page.locator('[data-act=adm-entrar]').click();
    await page.locator('#pin').fill('1234'); await page.locator('#mact-1').click();
    await page.locator('[data-act=adm-tab][data-v=datos]').click();
    const txt = await page.locator('#adm-body').innerText();
    assert.ok(/Dónde están tus datos/.test(txt) && /Copias de seguridad/.test(txt));
    // auditoría automática en pantalla
    await page.locator('[data-act=adm-tab][data-v=auditoria]').click();
    assert.ok(await page.locator('#adm-body tbody tr').count() > 5);
    await page.locator('#adm-body tbody tr').first().locator('[data-act=aud-det]').click();
    assert.ok(/Detalle del cambio/.test(await page.locator('.modal h3').innerText()));
    await foto('06-auditoria');
  }
  assert.deepStrictEqual(errs, [], 'sin errores en la pantalla ni pedidos a internet: ' + JSON.stringify(errs));
  console.log('E2E FASE', FASE, 'OK');
  await b.close();
})().catch(e => { console.error('E2E FALLÓ:', e.message); process.exit(1); });
