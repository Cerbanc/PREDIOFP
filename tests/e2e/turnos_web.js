// Pruebas de la web de turnos con un navegador real (Playwright) contra la nube local (ver tests/nube_local.py).
// Uso: PORT=8822 ESCENARIO=buscar node turnos_web.js      (FOTOS=/carpeta guarda capturas)
// Escenarios: dia_semana, reservar, concurrencia, buscar, recordatorios, refresco_errores, registro_temas
const { chromium } = require(process.env.PLAYWRIGHT_PATH || '/opt/node22/lib/node_modules/playwright');
const assert = require('assert');
const B = `http://127.0.0.1:${process.env.PORT}`;
const ESCENARIO = process.env.ESCENARIO || 'todos';
const FOTOS = process.env.FOTOS || '';
const TZ = 'America/Argentina/Buenos_Aires';

// ---- fechas (siempre hora de Argentina, igual que el navegador de las pruebas) ----
const hoy = () => new Date(Date.now() - 3 * 36e5).toISOString().slice(0, 10);
const sumar = (f, n) => new Date(new Date(f + 'T12:00:00Z').getTime() + n * 864e5).toISOString().slice(0, 10);
const F = n => sumar(hoy(), n);
const lunesDe = f => sumar(f, -((new Date(f + 'T12:00:00Z').getUTCDay() + 6) % 7));
const H = (h, m = 0) => h * 60 + m;
const hora = (h, m = 0) => new Date(`${hoy()}T${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:00-03:00`);

const CANCHAS = [
  { id: 'f5a', nombre: 'Fútbol 5 · Cancha 1', deporte: 'futbol', dur: 60, ap: 840, ci: 1380, precio: 28000, precio_fijo: 24000, orden: 1 },
  { id: 'f5b', nombre: 'Fútbol 5 · Cancha 2', deporte: 'futbol', dur: 60, ap: 840, ci: 1380, precio: 28000, precio_fijo: 24000, orden: 2 },
  { id: 'f7', nombre: 'Fútbol 7 · Cancha completa', deporte: 'futbol', dur: 60, ap: 840, ci: 1380, precio: 48000, precio_fijo: 42000, orden: 3 },
  { id: 'padelT', nombre: 'Pádel · Techada', deporte: 'padel', dur: 90, ap: 600, ci: 1410, precio: 26000, precio_fijo: 22000, orden: 4 },
  { id: 'padelA', nombre: 'Pádel · Abierta', deporte: 'padel', dur: 90, ap: 600, ci: 1410, precio: 22000, precio_fijo: 19000, orden: 5 },
];

let cookie = '';
async function entrar() {
  const r = await fetch(B + '/entrar', { method: 'POST', body: new URLSearchParams({ clave: 'clave123' }), redirect: 'manual' });
  cookie = r.headers.get('set-cookie').split(';')[0].split('=')[1];
}
async function api(met, ruta, cuerpo, pc) {
  const r = await fetch(B + ruta, { method: met, headers: { 'content-type': 'application/json', ...(pc ? { authorization: 'Bearer tokenpc' } : { cookie: 's=' + cookie }) }, body: cuerpo ? JSON.stringify(cuerpo) : undefined });
  return { status: r.status, ...(await r.json().catch(() => ({}))) };
}
async function reservar(cancha, fecha, ini, cliente, tel = '', quien = 'Ana', extra = {}) {
  const r = await api('POST', '/api/turnos', { accion: 'crear', cancha, fecha, ini, cliente, tel, quien, ...extra });
  assert.strictEqual(r.status, 200, `reservar ${cancha} ${fecha} ${ini}: ${JSON.stringify(r)}`);
}
const turnosDe = async f => (await api('GET', '/api/turnos?fecha=' + f)).turnos;

let navegador;
const abiertos = [];
async function abrir({ ancho = 390, alto = 844, tema = 'light', quien = 'Ana', reloj = null, ruta = '/turnos', sinNombre = false, antes = null } = {}) {
  const movil = ancho < 700;
  const ctx = await navegador.newContext({ viewport: { width: ancho, height: alto }, colorScheme: tema, locale: 'es-AR', timezoneId: TZ, hasTouch: movil, isMobile: movil, serviceWorkers: 'block' });
  await ctx.addCookies([{ name: 's', value: cookie, url: B }]);
  if (!sinNombre) await ctx.addInitScript(q => { try { if (!localStorage.getItem('quien')) localStorage.setItem('quien', q); } catch (e) {} }, quien);
  const page = await ctx.newPage();
  const errs = [];
  page.on('pageerror', e => errs.push('PAGEERROR ' + e.message));
  page.on('console', m => { if (m.type() === 'error' && !/Failed to load resource|net::ERR/.test(m.text())) errs.push('CONSOLE ' + m.text()); });
  page.on('request', r => { if (!r.url().startsWith(B) && !r.url().startsWith('data:')) errs.push('PEDIDO A INTERNET: ' + r.url()); });
  if (reloj) await page.clock.install({ time: reloj });
  if (antes) await antes(page, ctx);
  await page.goto(B + ruta);
  const c = { ctx, page, errs };
  abiertos.push(c);
  return c;
}
const dia = async (c, f) => { await c.page.fill('#fecha', f); await c.page.waitForFunction(f => document.querySelector('#est')?.textContent && document.querySelector('#fecha').value === f, f); await esperarGrilla(c); };
const esperarGrilla = async c => { await c.page.waitForSelector('#cal .slot', { timeout: 15000 }); await c.page.waitForFunction(() => !document.querySelector('#cal.espera')); };
const foto = async (c, nombre) => { if (FOTOS) await c.page.screenshot({ path: `${FOTOS}/${nombre}.png` }); };
const sinErrores = c => assert.deepStrictEqual(c.errs, [], 'errores en la página: ' + c.errs.join(' | '));
const texto = (c, sel) => c.page.locator(sel).first().innerText();
const sinDesborde = async (c, donde) => assert.ok(await c.page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), 'la página se sale de la pantalla (scroll horizontal): ' + donde);
const altoDe = (c, sel) => c.page.evaluate(s => { const hs = [...document.querySelectorAll(s)].filter(e => e.offsetParent !== null).map(e => e.getBoundingClientRect().height); return hs.length ? Math.min(...hs) : 0; }, sel);
async function esperarModal(c) { await c.page.waitForSelector('.fondo .modal'); }
async function cerrarModal(c) { await c.page.click('.fondo .x'); await c.page.waitForSelector('.fondo', { state: 'detached' }); }
const toast = async (c, re) => { await c.page.waitForFunction(r => [...document.querySelectorAll('.toast')].some(t => new RegExp(r).test(t.textContent)), re.source, { timeout: 10000 }); };

