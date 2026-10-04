// Tercera prueba de punta a punta: cuentas, anulación de ticket, foto de producto, copias y borrado del historial.
const { chromium } = require(process.env.PLAYWRIGHT_PATH || '/opt/node22/lib/node_modules/playwright');
const assert = require('assert');
const URL = `http://127.0.0.1:${process.env.PORT}/`;
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==', 'base64');

(async () => {
  const b = await chromium.launch({ args: ['--no-sandbox'] });
  const ctx = await b.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const errs = [];
  page.on('pageerror', e => errs.push('PAGEERROR ' + e.message));
  page.on('console', m => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errs.push('CONSOLE ' + m.text()); });
  const ev = (f, a) => page.evaluate(f, a);
  const cargar = async () => { await page.goto(URL); await page.waitForFunction(() => window.__pos && window.__pos.DB); };
  const audit = async (q = '') => (await ev(async q => (await (await fetch('/api/auditoria?limite=500&q=' + encodeURIComponent(q) + '&t=' + encodeURIComponent(PREDIO.token))).json()).filas.map(f => f.resumen), q));
  await cargar();
  const tile = n => page.locator('.tile', { hasText: n });

  // ---- 1. cuenta de una mesa: agregar, bajar y sacar quedan anotados
  await page.locator('select[data-in=dest]').selectOption('m1');
  await tile('Coca').click(); await tile('Coca').click();
  assert.strictEqual(await ev(() => __pos.DB.comandas.m1.items[0].cant), 2);
  await page.locator('[data-act=dec][data-i="0"]').click();
  await page.locator('[data-act=del][data-i="0"]').click();
  const t1 = await audit('Cuenta Mesa 1');
  assert.ok(t1.some(t => /se agregó 1 × Coca-Cola 500 ml/.test(t)), 'agregar: ' + JSON.stringify(t1));
  assert.ok(t1.some(t => /subió de 1 a 2/.test(t)));
  assert.ok(t1.some(t => /bajó de 2 a 1/.test(t)), 'bajar queda anotado');
  assert.ok(t1.some(t => /VACIADA sin cobrar|se sacó 1 × Coca-Cola/.test(t)), 'sacar queda anotado: ' + JSON.stringify(t1));

  // ---- 2. venta y anulación del ticket
  await page.locator('select[data-in=dest]').selectOption('M');
  await tile('Coca').click(); await tile('Coca').click(); await tile('Coca').click();
  await page.locator('[data-act=cobrar]').click();
  await page.locator('[data-act=pg-pct][data-i="0"][data-v="100"]').click();
  await page.locator('#mact-2').click();
  await page.waitForFunction(() => __pos.DB.ventas.length === 1);
  assert.strictEqual(await ev(() => __pos.DB.productos[0].stock), 7);
  await page.keyboard.press('Escape');
  await page.locator('[data-act=nav][data-v=tickets]').click();
  await page.locator('[data-act=tk-ver]').first().click();
  await page.locator('.modal footer button', { hasText: 'Anular ticket' }).click();
  await page.locator('#pin').fill('1234'); await page.locator('#mact-1').click();
  await page.locator('.modal footer button', { hasText: 'Anular ticket' }).click();
  await page.waitForFunction(() => __pos.DB.ventas[0].estado === 'anulada');
  assert.strictEqual(await ev(() => __pos.DB.productos[0].stock), 10, 'se devolvió el stock');
  const t2 = await audit('Ticket #1');
  assert.ok(t2.some(t => /pasó de «ok» a «anulada»/.test(t)), JSON.stringify(t2));
  assert.ok((await audit('Anulación')).length >= 1, 'el stock devuelto también queda anotado');

  // ---- 2b. lápiz: cambiar el precio de UN ítem sólo en esa venta
  await page.locator('[data-act=nav][data-v=venta]').click();
  await page.locator('select[data-in=dest]').selectOption('M');
  await tile('Coca').click(); await tile('Coca').click(); await tile('Pancho').click();
  await page.locator('[data-act=lapiz][data-i="0"]').click();
  if (await page.locator('#pin').count()) { await page.locator('#pin').fill('1234'); await page.locator('#mact-1').click(); }   // pide el código salvo que ya se haya desbloqueado hace poco
  await page.locator('#pi-precio').fill('1800'); await page.locator('#pi-motivo').selectOption('Promoción');
  await page.locator('.modal footer button', { hasText: 'Guardar precio' }).click();
  assert.ok(/\*/.test(await page.locator('.line').first().innerText()), 'el ítem quedó marcado con *');
  assert.strictEqual(await ev(() => totalItems(__pos.DB.comandas.M.items)), 3600 + 3500, 'sólo cambia ese ítem: 2 x 1800 + el pancho a lista');
  assert.strictEqual(await ev(() => __pos.DB.productos[0].precio), 2200, 'el precio del producto no se tocó');
  await tile('Coca').click();                                                                 // otra Coca: va aparte, a precio de lista
  assert.strictEqual(await ev(() => __pos.DB.comandas.M.items.filter(i => i.prodId === 'p1').length), 2);
  await page.locator('[data-act=dec][data-i="2"]').click();                                   // se saca la Coca extra (la de lista)
  await page.locator('[data-act=cobrar]').click();
  await page.locator('[data-act=pg-pct][data-i="0"][data-v="100"]').click();
  await page.locator('#mact-2').click();
  await page.waitForFunction(() => __pos.DB.ventas.length === 2);
  const v2 = await ev(() => { const v = __pos.DB.ventas[1]; return { total: v.total, di: v.descuentoItems, l: v.items[0].precioLista, mp: v.items[0].motivoPrecio, txt: ticketTexto(v) }; });
  assert.deepStrictEqual([v2.total, v2.di, v2.l, v2.mp], [7100, 800, 2200, 'Promoción'], JSON.stringify(v2));
  assert.ok(/Coca-Cola 500 ml \*/.test(v2.txt) && /lista \$2\.200 c\/u/.test(v2.txt), 'el ticket marca el precio modificado');
  await page.keyboard.press('Escape');
  const t2b = await audit('precio');
  assert.ok(t2b.some(t => /PRECIO de Coca-Cola 500 ml cambiado a \$ 1\.800 c\/u/.test(t)) && t2b.some(t => /PRECIO MODIFICADO en Coca-Cola/.test(t)), JSON.stringify(t2b));
  await page.locator('[data-act=nav][data-v=reportes]').click();
  assert.ok(/descuentos otorgados[\s\S]*800/i.test(await page.locator('.kpis').innerText()), 'el total de descuentos del día incluye la diferencia');

  // ---- 2c. Mercado Pago se lee como el efectivo: cuánto debería haber, pérdida y cierre
  await page.locator('[data-act=nav][data-v=venta]').click();
  await page.locator('select[data-in=dest]').selectOption('M');
  await tile('Coca').click();
  await page.locator('[data-act=cobrar]').click();
  await page.locator('[data-act=pg-del][data-i="0"]').click();
  await page.locator('[data-act=pg-add]', { hasText: 'QR' }).click();
  await page.locator('[data-act=pg-pct][data-i="0"][data-v="100"]').click();
  await page.locator('#mact-2').click();
  await page.waitForFunction(() => __pos.DB.ventas.length === 3);
  await page.keyboard.press('Escape');
  await page.locator('[data-act=nav][data-v=caja]').click();
  const qrId = await ev(() => DB.metodos.find(m => /QR/.test(m.nombre)).id);
  assert.strictEqual(await ev(id => saldoDigital(cajaAbierta(), id), qrId), 2200, 'la caja dice cuánto debería haber en el QR');
  assert.ok(/QR[\s\S]*2\.200/.test(await page.locator('#main').innerText()));
  await page.locator('[data-act=pagos-medio]').first().click();                                // lista de pagos para revisar
  assert.ok(/Ticket #3/.test(await page.locator('.modal').innerText()) && await page.locator('.modal [data-act=corr-tk]').count() === 1);
  await page.keyboard.press('Escape');
  await page.locator('[data-act=mv-new][data-t=perdida]').click();                              // pérdida manual en el QR
  await page.locator('#mv-med').selectOption(qrId); await page.locator('#mv-conc').fill('Diferencia sin explicar'); await page.locator('#mv-monto').fill('200');
  await page.locator('#mact-1').click();
  assert.strictEqual(await ev(id => saldoDigital(cajaAbierta(), id), qrId), 2000, 'la pérdida baja lo que debería haber');
  await page.locator('[data-act=caja-cerrar]').click();
  await page.locator('#cc-contado').fill('1000'); await page.locator('#cc-dejado').fill('1000');
  await page.locator('#cd-0').fill('1900');                                                      // la app de Mercado Pago dice otra cosa
  assert.ok(/faltan\s+\$\s?100/.test(await page.locator('#cd-r-0').innerText()) && await page.locator('#mact-1').isDisabled(), 'con diferencia hace falta una nota');
  await page.locator('#cc-nota').fill('Revisé los pagos y no la encontré');
  await page.locator('#mact-1').click();
  await page.waitForFunction(() => !cajaAbierta());
  const dg = await ev(() => DB.cajas[DB.cajas.length - 1].digital.map(d => [d.nombre, d.esperado, d.informado, d.diferencia]));
  assert.deepStrictEqual(dg.find(x => /QR/.test(x[0])), ['MP · QR', 2000, 1900, -100]);
  assert.ok((await audit('Medios digitales')).some(t => /MP · QR debía \$ 2\.000 · faltan \$ 100/.test(t)));
  assert.ok((await audit('PÉRDIDA')).length >= 1);

  // ---- 3. producto con foto: la imagen pasa a ser un archivo de la carpeta imagenes
  await page.locator('[data-act=nav][data-v=admin]').click();
  await page.locator('[data-act=adm-tab][data-v=productos]').click();
  await page.locator('[data-act=prod-new]').click();
  await page.locator('#pf-nombre').fill('Agua con foto'); await page.locator('#pf-precio').fill('1500');
  await page.locator('#pf-file').setInputFiles({ name: 'agua.png', mimeType: 'image/png', buffer: PNG });
  await page.waitForFunction(() => UI.pf && /^data:image/.test(UI.pf.img || ''));
  await page.locator('#mact-1').click();
  await page.waitForFunction(() => __pos.DB.productos.some(p => p.nombre === 'Agua con foto'));
  const img = await ev(() => __pos.DB.productos.find(p => p.nombre === 'Agua con foto').img);
  assert.ok(/^\/img\/[0-9a-f]{20}\.jpg$/.test(img), 'la foto quedó como archivo: ' + img);
  const r = await ev(async u => { const x = await fetch(u); return [x.status, x.headers.get('content-type')]; }, img);
  assert.deepStrictEqual(r, [200, 'image/jpeg']);
  await page.locator('[data-act=nav][data-v=venta]').click();
  await page.waitForFunction(() => { const i = document.querySelector('.tile img.th'); return i && i.complete && i.naturalWidth > 0; }, null, { timeout: 5000 });   // la foto se ve en la caja

  // ---- 4. copias: hacer una, cambiar algo y restaurar
  await page.locator('[data-act=nav][data-v=admin]').click();
  await page.locator('[data-act=adm-tab][data-v=datos]').click();
  await page.locator('[data-act=bk-crear]').click();
  await page.waitForSelector('[data-act=bk-restaurar]');
  const copia = await page.locator('[data-act=bk-restaurar]').first().getAttribute('data-f');
  await ev(() => { __pos.tx(() => { DB.negocio = 'Nombre que se va a deshacer'; }); });
  await page.locator('[data-act=bk-restaurar]', { hasText: 'Restaurar' }).first().click();
  await Promise.all([page.waitForEvent('load', { timeout: 15000 }), page.locator('#mact-1').click()]);      // la pantalla se recarga sola
  await page.waitForFunction(() => window.__pos && window.__pos.DB);
  assert.strictEqual(await ev(() => __pos.DB.negocio), 'Predio Deportivo', 'volvió al nombre de la copia (' + copia + ')');
  assert.strictEqual(await ev(() => __pos.DB.productos.length), 3, 'y conserva el producto con foto, que ya estaba en la copia');
  assert.ok((await audit('Se restauró la copia')).length === 1, 'la restauración quedó anotada');

  // ---- 5. borrar el historial: pide escribir BORRAR, hace copia y deja los productos
  await page.locator('[data-act=nav][data-v=admin]').click();
  await page.locator('[data-act=adm-entrar]').click();
  await page.locator('#pin').fill('1234'); await page.locator('#mact-1').click();
  await page.locator('[data-act=adm-tab][data-v=datos]').click();
  await page.locator('[data-act=reset-hist]').click();
  assert.ok(await page.locator('#mact-1').isDisabled(), 'confirmar está bloqueado hasta escribir la palabra');
  await page.locator('#conf-txt').fill('borrar');
  assert.ok(!(await page.locator('#mact-1').isDisabled()));
  await Promise.all([page.waitForEvent('load', { timeout: 15000 }), page.locator('#mact-1').click()]);
  await page.waitForFunction(() => window.__pos && window.__pos.DB);
  assert.deepStrictEqual(await ev(() => [__pos.DB.ventas.length, __pos.DB.movimientos.length, __pos.DB.contadores.venta, __pos.DB.productos.length]), [0, 0, 0, 3]);
  assert.ok((await audit('SE BORRÓ EL HISTORIAL')).length === 1);
  assert.deepStrictEqual(errs, [], JSON.stringify(errs));
  console.log('E2E FLUJOS OK');
  await b.close();
})().catch(e => { console.error('E2E FLUJOS FALLÓ:', e.message); process.exit(1); });
