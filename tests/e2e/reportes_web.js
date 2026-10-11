// Prueba con navegador real de la web de reportes por período (nube/src/pagina.html) contra una nube local con datos sembrados.
// Uso: URL=http://127.0.0.1:8830 ESPERADO='{...}' node reportes_web.js   (lo arma tests/test_reportes_web.py)
const { chromium } = require(process.env.PLAYWRIGHT_PATH || '/opt/node22/lib/node_modules/playwright');
const assert = require('assert');
const fs = require('fs');
const BASE = process.env.URL.replace(/\/$/, '');
const E = JSON.parse(process.env.ESPERADO);
const FOTOS = process.env.FOTOS || '';
const miles = n => new Intl.NumberFormat('es-AR', { useGrouping: 'always', maximumFractionDigits: 0 }).format(Math.round(n));
const plata = n => '$ ' + miles(n);
const limpio = s => s.replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim();

(async () => {
  const b = await chromium.launch({ args: ['--no-sandbox'] });
  const errs = [];
  const nuevo = async (ancho, alto, esc = 'light') => {
    const ctx = await b.newContext({ viewport: { width: ancho, height: alto }, isMobile: ancho < 600, hasTouch: ancho < 600, colorScheme: esc, acceptDownloads: true });
    const p = await ctx.newPage();
    p.on('pageerror', e => errs.push('PAGEERROR ' + e.message));
    p.on('console', m => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errs.push('CONSOLE ' + m.text()); });
    p.on('request', r => { if (!r.url().startsWith(BASE) && !r.url().startsWith('data:') && !r.url().startsWith('blob:')) errs.push('PEDIDO A INTERNET: ' + r.url()); });
    await p.goto(BASE + '/');
    await p.fill('input[name=clave]', 'clave123');
    await p.click('form button');
    await p.waitForSelector('#app:not([hidden])');
    return { ctx, p };
  };
  const ir = async (p, hash) => {
    await p.goto(BASE + '/#' + hash);
    await p.reload();
    await listo(p);
  };
  const listo = async p => { await p.waitForSelector('#cont:not(.cargando) :is(.card,.kpis)', { timeout: 20000 }); await p.waitForTimeout(150); };
  const texto = (p, sel) => p.locator(sel).first().innerText().then(limpio);
  const sinDesborde = async (p, que) => {
    const r = await p.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]);
    assert.ok(r[0] <= r[1], `${que}: la página se desborda a lo ancho (${r[0]} > ${r[1]})`);
  };
  const foto = (p, n) => FOTOS ? p.screenshot({ path: `${FOTOS}/${n}.png`, fullPage: true }) : null;

  // ============================================================ celular (390 x 844)
  let { ctx, p } = await nuevo(390, 844);
  // ---- semana pasada: lunes sin movimiento, martes y miércoles sin datos, jueves a domingo con ventas
  const S = E.semana;
  await ir(p, 'semana/' + S.lunes);
  assert.strictEqual(await p.locator('.tabs button[aria-selected=true]').innerText(), 'Semana');
  assert.ok(/^Semana del \d+ al \d+ de \w+|^Semana del \d+ de \w+ al \d+ de \w+/.test(await texto(p, '#per')), 'etiqueta de la semana: ' + await texto(p, '#per'));
  assert.ok((await texto(p, '.kpis.p .k:nth-child(1)')).includes(plata(S.total)), 'total vendido de la semana: ' + await texto(p, '.kpis.p .k:nth-child(1)'));
  assert.ok((await texto(p, '.kpis.p .k:nth-child(2)')).includes(miles(S.tickets)), 'tickets');
  assert.ok((await texto(p, '.kpis.p .k:nth-child(1)')).includes(S.delta_total + '% que la semana pasada'), 'comparación: ' + await texto(p, '.kpis.p .k:nth-child(1)') + ' esperaba ' + S.delta_total);
  const alerta = await texto(p, '.alerta');
  assert.ok(alerta.includes('Faltan 2 días de 7'), 'aviso de días que faltan: ' + alerta);
  assert.ok(/no se cuentan como cero|no se cuenta como cero/.test(alerta));
  assert.strictEqual(await p.locator('#graf .col').count(), 7);
  assert.strictEqual(await p.locator('#graf .rayas').count(), 2, 'dos días sin datos marcados');
  await sinDesborde(p, 'semana en celular');
  await foto(p, 'cel_semana');
  // tocar una barra: sin datos y con datos
  const tocar = async i => { await p.locator('#graf').scrollIntoViewIfNeeded(); const bb = await p.locator('#graf .col .sombra').nth(i).boundingBox(); await p.touchscreen.tap(bb.x + bb.width / 2, bb.y + bb.height / 2); };
  await tocar(2);
  let det = await texto(p, '#det');
  assert.ok(/sin datos/i.test(det) && det.toLowerCase().includes(S.miercoles_largo), 'detalle de un día sin datos: ' + det);
  await tocar(0);
  assert.ok(/sin movimiento/.test(await texto(p, '#det')), 'detalle de un día sin movimiento');
  await tocar(4);
  det = await texto(p, '#det');
  assert.ok(det.includes(plata(S.viernes_total)) && det.includes('Ver este día'), 'detalle de un día con ventas: ' + det);
  // teclado en el gráfico
  await p.locator('#graf').focus();
  await p.keyboard.press('ArrowRight');
  assert.ok((await texto(p, '#det')).toLowerCase().includes(S.sabado_largo), 'flechas del teclado: ' + await texto(p, '#det'));
  // cierres de caja del período
  const resu = await texto(p, '#cierres .resu');
  assert.ok(resu.includes(S.cierres_resumen), 'resumen de cierres: ' + resu + ' esperaba ' + S.cierres_resumen);
  assert.strictEqual(await p.locator('#cierres li').count(), S.cierres);
  if (S.cierres_con_dif) {
    await p.locator('#sd').check();
    assert.strictEqual(await p.locator('#cierres li').count(), S.cierres_con_dif, 'sólo con diferencia');
    await p.locator('#sd').uncheck();
  }
  // "de lo vendido a lo cobrado" y secciones
  for (const t of ['De lo vendido a lo cobrado', 'Por medio de pago', 'Por categoría', 'Por destino', 'Lo más vendido', 'Fiado y movimientos de caja', 'Día por día']) {
    assert.ok((await p.locator('#cont').innerText()).toLowerCase().includes(t.toLowerCase()), 'falta la sección ' + t);
  }
  assert.ok((await texto(p, '#cont .k3')).includes('Mejor día'), 'mejor día');
  // CSV de la semana
  let [dl] = await Promise.all([p.waitForEvent('download'), p.click('#csv')]);
  assert.ok(/^reporte_semana_\d{4}-\d{2}-\d{2}_a_\d{4}-\d{2}-\d{2}\.csv$/.test(dl.suggestedFilename()), dl.suggestedFilename());
  let csv = fs.readFileSync(await dl.path());
  assert.deepStrictEqual([...csv.slice(0, 3)], [0xEF, 0xBB, 0xBF], 'el CSV lleva BOM');
  let filas = csv.toString('utf8').slice(1).split('\r\n').filter(Boolean).map(l => l.split(';'));
  assert.strictEqual(filas.length, 8, 'encabezado + una fila por día');
  assert.strictEqual(filas[0][0], 'Fecha'); assert.strictEqual(filas[0].length, 10);
  assert.deepStrictEqual(filas.slice(1).map(f => f[2]), ['Sin movimiento', 'Sin datos', 'Sin datos', 'Con datos', 'Con datos', 'Con datos', 'Con datos']);
  assert.deepStrictEqual(filas[2].slice(3), ['', '', '', '', '', '', ''], 'un día sin datos queda vacío (no cero)');
  assert.strictEqual(Number(filas[4][3]), S.jueves_total);
  assert.strictEqual(filas.slice(1).reduce((a, f) => a + (Number(f[3]) || 0), 0), S.total);
  // navegar: semana anterior / siguiente / esta semana
  const etiqueta0 = await texto(p, '#per');
  await p.click('#ant'); await listo(p);
  assert.notStrictEqual(await texto(p, '#per'), etiqueta0);
  await p.click('#sig'); await listo(p);
  assert.strictEqual(await texto(p, '#per'), etiqueta0);
  await p.click('#hoy'); await listo(p);
  assert.ok(await p.locator('#sig').isDisabled(), 'no se puede avanzar más allá de esta semana');
  assert.strictEqual(await p.locator('#graf .bar.hoy').count(), 1, 'la barra de hoy está resaltada');
  assert.ok(location_hash_ok(await p.evaluate(() => location.hash)));
  await p.click('.tabs button[data-v=dia]'); await listo(p);
  assert.strictEqual(await p.evaluate(() => location.hash), '', 'el día de hoy no deja fecha en la dirección');
  await p.click('.tabs button[data-v=semana]'); await listo(p);
  // "Ver este día" lleva al día
  await ir(p, 'semana/' + S.lunes);
  await tocar(4);
  await p.click('#ver'); await listo(p);
  assert.strictEqual(await p.locator('#fecha').inputValue(), S.viernes);
  assert.ok((await p.locator('#cont').innerText()).toLowerCase().includes('cierres de caja'));
  assert.ok((await p.getAttribute('#xl', 'href')).endsWith('/excel/' + S.viernes), 'el botón del Excel del día sigue');
  await sinDesborde(p, 'día en celular');
  await foto(p, 'cel_dia');
  // día sin datos
  await ir(p, 'dia/' + S.martes);
  assert.ok((await p.locator('#cont').innerText()).includes('Sin datos de este día'));
  // ---- mes pasado
  const M = E.mes;
  await ir(p, 'mes/' + M.ref);
  assert.ok((await texto(p, '#per')).toLowerCase().includes(M.nombre), await texto(p, '#per'));
  assert.ok((await texto(p, '.kpis.p .k:nth-child(1)')).includes(plata(M.total)), 'total del mes: ' + await texto(p, '.kpis.p .k:nth-child(1)') + ' esperaba ' + plata(M.total));
  assert.strictEqual(await p.locator('#graf .col').count(), M.dias);
  assert.strictEqual(await p.locator('#graf .rayas').count(), M.faltan);
  assert.ok((await texto(p, '.kpis.p .k:nth-child(1)')).includes('que el mes pasado'));
  await sinDesborde(p, 'mes en celular');
  await foto(p, 'cel_mes');
  [dl] = await Promise.all([p.waitForEvent('download'), p.click('#csv')]);
  csv = fs.readFileSync(await dl.path()).toString('utf8');
  assert.strictEqual(csv.split('\r\n').filter(Boolean).length, M.dias + 1);
  // ---- fechas libres: 90 días se agrupan por semana; más de 400 días se rechaza con un aviso
  await ir(p, 'fechas/' + E.hoy_menos_89 + '/' + E.hoy);
  assert.ok(await p.locator('#graf .col').count() >= 13 && await p.locator('#graf .col').count() <= 15, 'barras por semana');
  await foto(p, 'cel_fechas');
  await sinDesborde(p, 'fechas en celular');
  await p.fill('#d1', E.hoy_menos_450);
  await p.dispatchEvent('#d1', 'change');
  await p.waitForSelector('text=Es un período muy largo');
  await p.fill('#d1', E.hoy_menos_6);
  await p.dispatchEvent('#d1', 'change'); await listo(p);
  assert.strictEqual(await p.locator('#graf .col').count(), 7);
  // ---- celulares chicos (320 de ancho): ninguna vista se desborda a lo ancho
  await p.setViewportSize({ width: 320, height: 640 });
  for (const h of ['dia/' + S.viernes, 'semana/' + S.lunes, 'mes/' + M.ref, 'fechas/' + E.hoy_menos_89 + '/' + E.hoy, 'semana/' + E.hoy]) { await ir(p, h); await sinDesborde(p, h + ' en 320 px'); }
  await p.setViewportSize({ width: 390, height: 844 });
  // ---- períodos sin nada: no se muestran ceros inventados ni se rompe
  await ir(p, 'fechas/2020-01-01/2020-01-10');
  assert.ok((await p.locator('#cont').innerText()).includes('Sin datos de este período'));
  assert.ok((await texto(p, '.alerta')).includes('Faltan 10 días de 10'));
  assert.ok(await p.locator('#csv').isHidden(), 'sin datos no hay CSV para bajar');
  await ir(p, 'semana/' + E.futuro);
  assert.ok((await p.locator('#cont').innerText()).includes('todavía no empezó'));
  assert.ok(await p.locator('#sig').isDisabled());
  await ir(p, 'fechas/' + E.hoy_menos_6 + '/' + E.hoy);
  // ---- error de conexión: mensaje claro y reintento
  await p.route('**/api/rango*', r => r.abort());
  await p.click('.tabs button[data-v=mes]');
  await p.waitForSelector('.err');
  assert.ok((await texto(p, '.err')).includes('No hay conexión'), await texto(p, '.err'));
  await p.unroute('**/api/rango*');
  await p.click('#reint'); await listo(p);
  assert.ok(await p.locator('#graf .col').count() >= 28);
  // ---- error del servidor
  await p.route('**/api/rango*', r => r.fulfill({ status: 500, contentType: 'application/json', body: '{"error":"boom"}' }));
  await p.click('#ant');
  await p.waitForSelector('.err');
  assert.ok((await texto(p, '.err')).includes('La página tuvo un problema'), await texto(p, '.err'));
  await p.unroute('**/api/rango*');
  // ---- claro y oscuro
  const antes = await p.evaluate(() => getComputedStyle(document.body).backgroundColor);
  await p.click('#tema');
  assert.notStrictEqual(await p.evaluate(() => getComputedStyle(document.body).backgroundColor), antes, 'el botón cambia el tema');
  await ctx.close();

  // ============================================================ PC (1280 x 900), oscuro
  ({ ctx, p } = await nuevo(1280, 900, 'dark'));
  await ir(p, 'semana/' + S.lunes);
  assert.ok(await p.locator('#graf text.v').count() > 0, 'en pantalla grande las barras muestran el valor');
  assert.strictEqual(await p.locator('.kpis.p .k').count(), 5);
  await foto(p, 'pc_semana_oscuro');
  await sinDesborde(p, 'semana en PC');
  await p.locator('#graf .col .sombra').nth(4).hover({ position: { x: 4, y: 4 } });                   // con el mouse alcanza con pasar por arriba
  assert.ok((await texto(p, '#det')).includes(plata(S.viernes_total)), 'detalle al pasar el mouse: ' + await texto(p, '#det'));
  await ctx.close();

  assert.deepStrictEqual(errs, [], 'errores en la consola: ' + errs.join(' | '));
  await b.close();
  console.log('reportes web: todo bien');
})().catch(e => { console.error(e); process.exit(1); });

function location_hash_ok(h) { return h === '#semana'; }          // lo de hoy no lleva fecha en la dirección: un favorito siempre abre lo actual