// =====================================================================================================
async function dia_semana() {
  // ---- el reloj de la pantalla: 15:20 de hoy
  const c = await abrir({ reloj: hora(15, 20) });
  await esperarGrilla(c);
  assert.strictEqual(await c.page.locator('.tabs [aria-selected=true]').getAttribute('data-v'), 'dia');
  assert.strictEqual(await c.page.locator('#cal .ahora').count(), 5, 'la raya de «ahora» está en las cinco canchas');
  assert.strictEqual((await texto(c, '#cal .ahora-t')).trim(), '15:20');
  const top = await c.page.locator('#cal .ahora').first().evaluate(e => parseFloat(e.style.top));
  assert.ok(Math.abs(top - (15 * 60 + 20 - 600) / 60 * 52) < 1.5, 'la raya está en la hora de ahora (eje desde las 10:00): ' + top);
  assert.ok(await c.page.locator('#cal').evaluate(e => e.scrollTop) > 150, 'al abrir hoy la grilla se ubica cerca de la hora actual');
  assert.ok(await c.page.locator('.slot.free[data-c=f5a][data-s="840"]').evaluate(e => e.classList.contains('past')), 'las 14:00 ya pasaron');
  assert.ok(!(await c.page.locator('.slot.free[data-c=f5a][data-s="900"]').evaluate(e => e.classList.contains('past'))), 'las 15:00 están en curso');
  assert.ok((await texto(c, '#est')).includes('(hoy)'));
  // la raya se corre sola con el reloj
  await c.page.clock.fastForward('10:00');
  await c.page.waitForFunction(() => document.querySelector('#cal .ahora-t')?.textContent === '15:30', null, { timeout: 25000 });
  await foto(c, 'm_dia_hoy');
  await sinDesborde(c, 'día');

  // ---- horarios nuevos de las canchas, sin nada escrito a mano: un día vacío
  const vacio = F(60);
  await dia(c, vacio);
  const horarios = id => c.page.$$eval(`.slot.free[data-c="${id}"]`, es => es.map(e => +e.dataset.s));
  assert.deepStrictEqual(await horarios('f5a'), [840, 900, 960, 1020, 1080, 1140, 1200, 1260, 1320], 'fútbol 14:00 a 23:00 de a 60');
  assert.deepStrictEqual(await horarios('f7'), [840, 900, 960, 1020, 1080, 1140, 1200, 1260, 1320]);
  assert.deepStrictEqual(await horarios('padelT'), [600, 690, 780, 870, 960, 1050, 1140, 1230, 1320], 'pádel 10:00 a 23:30 de a 90');
  assert.deepStrictEqual(await horarios('padelA'), [600, 690, 780, 870, 960, 1050, 1140, 1230, 1320]);
  assert.ok((await c.page.locator('#cal .cerrada').count()) >= 3, 'fuera del horario de cada cancha la grilla se ve cerrada (rayada)');
  assert.strictEqual(await c.page.locator('#cal .ahora').count(), 0, 'otro día no tiene raya de ahora');
  assert.strictEqual(await c.page.locator('#cal').evaluate(e => e.scrollTop), 0, 'otro día: arriba, donde abre la primera cancha');

  // ---- la semana: de un vistazo (una semana lejana y sin fijos: los fijos se repiten para siempre y ensuciarían a las otras pruebas)
  const lu = lunesDe(F(100));
  const d = n => sumar(lu, n);
  await reservar('f5a', d(0), H(20), 'Lunes Uno', '11 1111-1111');
  await reservar('f5b', d(0), H(20), 'Lunes Dos');
  await reservar('f7', d(1), H(21), 'Siete Martes');
  await reservar('padelT', d(2), H(11, 30), 'Pádel Miércoles');
  await c.page.click('.tabs [data-v=semana]');
  await c.page.waitForSelector('#sem .dcard');
  await c.page.fill('#sfecha', d(3));
  await c.page.waitForFunction(f => document.querySelector('#sem .dcard')?.dataset.f === f, lu);
  assert.strictEqual(await c.page.locator('#sem .dcard').count(), 7);
  assert.strictEqual(await c.page.locator('#sem .dp').count(), 7);
  assert.deepStrictEqual(await c.page.$$eval('#sem .dp .n', es => es.map(e => +e.textContent)), [2, 1, 1, 0, 0, 0, 0], 'turnos por día en la tira de arriba');
  assert.ok((await texto(c, '#sest')).startsWith('Semana del '), await texto(c, '#sest'));
  assert.ok((await texto(c, '#sest')).includes('4 turnos'));
  const lunes = c.page.locator(`#sem .dcard[data-f="${d(0)}"]`);
  assert.ok((await lunes.innerText()).includes('2 turnos'));
  const fila = (card, nombre) => card.locator('.cfila').filter({ hasText: nombre });
  assert.ok((await fila(lunes, 'Fútbol 5 · Cancha 1').innerText()).includes('1 turno · 8 libres'), await fila(lunes, 'Fútbol 5 · Cancha 1').innerText());
  assert.strictEqual(await fila(lunes, 'Fútbol 5 · Cancha 1').locator('.chip.ocu').count(), 1);
  assert.strictEqual((await fila(lunes, 'Fútbol 5 · Cancha 1').locator('.chip.ocu').innerText()).trim(), '20');
  assert.strictEqual(await fila(lunes, 'Fútbol 5 · Cancha 1').locator('.chip.lib').count(), 8);
  assert.deepStrictEqual(await fila(lunes, 'Fútbol 7').locator('.chip.blk').allInnerTexts(), ['20'], 'Fútbol 7 queda bloqueado cuando hay un Fútbol 5');
  // Fútbol 7 el martes a las 21: ocupa las dos de Fútbol 5 (casillas rayadas)
  const martes = c.page.locator(`#sem .dcard[data-f="${d(1)}"]`);
  assert.strictEqual((await fila(martes, 'Fútbol 7').locator('.chip.ocu').innerText()).trim(), '21');
  for (const n of ['Fútbol 5 · Cancha 1', 'Fútbol 5 · Cancha 2']) assert.strictEqual((await fila(martes, n).locator('.chip.blk').innerText()).trim(), '21', n);
  assert.strictEqual(await fila(martes, 'Pádel · Techada').locator('.chip.blk, .chip.ocu').count(), 0);
  // pádel: casillas de 1h30 (11:30)
  const miercoles = c.page.locator(`#sem .dcard[data-f="${d(2)}"]`);
  assert.strictEqual((await fila(miercoles, 'Pádel · Techada').locator('.chip.ocu').innerText()).trim(), '11:30');
  assert.deepStrictEqual(await fila(miercoles, 'Pádel · Abierta').locator('.chip').allInnerTexts(), ['10', '11:30', '13', '14:30', '16', '17:30', '19', '20:30', '22']);
  assert.ok((await martes.innerText()).includes('1 turno') && (await c.page.locator(`#sem .dcard[data-f="${d(5)}"]`).innerText()).includes('sin turnos'));
  await sinDesborde(c, 'semana'); await foto(c, 'm_semana');
  assert.strictEqual(await c.page.evaluate(() => localStorage.getItem('vista')), 'semana');
  // tocar un día lo abre en la vista de día
  await martes.click();
  await c.page.waitForSelector('#v-dia:not([hidden]) .slot');
  assert.strictEqual(await c.page.inputValue('#fecha'), d(1));
  assert.strictEqual(await c.page.locator('.tabs [aria-selected=true]').getAttribute('data-v'), 'dia');
  await c.page.waitForSelector('#cal .slot.res');
  assert.ok((await texto(c, '#cal .slot.res')).includes('Siete Martes'));
  assert.ok((await texto(c, '#est')).startsWith('martes'), await texto(c, '#est'));
  // y la semana siguiente con la flecha
  await c.page.click('.tabs [data-v=semana]');
  await c.page.waitForSelector('#sem .dcard');
  await c.page.click('#ssig');
  await c.page.waitForFunction(f => document.querySelector('#sem .dcard')?.dataset.f === f, d(7));
  assert.strictEqual(await c.page.locator('#sem .chip.ocu').count(), 0);
  assert.strictEqual(await c.page.locator('#sem .dcard.hoy').count(), 0);
  sinErrores(c);

  // ---- en la compu: la semana en varias columnas y la grilla del día entra entera
  const pc = await abrir({ ancho: 1280, alto: 800 });
  await esperarGrilla(pc);
  assert.ok(await pc.page.locator('#cal').evaluate(e => e.scrollWidth <= e.clientWidth + 1), 'en la compu las cinco canchas entran sin scroll horizontal');
  assert.strictEqual(await pc.page.locator('.tabs').evaluate(e => getComputedStyle(e).position), 'static', 'en la compu las pestañas van arriba');
  await foto(pc, 'pc_dia');
  await pc.page.click('.tabs [data-v=semana]');
  await pc.page.waitForSelector('#sem .dcard');
  const tops = await pc.page.$$eval('#sem .dcard', es => es.slice(0, 3).map(e => Math.round(e.getBoundingClientRect().top)));
  assert.ok(tops[0] === tops[1] && tops[1] === tops[2], 'tres días por fila en la compu: ' + tops);
  await foto(pc, 'pc_semana'); sinErrores(pc);
}

