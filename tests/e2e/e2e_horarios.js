// Prueba con navegador real: horarios y nombres de la planilla de papel (base nueva y base vieja migrada), calendario, Admin y avisos de turno con pádel hasta las 23:30.
// FASE=nueva: base vacía (el programa carga las canchas con los horarios de papel). FASE=migracion: base con los horarios de ejemplo y turnos ya cargados.
const { chromium } = require(process.env.PLAYWRIGHT_PATH || '/opt/node22/lib/node_modules/playwright');
const assert = require('assert');
const URL = `http://127.0.0.1:${process.env.PORT}/`;
const FASE = process.env.FASE;

(async () => {
  const b = await chromium.launch({ args: ['--no-sandbox'] });
  const ctx = await b.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const errs = [];
  page.on('pageerror', e => errs.push('PAGEERROR ' + e.message));
  page.on('console', m => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errs.push('CONSOLE ' + m.text()); });
  const ev = (f, a) => page.evaluate(f, a);
  const cargar = async () => { await page.goto(URL); await page.waitForFunction(() => window.__pos && window.__pos.DB); };
  const canchas = () => ev(() => __pos.DB.canchas.map(c => ({ clave: c.clave, nombre: c.nombre, dur: c.dur, ap: c.ap, ci: c.ci, precio: c.precio, precioFijo: c.precioFijo })));
  const porClave = lista => Object.fromEntries(lista.map(c => [c.clave, c]));
  const lineasDeMigracion = () => ev(() => __pos.DB.log.filter(l => /Horarios y nombres de las canchas/.test(l.detalle)).map(l => l.detalle));
  await cargar();

  // ---- horarios de la planilla de papel
  let c = porClave(await canchas());
  if (FASE === 'nueva') {
    for (const k of ['f5a', 'f5b', 'f7']) assert.deepStrictEqual([c[k].dur, c[k].ap, c[k].ci], [60, 840, 1380], 'fútbol ' + k);
    for (const k of ['padelT', 'padelA']) assert.deepStrictEqual([c[k].dur, c[k].ap, c[k].ci], [90, 600, 1410], 'pádel ' + k);
    assert.deepStrictEqual(Object.values(c).map(x => x.nombre), ['Fútbol 5 · Cancha 1', 'Fútbol 5 · Cancha 2', 'Fútbol 7 · Cancha completa', 'Pádel · Cancha 1', 'Pádel · Cancha 2']);
    assert.strictEqual(await ev(() => __pos.DB.config.horariosPapel), true, 'la base nueva ya nace con la marca');
    assert.deepStrictEqual(await lineasDeMigracion(), [], 'una base nueva no necesita migrar: no deja nada en el registro');
    assert.deepStrictEqual([c.f5a.precio, c.f5a.precioFijo, c.padelT.precio], [28000, 24000, 26000]);
  } else {
    // base vieja: los precios no se tocan, los horarios y nombres sí, y se amplía lo que haría invisible a un turno ya cargado
    assert.deepStrictEqual(Object.values(c).map(x => x.nombre), ['Fútbol 5 · Cancha 1', 'Fútbol 5 · Cancha 2', 'Fútbol 7 · Cancha completa', 'Pádel · Cancha 1', 'Pádel · Cancha 2']);
    assert.deepStrictEqual([c.f5a.dur, c.f5a.ap, c.f5a.ci, c.f5a.precio, c.f5a.precioFijo], [60, 840, 1380, 31000, 26000], 'f5a: lo cancelado a las 7 no cuenta');
    assert.deepStrictEqual([c.f5b.ap, c.f5b.ci, c.f5b.precio], [600, 1380, 31000], 'f5b: había un turno a las 10:00, se abre desde las 10:00');
    assert.deepStrictEqual([c.f7.ap, c.f7.ci, c.f7.precio], [840, 1440, 50000], 'f7: había un turno de 23:00 a 24:00');
    assert.deepStrictEqual([c.padelT.dur, c.padelT.ap, c.padelT.ci, c.padelT.precio, c.padelT.precioFijo], [90, 600, 1410, 27000, 23000]);
    assert.deepStrictEqual([c.padelA.ap, c.padelA.ci, c.padelA.precio], [600, 1410, 23000], 'padelA: su fijo de las 21:30 entra en el rango nuevo');
    assert.strictEqual(await ev(() => __pos.DB.config.horariosPapel), true);
    const lineas = await lineasDeMigracion();
    assert.strictEqual(lineas.length, 1, 'queda anotado en el registro en una línea');
    assert.ok(/Fútbol 5 · Cancha 2 \(10:00 a 23:00\)/.test(lineas[0]) && /Fútbol 7 · Cancha completa \(14:00 a 24:00\)/.test(lineas[0]) && !/Pádel/.test(lineas[0].split('ampliaron')[1] || ''), lineas[0]);
    // quedó guardado en el programa (no sólo en la pantalla) y no se vuelve a migrar al recargar
    const guardado = await ev(() => http('GET', '/api/estado').db.canchas.map(x => [x.clave, x.nombre, x.ap, x.ci, x.dur, x.precio]));
    assert.deepStrictEqual(guardado.find(x => x[0] === 'padelA'), ['padelA', 'Pádel · Cancha 2', 600, 1410, 90, 23000]);
    assert.deepStrictEqual(guardado.find(x => x[0] === 'f5b').slice(2, 4), [600, 1380]);
    await cargar();
    assert.strictEqual((await lineasDeMigracion()).length, 1, 'al recargar no se migra otra vez');
    assert.deepStrictEqual(porClave(await canchas()).f5b.ap, 600);
    await ev(() => { __pos.tx(() => { __pos.DB.canchas.find(x => x.clave === 'f5a').ap = 900; }); });         // si el dueño cambia un horario en Admin, queda como lo puso
    await cargar();
    assert.strictEqual(porClave(await canchas()).f5a.ap, 900, 'la migración se hizo una sola vez');
    await ev(() => { __pos.tx(() => { __pos.DB.canchas.find(x => x.clave === 'f5a').ap = 840; }); });
  }

  // ---- calendario: una columna por cancha, un casillero por turno, las horas de la grilla
  await page.locator('[data-act=nav][data-v=turnos]').click();
  await page.waitForSelector('.ccol');
  const columnas = await ev(() => [...document.querySelectorAll('.ccol')].map(col => col.querySelectorAll('.slot').length));
  const horas = await ev(() => [...document.querySelectorAll('.tcol span')].map(s => s.textContent));
  if (FASE === 'nueva') {
    assert.deepStrictEqual(columnas, [9, 9, 9, 9, 9], 'fútbol: 14 a 22 (9 turnos de 1 hora); pádel: 9 turnos de 90 minutos');
    assert.deepStrictEqual(horas, ['10:00', '11:00', '12:00', '13:00', '14:00', '15:00', '16:00', '17:00', '18:00', '19:00', '20:00', '21:00', '22:00', '23:00']);
    const pad = await ev(() => [...document.querySelectorAll('.ccol')[3].querySelectorAll('.slot b')].map(x => x.textContent.slice(0, 5)));
    assert.deepStrictEqual(pad, ['10:00', '11:30', '13:00', '14:30', '16:00', '17:30', '19:00', '20:30', '22:00']);
    const fut = await ev(() => [...document.querySelectorAll('.ccol')[0].querySelectorAll('.slot b')].map(x => x.textContent.slice(0, 5)));
    assert.deepStrictEqual(fut, ['14:00', '15:00', '16:00', '17:00', '18:00', '19:00', '20:00', '21:00', '22:00']);
  } else {
    assert.deepStrictEqual(columnas, [9, 13, 10, 9, 9]);
    assert.strictEqual(horas[0], '10:00'); assert.strictEqual(horas[horas.length - 1], '23:00');
    const fecha = await ev(() => __pos.DB.turnos.find(t => t.cliente === 'Temprano').fecha);
    await ev(f => { __pos.UI.turFecha = f; }, fecha);
    await page.locator('[data-act=nav][data-v=turnos]').click();
    await page.waitForSelector('.ccol');
    assert.ok(await page.locator('.ccol >> nth=1').locator('.slot', { hasText: 'Temprano' }).count() === 1, 'el turno de las 10:00 se ve (no quedó invisible)');
    assert.ok(await page.locator('.ccol >> nth=2').locator('.slot', { hasText: 'Noche' }).count() === 1, 'y el de las 23:00 también');
  }

  // ---- Admin: Abre/Cierra muestran el horario real (pádel cierra 23:30, no una hora entera) y se pueden cambiar
  await page.locator('[data-act=nav][data-v=admin]').click();
  await page.locator('[data-act=adm-entrar]').click();
  await page.locator('#pin').fill('1234'); await page.locator('#mact-1').click();
  await page.locator('[data-act=adm-tab][data-v=canchas]').click();
  const idPT = await ev(() => __pos.DB.canchas.find(x => x.clave === 'padelT').id);
  const cierra = page.locator(`select[data-f=ci][data-id="${idPT}"]`);
  assert.strictEqual(await cierra.evaluate(s => s.selectedOptions[0].textContent), '23:30');
  assert.strictEqual(await page.locator(`select[data-f=ap][data-id="${idPT}"]`).evaluate(s => s.selectedOptions[0].textContent), '10:00');
  await cierra.selectOption('23');
  assert.strictEqual(await ev(() => __pos.DB.canchas.find(x => x.clave === 'padelT').ci), 1380);
  await page.locator('[data-act=adm-tab][data-v=fijos]').click(); await page.locator('[data-act=adm-tab][data-v=canchas]').click();      // se vuelve a dibujar la pantalla
  assert.strictEqual(await page.locator(`select[data-f=ci][data-id="${idPT}"] option[value="23.5"]`).count(), 1, 'la opción 23:30 sigue estando: se puede volver al horario de papel');
  assert.deepStrictEqual(await page.locator(`select[data-f=ci][data-id="${idPT}"] option`).evaluateAll(o => o.map(x => x.textContent).slice(-3)), ['23:00', '23:30', '24:00 (medianoche)'], 'las opciones van de media en media hora y en orden');
  await cierra.selectOption('23.5');
  assert.strictEqual(await ev(() => __pos.DB.canchas.find(x => x.clave === 'padelT').ci), 1410, 'volver a elegir 23:30 deja 23:30');
  await page.waitForTimeout(700);                                              // se guarda al dejar de tocar

  // ---- avisos: pádel de las 22:00 (termina 23:30). Empieza a las 22:00 y la cuenta de la cancha se arma sola; a las 23:30 la cancha queda libre.
  await ev(() => {
    __pos.tx(() => {
      __pos.DB.mesas.push({ id: 'mpad1', nombre: 'PÁDEL 1', tipo: 'padel', x: 59, y: 3, w: 18, h: 14, rapido: false });
      __pos.DB.canchas.find(x => x.clave === 'padelT').lugarId = 'mpad1';
    });
  });
  const hoy = await ev(() => __pos.dayKey(Date.now()));
  const idNoche = await ev(h => { let id; __pos.tx(() => { id = __pos.crearTurno({ canchaId: __pos.DB.canchas.find(x => x.clave === 'padelT').id, fecha: h, ini: 1320, cliente: 'Los de las diez', tel: '', nota: '', pagos: [] }); }); return id; }, hoy);
  const t = await ev(id => { const x = __pos.DB.turnos.find(y => y.id === id); return [x.ini, x.fin]; }, idNoche);
  assert.deepStrictEqual(t, [1320, 1410], 'dura 90 minutos: de 22:00 a 23:30');
  const a = (h, m) => new Date(hoy + 'T' + String(h).padStart(2, '0') + ':' + String(m).padStart(2, '0') + ':00').getTime();
  const reloj = ms => ev(x => { window.__ahora = x; if (!window.__dateNowOriginal) window.__dateNowOriginal = Date.now; Date.now = () => window.__ahora; }, ms);
  await reloj(a(21, 59)); await ev(() => { __pos.revisarTurnos(); });
  assert.ok(!(await ev(() => (__pos.DB.comandas.mpad1 || { items: [] }).items.some(i => i.turnoId))), 'a las 21:59 todavía no empezó');
  await reloj(a(22, 5)); await ev(() => { __pos.revisarTurnos(); });
  const cuenta = await ev(id => { const k = __pos.DB.comandas.mpad1; return k && { tiene: k.items.some(i => i.turnoId === id), fin: k.turnoFin, label: k.turnoLabel }; }, idNoche);
  assert.ok(cuenta && cuenta.tiene && cuenta.label === '22:00', 'a las 22:05 el turno empezó y su costo está en la cuenta de la cancha');
  assert.strictEqual(cuenta.fin, a(23, 30), 'termina a las 23:30');
  await reloj(a(23, 29)); await ev(() => { __pos.revisarTurnos(); });
  assert.ok(await ev(() => !!__pos.DB.comandas.mpad1), 'a las 23:29 la cuenta sigue en la cancha');
  await reloj(a(23, 31)); await ev(() => { __pos.revisarTurnos(); });
  assert.ok(await ev(() => !__pos.DB.comandas.mpad1 && __pos.DB.mesas.some(m => m.temporal && /PÁDEL 1 22:00hs/.test(m.nombre))), 'a las 23:31 la cancha queda libre y la cuenta pasa a una mesa suelta');
  await ev(() => { Date.now = window.__dateNowOriginal; });

  assert.deepStrictEqual(errs, [], JSON.stringify(errs));
  console.log('E2E HORARIOS OK ' + FASE);
  await b.close();
})().catch(e => { console.error('E2E HORARIOS FALLÓ:', e.message); process.exit(1); });