// =====================================================================================================
async function reservar_escenario() {
  const f = F(31);
  // ---- el nombre se pregunta una sola vez por celular
  const n = await abrir({ sinNombre: true });
  await n.page.waitForSelector('.fondo .modal');
  assert.ok((await texto(n, '.fondo h2')).includes('¿Cómo te llamás?'));
  assert.strictEqual(await n.page.locator('.fondo .x').count(), 0, 'no se puede cerrar sin decir el nombre');
  assert.strictEqual(await n.page.evaluate(() => document.activeElement.id), 'nm', 'el cursor ya está en el nombre');
  await n.page.keyboard.press('Escape');
  await n.page.mouse.click(5, 5);
  assert.strictEqual(await n.page.locator('.fondo').count(), 1, 'ni con Escape ni tocando afuera se cierra');
  await n.page.click('#nm');
  await n.page.keyboard.type('Carolina');
  await n.page.keyboard.press('Enter');
  await n.page.waitForSelector('.fondo', { state: 'detached' });
  assert.ok((await texto(n, '#quien-txt')).includes('Anotás como Carolina'));
  await n.page.reload();
  await esperarGrilla(n);
  assert.strictEqual(await n.page.locator('.fondo').count(), 0, 'la segunda vez no pregunta');
  await n.ctx.close();

  // ---- reservar con una mano
  const c = await abrir({ quien: 'Ana' });
  await esperarGrilla(c);
  await dia(c, f);
  await c.page.click('.slot.free[data-c=f5a][data-s="1080"]');
  await esperarModal(c);
  assert.ok((await texto(c, '.fondo h2')).startsWith('Reservar · Fútbol 5 · Cancha 1'));
  const vp = c.page.viewportSize();
  const caja = await c.page.locator('.fondo .modal').boundingBox();
  assert.ok(Math.abs(caja.y + caja.height - vp.height) < 2, 'la ventana sube desde abajo (al alcance del pulgar)');
  const bReservar = await c.page.locator('.fondo .acc .pri').boundingBox();
  assert.ok(bReservar.y > vp.height * 0.6 && bReservar.height >= 48, 'el botón Reservar está abajo y es grande: ' + JSON.stringify(bReservar));
  assert.ok((await altoDe(c, '.fondo input:not([type=radio]), .fondo .tipo span')) >= 48 - 1, 'campos cómodos para tocar');
  assert.strictEqual(await c.page.evaluate(() => document.activeElement.id), 'cli', 'ya se puede escribir el nombre');
  assert.ok((await texto(c, '.fondo .modal')).includes('también quedan ocupadas: Fútbol 7'), 'avisa que se ocupa también Fútbol 7');
  await foto(c, 'm_nuevo');
  await c.page.keyboard.type('Los Pruebas');
  await c.page.keyboard.press('Enter');                                   // Enter pasa al teléfono
  await c.page.keyboard.type('011 15 4444-5555');
  await c.page.keyboard.press('Enter');                                   // y a la nota
  await c.page.keyboard.type('trae pelota');
  await c.page.keyboard.press('Enter');                                   // Enter en la última casilla reserva
  await toast(c, /Turno reservado/);
  await c.page.waitForSelector('.fondo', { state: 'detached' });
  const mio = c.page.locator('#cal .slot.res', { hasText: 'Los Pruebas' });
  await mio.waitFor();
  assert.ok((await mio.innerText()).includes('sin pago'));
  const guardado = (await turnosDe(f)).find(t => t.cliente === 'Los Pruebas');
  assert.ok(guardado && guardado.creado_por === 'Ana' && guardado.modificado_por === 'Ana' && guardado.tel === '011 15 4444-5555' && guardado.nota === 'trae pelota' && guardado.precio === 28000);
  // la casilla de Fútbol 7 de esa hora quedó marcada como ocupada por Fútbol 5
  assert.ok((await texto(c, '#cal .slot.blk')).includes('ocupada por Fútbol 5 · Cancha 1'));

  // ---- el detalle dice quién lo cargó
  await mio.click();
  await esperarModal(c);
  const det = await texto(c, '.fondo .modal');
  assert.ok(/Cargado por Ana el \d\d\/\d\d \d\d:\d\d/.test(det), det);
  assert.ok(!det.includes('Último cambio'), 'recién cargado: no hubo otro cambio');
  const wa = await c.page.locator('.fondo a.wa').getAttribute('href');
  assert.ok(wa.startsWith('https://wa.me/5491144445555?text='), wa);
  await cerrarModal(c);

  // ---- otro celular (Beto) lo edita; se ve quién fue
  const b = await abrir({ quien: 'Beto' });
  await esperarGrilla(b); await dia(b, f);
  await b.page.locator('#cal .slot.res', { hasText: 'Los Pruebas' }).click();
  await esperarModal(b);
  await b.page.click('.fondo .acc .pri');                                 // Editar datos
  await b.page.waitForSelector('.fondo h2:has-text("Editar")');
  await b.page.fill('#cli', 'Los Pruebas II');
  await b.page.click('.fondo .acc .pri');
  await toast(b, /Guardado/);
  await b.page.locator('#cal .slot.res', { hasText: 'Los Pruebas II' }).click();
  await esperarModal(b);
  const det2 = await texto(b, '.fondo .modal');
  assert.ok(/Cargado por Ana el/.test(det2) && /Último cambio de Beto el \d\d\/\d\d \d\d:\d\d/.test(det2), det2);
  await b.ctx.close();
  // en el celular de Ana, al volver a la pestaña se actualiza sola
  await c.page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  await c.page.locator('#cal .slot.res', { hasText: 'Los Pruebas II' }).waitFor({ timeout: 10000 });

  // ---- cancelar pide confirmar tocando dos veces
  await c.page.locator('#cal .slot.res', { hasText: 'Los Pruebas II' }).click();
  await esperarModal(c);
  const cancelar = c.page.locator('.fondo .acc button', { hasText: 'Cancelar turno' });
  await cancelar.click();
  assert.ok((await c.page.locator('.fondo .acc .dan').first().innerText()).includes('Tocá de nuevo'));
  assert.ok((await turnosDe(f)).some(t => t.cliente === 'Los Pruebas II'), 'un solo toque no cancela');
  await c.page.locator('.fondo .acc .dan').first().click();
  await toast(c, /Listo/);
  await c.page.waitForSelector('.fondo', { state: 'detached' });
  assert.ok(!(await turnosDe(f)).some(t => t.cliente === 'Los Pruebas II'));
  await c.page.waitForFunction(() => !document.querySelector('#cal .slot.res'));

  // ---- reprogramar: la hora se elige de la lista de la cancha
  await reservar('f5b', f, H(17), 'Se Muda', '11 5555-6666');
  await c.page.waitForTimeout(900);                                       // dos avisos de «volviste a la pestaña» pegados cuentan como uno
  await c.page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  await c.page.locator('#cal .slot.res', { hasText: 'Se Muda' }).waitFor({ timeout: 10000 });
  await c.page.locator('#cal .slot.res', { hasText: 'Se Muda' }).click();
  await esperarModal(c);
  await c.page.locator('.fondo .acc button', { hasText: 'Reprogramar' }).click();
  await c.page.waitForSelector('#rh');
  assert.deepStrictEqual(await c.page.$$eval('#rh option', os => os.map(o => +o.value)), [840, 900, 960, 1020, 1080, 1140, 1200, 1260, 1320]);
  assert.strictEqual(await c.page.inputValue('#rh'), String(H(17)), 'arranca en la hora actual del turno');
  await c.page.selectOption('#rc', 'padelT');
  assert.deepStrictEqual(await c.page.$$eval('#rh option', os => os.map(o => +o.value)), [600, 690, 780, 870, 960, 1050, 1140, 1230, 1320], 'al cambiar de cancha cambian los horarios');
  assert.ok((await c.page.locator('#rh option:checked').innerText()).includes('a '), 'cada horario muestra de qué hora a qué hora');
  await c.page.selectOption('#rh', String(H(20, 30)));
  await c.page.locator('.fondo .acc .pri').click();
  await toast(c, /reprogramado/);
  const movido = (await turnosDe(f)).find(t => t.cliente === 'Se Muda');
  assert.ok(movido.cancha_id === 'padelT' && movido.ini === H(20, 30) && movido.fin === H(22) && movido.creado_por === 'Ana' && movido.modificado_por === 'Ana');

  // ---- un turno fijo
  await c.page.click('.slot.free[data-c=f5b][data-s="900"]');
  await esperarModal(c);
  const diaSemana = ['domingos', 'lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábados'][new Date(f + 'T12:00:00Z').getUTCDay()];
  const opcionFijo = await texto(c, '.fondo .tipo label:nth-child(2)');
  assert.ok(opcionFijo.includes('todos los ' + diaSemana) && /\$\s*24\.000/.test(opcionFijo), opcionFijo);
  await c.page.keyboard.type('Fijo De Prueba');
  await c.page.locator('.fondo .tipo label:nth-child(2)').click();
  await c.page.locator('.fondo .acc .pri').click();
  await toast(c, /Turno reservado/);
  await c.page.locator('#cal .slot.res.fijo', { hasText: 'Fijo De Prueba' }).waitFor();
  assert.ok((await turnosDe(sumar(f, 14))).some(t => t.cliente === 'Fijo De Prueba' && t.virtual && t.creado_por === 'Ana'), 'el fijo se repite cada semana');
  await c.page.locator('#cal .slot.res.fijo', { hasText: 'Fijo De Prueba' }).click();
  await esperarModal(c);
  assert.ok((await texto(c, '.fondo .modal')).includes('FIJO') && /Cargado por Ana/.test(await texto(c, '.fondo .modal')));
  await cerrarModal(c);
  // en la semana el fijo figura con su marca, y se repite la semana siguiente
  await c.page.click('.tabs [data-v=semana]');
  await c.page.waitForSelector('#sem .dcard');
  const hoyFijo = c.page.locator(`#sem .dcard[data-f="${f}"] .cfila`, { hasText: 'Fútbol 5 · Cancha 2' });
  assert.strictEqual(await hoyFijo.locator('.chip.ocu.fijo').count(), 1);
  await c.page.click('#ssig');
  await c.page.waitForFunction(x => document.querySelector(`#sem .dcard[data-f="${x}"]`), sumar(f, 7));
  assert.strictEqual(await c.page.locator(`#sem .dcard[data-f="${sumar(f, 7)}"] .cfila`, { hasText: 'Fútbol 5 · Cancha 2' }).locator('.chip.ocu.fijo').count(), 1);
  sinErrores(c);
}

// =====================================================================================================
async function concurrencia() {
  const f = F(32);
  const a = await abrir({ quien: 'Ana' }), b = await abrir({ quien: 'Beto' });
  for (const x of [a, b]) { await esperarGrilla(x); await dia(x, f); }
  // dos celulares quieren la misma cancha a la misma hora
  for (const [x, nombre] of [[a, 'Cliente de Ana'], [b, 'Cliente de Beto']]) {
    await x.page.click('.slot.free[data-c=f5a][data-s="1200"]');
    await esperarModal(x);
    await x.page.fill('#cli', nombre);
  }
  await Promise.all([a.page.click('.fondo .acc .pri'), b.page.click('.fondo .acc .pri')]);
  await Promise.all([a, b].map(x => x.page.waitForFunction(() => document.querySelector('#m-err')?.textContent || !document.querySelector('.fondo'), null, { timeout: 15000 })));
  const resultado = await Promise.all([a, b].map(async x => ({ x, error: await x.page.locator('#m-err').count() ? (await x.page.locator('#m-err').innerText()).trim() : '' })));
  const perdedores = resultado.filter(r => r.error), ganadores = resultado.filter(r => !r.error);
  assert.strictEqual(perdedores.length, 1, 'uno solo pierde: ' + JSON.stringify(resultado.map(r => r.error)));
  assert.strictEqual(ganadores.length, 1);
  assert.ok(/ocupado/.test(perdedores[0].error), perdedores[0].error);
  const en = (await turnosDe(f)).filter(t => t.cancha_id === 'f5a' && t.ini === H(20));
  assert.strictEqual(en.length, 1, 'quedó un solo turno');
  const quienGano = en[0].creado_por;
  assert.strictEqual(quienGano, ganadores[0].x === a ? 'Ana' : 'Beto');
  await perdedores[0].x.page.waitForFunction(n => [...document.querySelectorAll('#cal .slot.res')].some(e => e.textContent.includes(n)), en[0].cliente, { timeout: 10000 });
  await foto(perdedores[0].x, 'm_perdio');
  await perdedores[0].x.page.click('.fondo .acc button:not(.pri)');

  // Fútbol 7 contra Fútbol 5: Ana tiene abierta la hora de Fútbol 5 mientras Beto toma Fútbol 7
  await a.page.click('.slot.free[data-c=f5b][data-s="1260"]');
  await esperarModal(a);
  await a.page.fill('#cli', 'Quiere Fútbol 5');
  await b.page.click('.slot.free[data-c=f7][data-s="1260"]');
  await esperarModal(b);
  assert.ok((await texto(b, '.fondo .modal')).includes('también quedan ocupadas: Fútbol 5 · Cancha 1 y Fútbol 5 · Cancha 2'));
  await b.page.fill('#cli', 'Quiere Fútbol 7');
  await b.page.click('.fondo .acc .pri');
  await toast(b, /Turno reservado/);
  await a.page.click('.fondo .acc .pri');                                 // la pantalla de Ana todavía veía libre ese horario
  await a.page.waitForFunction(() => /Fútbol 7/.test(document.querySelector('#m-err')?.textContent || ''), null, { timeout: 15000 });
  assert.ok(!(await turnosDe(f)).some(t => t.cliente === 'Quiere Fútbol 5'), 'no se coló el Fútbol 5');
  await a.page.click('.fondo .acc button:not(.pri)');
  await a.page.waitForFunction(() => [...document.querySelectorAll('#cal .slot.blk')].filter(e => /ocupada por Fútbol 7/.test(e.textContent)).length >= 2, null, { timeout: 10000 });      // y su pantalla se actualizó sola: las dos de Fútbol 5 figuran ocupadas por Fútbol 7
  // al revés: con un Fútbol 5 reservado, Fútbol 7 no se puede
  const r = await api('POST', '/api/turnos', { accion: 'crear', cancha: 'f7', fecha: f, ini: H(20), cliente: 'No entra', quien: 'Beto' });
  assert.strictEqual(r.status, 409);
  sinErrores(a); sinErrores(b);
}

// =====================================================================================================
async function buscar() {
  await reservar('f5a', F(3), H(18), 'Hortensia Búsqueda', '11 3333-4444');
  await reservar('f5b', F(4), H(19), 'Ulises Búsqueda', '011 15 5555-9999');
  await reservar('padelT', F(-10), H(10), 'Hortensio Antiguo', '');
  await reservar('padelA', F(2), H(16), 'Fijo Búsqueda', '11 6000-1111', 'Ana', { fijo: true });
  const c = await abrir({});
  await esperarGrilla(c);
  await c.page.click('.tabs [data-v=buscar]');
  assert.strictEqual(await c.page.evaluate(() => document.activeElement.id), 'q', 'al abrir Buscar ya se puede escribir');
  assert.ok((await texto(c, '#res')).includes('Escribí un nombre o un teléfono'));
  await c.page.keyboard.type('h');
  await c.page.waitForTimeout(500);
  assert.ok((await texto(c, '#res')).includes('al menos 2'), 'con una letra no busca');
  await c.page.keyboard.type('ORTEN');                                    // «hORTEN»: mayúsculas mezcladas, sin acentos, pocas letras
  await c.page.waitForSelector('#res .fila[data-t]');
  const nombres = await c.page.$$eval('#res .fila[data-t] .f2 b', es => es.map(e => e.textContent));
  assert.deepStrictEqual(nombres, ['Hortensio Antiguo', 'Hortensia Búsqueda'], 'ordenados por fecha');
  const fechas = await c.page.$$eval('#res .fila[data-t]', es => es.map(e => e.dataset.f));
  assert.deepStrictEqual(fechas, [F(-10), F(3)]);
  assert.ok(await c.page.locator('#res .fila.pas').count() === 1 && (await texto(c, '#res .sep')).includes('de hoy en adelante'), 'lo pasado se ve apagado y hay una raya «de hoy en adelante»');
  const f2 = await c.page.locator('#res .fila[data-t]').nth(1).innerText();
  assert.ok(f2.includes('18:00 a 19:00') && f2.includes('Fútbol 5 · Cancha 1') && f2.includes('11 3333-4444'), f2);
  await foto(c, 'm_buscar');
  // tocar el resultado abre el turno en su día
  await c.page.locator('#res .fila[data-t]').nth(1).click();
  await esperarModal(c);
  assert.strictEqual(await c.page.inputValue('#fecha'), F(3));
  assert.strictEqual(await c.page.locator('.tabs [aria-selected=true]').getAttribute('data-v'), 'dia');
  const det = await texto(c, '.fondo .modal');
  assert.ok(det.includes('Hortensia Búsqueda') && det.includes('Fútbol 5 · Cancha 1') && det.includes('18:00 a 19:00'), det);
  await cerrarModal(c);
  // por teléfono, en cualquier formato
  await c.page.click('.tabs [data-v=buscar]');
  await c.page.fill('#q', '1155559999');
  await c.page.waitForFunction(() => document.querySelector('#res .fila[data-t] .f2 b')?.textContent === 'Ulises Búsqueda');
  assert.deepStrictEqual(await c.page.$$eval('#res .fila[data-t] .f2 b', es => es.map(e => e.textContent)), ['Ulises Búsqueda']);
  await c.page.fill('#q', '+54 9 11 3333 4444');
  await c.page.waitForFunction(() => document.querySelector('#res .fila[data-t] .f2 b')?.textContent === 'Hortensia Búsqueda');
  // sin resultados
  await c.page.fill('#q', 'zzzzqq');
  await c.page.waitForFunction(() => /No encontramos turnos con/.test(document.querySelector('#res')?.textContent || ''));
  // los fijos aparecen aparte y llevan a su próximo día
  await c.page.fill('#q', 'fijo busq');
  await c.page.waitForSelector('#res .fila[data-fijo]');
  assert.ok((await texto(c, '#res h3')).includes('Turnos fijos (1)'));
  const fj = await texto(c, '#res .fila[data-fijo]');
  assert.ok(fj.includes('Fijo Búsqueda') && fj.includes('Pádel · Abierta') && fj.includes('todos los') && fj.includes('FIJO') && fj.includes('11 6000-1111'), fj);
  assert.strictEqual(await c.page.locator('#res .fila[data-fijo]').getAttribute('data-f'), F(2));
  await c.page.locator('#res .fila[data-fijo]').click();
  await esperarModal(c);
  assert.strictEqual(await c.page.inputValue('#fecha'), F(2));
  assert.ok((await texto(c, '.fondo h2')).includes('FIJO'));
  await cerrarModal(c);
  // sin conexión, la búsqueda lo dice claro y permite probar de nuevo
  await c.page.click('.tabs [data-v=buscar]');
  await c.ctx.setOffline(true);
  await c.page.fill('#q', 'hortensia');
  await c.page.waitForSelector('#res #rebus');
  assert.ok((await texto(c, '#res')).includes('No se pudo buscar'));
  await c.ctx.setOffline(false);                                          // al volver internet la búsqueda se rehace sola
  await c.page.waitForSelector('#res .fila[data-t]');
  assert.strictEqual(await c.page.locator('#res #rebus').count(), 0);
  sinErrores(c);
}

// =====================================================================================================
async function recordatorios() {
  const man = F(1);
  await reservar('f5a', man, H(15), 'Ñandú Rec', '11 3333-4444');
  await reservar('f5b', man, H(19), 'Sin Teléfono Uno', '');
  await reservar('padelT', man, H(10), 'Doña Rosa Rec', '+54 9 11 6000-1111');
  await reservar('padelA', man, H(11, 30), 'Sin Teléfono Dos', 'consultar');
  await reservar('f5a', man, H(21), 'Marcos Rec', '011 15 4444-5555');
  await reservar('padelT', man, H(13), 'Fijo Rec', '351 155 123456', 'Ana', { fijo: true });          // la ocurrencia de mañana de un fijo cuenta
  await reservar('padelA', man, H(14, 30), 'Cancelado Rec', '11 1');
  await api('POST', '/api/turnos', { accion: 'cancelar', id: (await turnosDe(man)).find(t => t.cliente === 'Cancelado Rec').id, quien: 'Ana' });
  const c = await abrir({ reloj: hora(15, 20) });
  await esperarGrilla(c);
  await c.page.waitForFunction(() => !document.querySelector('#badge').hidden, null, { timeout: 10000 });
  assert.strictEqual((await texto(c, '#badge')).trim(), '4', 'la pestaña avisa cuántos recordatorios faltan');
  await c.page.evaluate(() => { try { localStorage.setItem('enviados:2020-01-01', '["x"]'); } catch (e) {} });
  await c.page.click('.tabs [data-v=recordatorios]');
  await c.page.waitForSelector('#rec .rcard');
  assert.deepStrictEqual(await c.page.$$eval('#rec .rcard .rhora', es => es.map(e => e.textContent)), ['10:00', '13:00', '15:00', '21:00'], 'ordenados por hora');
  assert.deepStrictEqual(await c.page.$$eval('#rec .rcard .rdat b', es => es.map(e => e.textContent)), ['Doña Rosa Rec', 'Fijo Rec · FIJO', 'Ñandú Rec', 'Marcos Rec']);
  const resumen = await texto(c, '#rec .resumen');
  assert.ok(resumen.includes('6 turnos') && resumen.includes('4 con teléfono') && resumen.includes('enviados 0 de 4'), resumen);
  assert.ok((await texto(c, '#sin-tel')).includes('2 turnos de mañana no tienen teléfono'), await texto(c, '#sin-tel'));
  assert.deepStrictEqual(await c.page.$$eval('#rec .sintel .fila .f2 b', es => es.map(e => e.textContent)), ['Sin Teléfono Dos', 'Sin Teléfono Uno']);
  // el botón de WhatsApp trae el mensaje ya escrito
  const wa = await c.page.$$eval('#rec .rcard a[data-wa]', es => es.map(e => e.getAttribute('href')));
  const largo = await c.page.evaluate(f => new Date(f + 'T12:00:00').toLocaleDateString('es-AR', { weekday: 'long', day: 'numeric', month: 'long' }), man);
  const mensaje = (nombre, cancha, hh) => `Hola ${nombre}! Te recordamos tu turno de ${cancha} el ${largo} a las ${hh}. ¡Te esperamos!`;
  const esperado = [
    ['5491160001111', mensaje('Doña Rosa Rec', 'Pádel · Techada', '10:00')],
    ['5493515123456', mensaje('Fijo Rec', 'Pádel · Techada', '13:00')],
    ['5491133334444', mensaje('Ñandú Rec', 'Fútbol 5 · Cancha 1', '15:00')],
    ['5491144445555', mensaje('Marcos Rec', 'Fútbol 5 · Cancha 1', '21:00')],
  ];
  esperado.forEach(([num, msj], i) => assert.strictEqual(wa[i], `https://wa.me/${num}?text=${encodeURIComponent(msj)}`, `WhatsApp de la fila ${i}`));
  assert.ok(await c.page.locator('#rec .rcard a[data-wa]').first().getAttribute('target') === '_blank');
  await foto(c, 'm_recordatorios');
  assert.ok((await altoDe(c, '#rec .racc .btn, #rec .racc button')) >= 48 - 1, 'botones grandes');
  // el mismo texto que arma el detalle del turno
  await c.page.click('.tabs [data-v=dia]');
  await dia(c, man);
  await c.page.locator('#cal .slot.res', { hasText: 'Ñandú Rec' }).click();
  await esperarModal(c);
  assert.strictEqual(await c.page.locator('.fondo a.wa').getAttribute('href'), wa[2], 'recordatorio y detalle usan el mismo mensaje');
  await cerrarModal(c);
  await c.page.click('.tabs [data-v=recordatorios]');
  await c.page.waitForSelector('#rec .rcard');
  // marcar enviado: se recuerda por día
  await c.page.locator('#rec .rcard').first().locator('[data-env]').click();
  await c.page.waitForFunction(() => document.querySelector('#rec .rcard.listo'));
  assert.ok((await texto(c, '#rec .rcard.listo [data-env]')).includes('Enviado'));
  assert.ok((await texto(c, '#rec .resumen')).includes('enviados 1 de 4'));
  assert.strictEqual((await texto(c, '#badge')).trim(), '3');
  const claves = await c.page.evaluate(() => Object.keys(localStorage).filter(k => k.startsWith('enviados:')));
  assert.deepStrictEqual(claves, ['enviados:' + man], 'se guarda por día y se limpian los días viejos');
  await c.page.reload();
  await esperarGrilla(c);
  await c.page.click('.tabs [data-v=recordatorios]');
  await c.page.waitForSelector('#rec .rcard.listo');
  assert.strictEqual(await c.page.locator('#rec .rcard.listo').count(), 1, 'sigue marcado después de recargar');
  assert.strictEqual((await texto(c, '#badge')).trim(), '3');
  await c.page.locator('#rec .rcard.listo [data-env]').click();                                    // se puede desmarcar
  await c.page.waitForFunction(() => !document.querySelector('#rec .rcard.listo'));
  assert.strictEqual((await texto(c, '#badge')).trim(), '4');
  // un turno sin teléfono se toca y se abre para cargarle el teléfono
  await c.page.locator('#rec .sintel .fila').first().click();
  await esperarModal(c);
  assert.ok((await texto(c, '.fondo .modal')).includes('Sin Teléfono'));
  await c.page.locator('.fondo .acc .pri').click();                                                // Editar datos
  await c.page.waitForSelector('#tel');
  await c.page.fill('#tel', '11 2345-6789');
  await c.page.locator('.fondo .acc .pri').click();
  await toast(c, /Guardado/);
  await c.page.waitForFunction(() => document.querySelectorAll('#rec .rcard').length === 5);
  assert.ok((await texto(c, '#rec .resumen')).includes('5 con teléfono'));
  assert.ok((await texto(c, '#sin-tel')).includes('1 turno de mañana no tiene teléfono'));
  sinErrores(c);
}

// =====================================================================================================
async function refresco_errores() {
  const f = F(33);
  // ---- se actualiza sola al volver a la pestaña
  const c = await abrir({});
  await esperarGrilla(c); await dia(c, f);
  await reservar('f5b', f, H(18), 'Llegó Solo');
  await c.page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  await c.page.locator('#cal .slot.res', { hasText: 'Llegó Solo' }).waitFor({ timeout: 10000 });
  // ---- sin internet: lo dice y no borra lo que se veía
  await c.ctx.setOffline(true);
  await reservar('f5b', f, H(19), 'Llegó Después');
  await c.page.waitForTimeout(900);
  await c.page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await c.page.waitForFunction(() => /desactualizado/.test(document.querySelector('#aviso')?.textContent || ''), null, { timeout: 10000 });
  assert.ok(/desactualizado/.test(await texto(c, '#aviso')), await texto(c, '#aviso'));
  assert.strictEqual(await c.page.locator('#cal .slot.res', { hasText: 'Llegó Solo' }).count(), 1, 'lo que se veía sigue ahí');
  await foto(c, 'm_sin_internet');
  await c.ctx.setOffline(false);
  await c.page.waitForTimeout(900);
  await c.page.evaluate(() => window.dispatchEvent(new Event('online')));
  await c.page.locator('#cal .slot.res', { hasText: 'Llegó Después' }).waitFor({ timeout: 10000 });
  await c.page.waitForSelector('#aviso', { state: 'hidden' });
  // ---- guardar con un error del servidor o sin red: mensaje claro en la misma ventana
  await c.page.click('.slot.free[data-c=padelT][data-s="600"]');
  await esperarModal(c);
  await c.page.fill('#cli', 'No se guarda');
  await c.page.route('**/api/turnos', r => r.request().method() === 'POST' ? r.fulfill({ status: 500, contentType: 'application/json', body: '{"error":"Error del servidor"}' }) : r.continue());
  await c.page.click('.fondo .acc .pri');
  await c.page.waitForFunction(() => /Error del servidor/.test(document.querySelector('#m-err')?.textContent || ''));
  assert.strictEqual(await c.page.locator('.fondo .acc .pri').isDisabled(), false, 'se puede volver a intentar');
  await c.page.unroute('**/api/turnos');
  await c.page.route('**/api/turnos', r => r.request().method() === 'POST' ? r.abort() : r.continue());
  await c.page.click('.fondo .acc .pri');
  await c.page.waitForFunction(() => /No se pudo conectar con el servidor/.test(document.querySelector('#m-err')?.textContent || ''));
  await c.page.unroute('**/api/turnos');
  await c.page.click('.fondo .acc .pri');                                // ahora sí
  await toast(c, /Turno reservado/);
  assert.ok((await turnosDe(f)).some(t => t.cliente === 'No se guarda'));
  sinErrores(c);

  // ---- si no se puede cargar la pantalla, se explica y se puede reintentar
  const d = await abrir({ antes: async p => { await p.route('**/api/turnos?fecha=*', r => r.abort()); } });
  await d.page.waitForSelector('#cal .reint', { timeout: 15000 });
  assert.ok((await texto(d, '#cal')).includes('No se pudieron cargar los turnos'));
  assert.ok((await altoDe(d, '#cal .reint')) >= 44);
  await foto(d, 'm_error_carga');
  await d.page.unroute('**/api/turnos?fecha=*');
  await d.page.click('#cal .reint');
  await esperarGrilla(d);
  // mientras carga se ve la barra de progreso y la lista anterior apagada
  await d.page.route('**/api/turnos?fecha=*', async r => { await new Promise(x => setTimeout(x, 1500)); await r.continue(); });
  await d.page.click('#sig');
  await d.page.waitForSelector('#carga:not([hidden])');
  assert.ok(await d.page.locator('#cal.espera').count() === 1, 'mientras llega el día nuevo, el anterior se ve apagado');
  await d.page.waitForSelector('#carga', { state: 'hidden', timeout: 10000 });
  assert.strictEqual(await d.page.locator('#cal.espera').count(), 0);
}

// =====================================================================================================
async function registro_temas() {
  await reservar('f5a', F(34), H(15), 'Para el Registro', '', 'Ana');
  for (const [ancho, alto, tema] of [[390, 844, 'light'], [390, 844, 'dark'], [320, 640, 'dark'], [1280, 800, 'light'], [1280, 800, 'dark']]) {
    const nombre = `${ancho < 700 ? (ancho < 360 ? 'xs' : 'm') : 'pc'}_${tema === 'dark' ? 'osc' : 'claro'}`;
    const c = await abrir({ ancho, alto, tema });
    await esperarGrilla(c);
    const fondo = await c.page.evaluate(() => getComputedStyle(document.body).backgroundColor);
    assert.strictEqual(fondo, tema === 'dark' ? 'rgb(15, 23, 20)' : 'rgb(244, 246, 245)', 'tema ' + tema);
    // todas las pantallas, sin desbordes
    for (const v of ['semana', 'buscar', 'recordatorios', 'dia']) {
      await c.page.click(`.tabs [data-v=${v}]`);
      await c.page.waitForSelector(`#v-${v}:not([hidden])`);
      if (v === 'buscar') { await c.page.fill('#q', 'registro'); await c.page.waitForSelector('#res .fila[data-t]'); }
      if (v === 'recordatorios') await c.page.waitForSelector('#rec .resumen');
      if (v === 'semana') await c.page.waitForSelector('#sem .dcard');
      await sinDesborde(c, `${nombre} ${v}`);
      await foto(c, `${nombre}_${v}`);
    }
    if (ancho < 700) {      // tamaños para tocar con el dedo
      for (const sel of ['.tabs button', '#ant', '#sig', '#hoy', '#fecha', '#btnreg', '#tema', '.salir', '#quien-cambiar']) {
        assert.ok((await altoDe(c, sel)) >= 43.5, `${sel} muy chico para el dedo: ${await altoDe(c, sel)}`);
      }
      assert.ok((await c.page.locator('.tabs').boundingBox()).y + (await c.page.locator('.tabs').boundingBox()).height >= alto - 1, 'las pestañas están abajo');
      await c.page.waitForSelector('#cal .slot.free');
      assert.ok((await altoDe(c, '#cal .slot.free')) >= 44, 'huecos libres cómodos para tocar');
    }
    // registro de cambios
    await c.page.click('#btnreg');
    await c.page.waitForSelector('#v-registro:not([hidden]) table tr');
    assert.ok((await texto(c, '#reg')).includes('Para el Registro'), 'el registro muestra lo hecho');
    assert.strictEqual((await texto(c, '#btnreg')).trim(), 'Volver');
    await sinDesborde(c, nombre + ' registro'); await foto(c, nombre + '_registro');
    await c.page.click('#btnreg');
    await c.page.waitForSelector('#v-dia:not([hidden])');
    // el botón de claro/oscuro cambia y se acuerda
    const antes = await c.page.evaluate(() => getComputedStyle(document.body).backgroundColor);
    await c.page.click('#tema');
    assert.notStrictEqual(await c.page.evaluate(() => getComputedStyle(document.body).backgroundColor), antes);
    await c.page.reload(); await esperarGrilla(c);
    assert.notStrictEqual(await c.page.evaluate(() => getComputedStyle(document.body).backgroundColor), antes, 'la elección del tema se recuerda');
    sinErrores(c);
    await c.ctx.close();
  }
}

// =====================================================================================================
const ESCENARIOS = { dia_semana, reservar: reservar_escenario, concurrencia, buscar, recordatorios, refresco_errores, registro_temas };
(async () => {
  await entrar();
  assert.strictEqual((await api('POST', '/api/sync', { canchas: CANCHAS }, true)).status, 200);
  navegador = await chromium.launch({ args: ['--no-sandbox'] });
  const lista = ESCENARIO === 'todos' ? Object.keys(ESCENARIOS) : ESCENARIO.split(',');
  try {
    for (const n of lista) {
      const t0 = Date.now();
      await ESCENARIOS[n]();
      console.log(`ok ${n} (${((Date.now() - t0) / 1000).toFixed(1)} s)`);
    }
  } catch (e) {
    console.error('FALLÓ:', e && e.stack || e);
    for (const c of abiertos) { try { await c.page.screenshot({ path: `/tmp/turnos_web_fallo_${abiertos.indexOf(c)}.png` }); } catch (x) {} }
    process.exitCode = 1;
  } finally {
    await navegador.close();
  }
})();
