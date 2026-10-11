// Prueba de punta a punta: cargar stock leyendo la foto de un remito (con un Gemini falso).
// foto -> "Leyendo el remito…" -> tabla -> corregir renglones -> cargar -> el stock cambió y quedó en el historial.
const { chromium } = require(process.env.PLAYWRIGHT_PATH || '/opt/node22/lib/node_modules/playwright');
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const URL = `http://127.0.0.1:${process.env.PORT}/`;
const CONFIG = path.join(process.env.DATOS, 'configuracion.json');
const GEMINI = process.env.GEMINI_FALSO;
const FOTOS = process.env.FOTOS || '';

(async () => {
  const b = await chromium.launch({ args: ['--no-sandbox'] });
  const ctx = await b.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const errs = [];
  page.on('pageerror', e => errs.push('PAGEERROR ' + e.message));
  page.on('console', m => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errs.push('CONSOLE ' + m.text()); });
  const ev = (f, a) => page.evaluate(f, a);
  const foto = n => FOTOS ? page.screenshot({ path: `${FOTOS}/remito-${n}.png` }) : null;
  const cargar = async () => { await page.goto(URL); await page.waitForFunction(() => window.__pos && window.__pos.DB); };
  const stock = id => ev(id => __pos.DB.productos.find(p => p.id === id).stock, id);
  const titulo = () => page.locator('.modal header h3').innerText();
  const forzar = (estado, cuerpo, demora = 0) => fetch(GEMINI + '/__forzar', { method: 'POST', body: JSON.stringify({ estado, cuerpo, demora }) });
  const errorGoogle = (codigo, estado, mensaje, detalles) => ({ error: { code: codigo, status: estado, message: mensaje, ...(detalles ? { details: detalles } : {}) } });
  const pill = i => page.locator('#rm-s-' + i).innerText();
  const unidades = async i => +(await page.locator('#rm-u-' + i).innerText());
  const tildado = i => page.locator('#rm-ck-' + i).isChecked();
  const producto = i => page.locator('#rm-p-' + i + ' span').innerText();

  await cargar();
  // una foto grande de verdad (3200 x 2400) para comprobar que se reduce antes de mandarla
  const b64 = await ev(async () => {
    const c = document.createElement('canvas'); c.width = 3200; c.height = 2400;
    const x = c.getContext('2d'); const g = x.createLinearGradient(0, 0, 3200, 2400); g.addColorStop(0, '#fafafa'); g.addColorStop(1, '#cfd8dc');
    x.fillStyle = g; x.fillRect(0, 0, 3200, 2400); x.fillStyle = '#111'; x.font = 'bold 90px sans-serif';
    for (let i = 0; i < 20; i++) x.fillText('COCA COLA 500CC BULTO 6X12      ' + (i + 1), 120, 160 + i * 105);
    const blob = await new Promise(r => c.toBlob(r, 'image/png'));
    const buf = new Uint8Array(await blob.arrayBuffer()); let s = ''; for (let i = 0; i < buf.length; i += 0x8000) s += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
    return btoa(s);
  });
  const FOTO = Buffer.from(b64, 'base64');
  const elegirFoto = () => page.locator('#foto-file').setInputFiles({ name: 'remito.png', mimeType: 'image/png', buffer: FOTO });
  const abrir = async () => { await page.locator('[data-act=nav][data-v=stock]').click(); await page.locator('[data-act=foto]').click(); };

  // ---- 0. sin clave: el mensaje explica el paso a paso y se vuelve a revisar sin reiniciar
  await abrir();
  await page.locator('#modal-root').getByText('Falta la clave de Google').first().waitFor();
  const pasos = await page.locator('.mbody').innerText();
  assert.ok(/aistudio\.google\.com\/apikey/.test(pasos) && /Crear clave de API/.test(pasos) && /Bloc de notas/.test(pasos) && /gemini_clave/.test(pasos), pasos);
  assert.ok(pasos.includes(CONFIG), 'dice dónde está el archivo: ' + pasos);
  assert.ok(/se manda a Google/.test(pasos), 'avisa que la foto viaja a Google');
  await foto('0-sin-clave');
  await page.locator('.modal footer button', { hasText: 'Ya la pegué' }).click();
  await page.locator('.toast', { hasText: 'Todavía no está la clave' }).waitFor();
  assert.strictEqual(await titulo(), 'Falta la clave de Google');
  fs.writeFileSync(CONFIG, JSON.stringify({ gemini_clave: process.env.CLAVE }, null, 2));            // la persona pega la clave y guarda
  await page.locator('.modal footer button', { hasText: 'Ya la pegué' }).click();
  await page.locator('#foto-file').waitFor();
  assert.strictEqual(await titulo(), 'Cargar stock con foto del remito');
  assert.strictEqual(await page.locator('#mact-1').isDisabled(), true, '"Leer remito" espera a que haya una foto');

  // ---- 1. elegir la foto y leerla: "Leyendo el remito…" sin congelar la pantalla
  const antes = { p1: await stock('p1'), p3: await stock('p3'), p5: await stock('p5'), p8: await stock('p8'), p9: await stock('p9'), p10: await stock('p10'), p11: await stock('p11') };
  await elegirFoto();
  await page.locator('#foto-prev img').waitFor();
  assert.strictEqual(await page.locator('#mact-1').isDisabled(), false);
  await ev(() => { window.__t = 0; window.__iv = setInterval(() => { window.__t++; }, 25); });
  await page.locator('#mact-1').click();
  await page.locator('#rm-leyendo').waitFor();
  assert.ok(/Leyendo el remito…/.test(await page.locator('.modal').innerText()));
  await foto('1-leyendo');
  await page.waitForTimeout(700);
  assert.ok(await ev(() => window.__t) >= 12, 'la pantalla no se congela mientras lee (hilos de la página siguen andando)');
  await page.locator('#rm-tabla').waitFor({ timeout: 15000 });
  await ev(() => clearInterval(window.__iv));
  await foto('2-tabla');

  // ---- 2. la tabla: lo que dice el remito, el producto elegido, cantidad x presentación = unidades y el estado de cada renglón
  assert.strictEqual(await page.locator('.rm-row').count(), 7);
  assert.strictEqual(await titulo(), 'Revisá lo que leyó antes de cargar');
  assert.strictEqual(await page.locator('#rm-dup').count(), 0, 'es la primera vez: no hay aviso de repetido');
  assert.deepStrictEqual([await pill(0), await producto(0), await unidades(0), await tildado(0)], ['seguro', 'Coca-Cola 500 ml', 144, true]);
  assert.strictEqual(await page.locator('#rm-c-0').inputValue(), '2');
  assert.strictEqual(await page.locator('#rm-m-0').inputValue(), 'bulto');
  assert.strictEqual(await page.locator('#rm-n-0').inputValue(), '72');
  assert.deepStrictEqual([await pill(1), await producto(1), await unidades(1), await tildado(1)], ['seguro', 'Pan de pancho', 24, true]);
  assert.deepStrictEqual([await pill(2), await producto(2), await tildado(2)], ['revisar', 'Agua sin gas 500 ml', false], 'AGUA 500: no dice si es con gas o sin gas, lo decide la persona');
  assert.strictEqual(await unidades(2), 12, 'pack x 6 que sabe el producto');
  assert.deepStrictEqual([await pill(3), await producto(3), await tildado(3)], ['elegí el producto', 'Elegí el producto', false]);
  assert.strictEqual(await page.locator('#rm-ck-3').isDisabled(), true, 'sin producto no se puede tildar');
  assert.deepStrictEqual([await pill(4), await producto(4), await unidades(4)], ['seguro', 'Alfajor triple', 24]);
  assert.strictEqual(await page.locator('#rm-n-4').inputValue(), '24', 'el X24 del renglón');
  assert.deepStrictEqual([await pill(5), await producto(5), await unidades(5)], ['seguro', 'Seven Up 500 ml', 6]);
  assert.deepStrictEqual([await pill(6), await producto(6), await tildado(6)], ['revisar', 'Sprite 500 ml', false], 'la IA dijo Seven Up pero el renglón dice Sprite');
  assert.strictEqual(await page.locator('#rm-n-5').isHidden(), true, 'por unidad no se pide "cuántas trae"');
  assert.ok(/Se van a cargar 4 renglones \(198 unidades\)/.test(await page.locator('#rm-resumen').innerText()) && /3 quedan sin cargar/.test(await page.locator('#rm-resumen').innerText()),
    await page.locator('#rm-resumen').innerText());
  assert.strictEqual(await stock('p1'), antes.p1, 'todavía no se cargó nada');

  // ---- 3. corregir renglones
  // 3a. un amarillo: elegir con el menú con búsqueda
  await page.locator('#rm-p-2').click();
  await page.locator('#rm-pop').waitFor();
  assert.ok(/los más parecidos/i.test(await page.locator('#rm-opts').innerText()), 'primero los parecidos');
  await page.locator('#rm-pq').fill('con gas');
  assert.strictEqual(await page.locator('#rm-opts .rm-opt').count(), 1);
  await foto('3-menu');
  await page.locator('#rm-opts .rm-opt', { hasText: 'Agua con gas 500 ml' }).click();
  assert.strictEqual(await page.locator('#rm-pop').count(), 0);
  assert.deepStrictEqual([await producto(2), await pill(2), await tildado(2), await unidades(2)], ['Agua con gas 500 ml', 'revisado', true, 12]);
  // 3b. un rojo: buscar algo que no existe; Esc cierra sólo el menú; un clic afuera también
  await page.locator('#rm-p-3').click();
  await page.locator('#rm-pq').fill('servilletas');
  assert.ok(/No hay productos con ese nombre/.test(await page.locator('#rm-opts').innerText()));
  await page.keyboard.press('Escape');
  assert.strictEqual(await page.locator('#rm-pop').count(), 0);
  assert.strictEqual(await page.locator('#rm-tabla').count(), 1, 'Esc cerró sólo el menú, no la ventana');
  await page.locator('#rm-p-3').click();
  await page.locator('.rm-t code').first().click();
  assert.strictEqual(await page.locator('#rm-pop').count(), 0);
  assert.strictEqual(await page.locator('#rm-tabla').count(), 1, 'un clic afuera cerró sólo el menú');
  // 3c. con el teclado: Enter en el buscador elige el primero
  await page.locator('#rm-p-6').click();
  await page.locator('#rm-pq').fill('sprite');
  await page.locator('#rm-pq').press('Enter');
  assert.deepStrictEqual([await producto(6), await pill(6), await tildado(6)], ['Sprite 500 ml', 'revisado', true]);
  // 3d. cantidades
  await page.locator('#rm-c-1').fill('5');
  assert.strictEqual(await unidades(1), 30, '5 paquetes x 6');
  await page.locator('#rm-c-5').fill('8');
  assert.strictEqual(await unidades(5), 8);
  await page.locator('#rm-m-5').selectOption('caja');
  assert.strictEqual(await page.locator('#rm-n-5').isVisible(), true);
  assert.strictEqual(await unidades(5), 8 * 12, 'al pasar a cajas usa lo que trae el producto (x 12)');
  assert.ok(/12/.test(await page.locator('#rm-n-5').inputValue()));
  await page.locator('#rm-m-5').selectOption('unidad');
  assert.strictEqual(await unidades(5), 8);
  await page.locator('#rm-c-0').press('Enter');                               // Enter en un número no carga el remito por accidente
  assert.strictEqual(await page.locator('#rm-tabla').count(), 1);
  assert.strictEqual(await stock('p1'), antes.p1);
  // 3e. un renglón se puede sacar con la tilde
  await page.locator('#rm-ck-4').uncheck();
  assert.ok(/Se van a cargar 5 renglones/.test(await page.locator('#rm-resumen').innerText()));
  await page.locator('#rm-ck-4').check();
  assert.ok(/Se van a cargar 6 renglones \(230 unidades\)/.test(await page.locator('#rm-resumen').innerText()), await page.locator('#rm-resumen').innerText());
  await foto('4-corregida');

  // ---- 4. cargar: el stock cambia, queda en el historial y en la base
  await page.locator('#mact-1').click();
  await page.waitForFunction(() => !document.querySelector('#modal-root .modal'));
  await page.locator('.toast', { hasText: 'Se cargaron 6 renglones al stock (230 unidades)' }).waitFor();
  const despues = { p1: await stock('p1'), p3: await stock('p3'), p5: await stock('p5'), p8: await stock('p8'), p9: await stock('p9'), p10: await stock('p10'), p11: await stock('p11') };
  assert.deepStrictEqual(despues, { p1: antes.p1 + 144, p3: antes.p3 + 24, p5: antes.p5 + 30, p8: antes.p8, p9: antes.p9 + 12, p10: antes.p10 + 12, p11: antes.p11 + 8 });
  const movs = await ev(() => __pos.DB.movimientos.filter(m => (m.ref || '').startsWith('remito:')).map(m => ({ p: m.prodId, c: m.cant, t: m.tipo, m: m.motivo, a: m.antes, d: m.despues })));
  assert.strictEqual(movs.length, 6);
  assert.ok(movs.every(m => m.t === 'ingreso' && m.m.startsWith('Remito (foto) · ')));
  assert.ok(movs.some(m => m.p === 'p1' && m.c === 144 && /COCA COLA 500CC BULTO 6X12/.test(m.m)));
  assert.ok(!movs.some(m => /SERVILLETAS/.test(m.m)), 'el renglón sin producto no se cargó');
  assert.strictEqual(new Set(await ev(() => __pos.DB.movimientos.filter(m => (m.ref || '').startsWith('remito:')).map(m => m.ref))).size, 1, 'todos con la misma huella del remito');
  await page.locator('[data-act=stk-tab][data-v=hist]').click();
  const hist = await page.locator('.tbl-wrap').innerText();
  assert.ok(/Remito \(foto\) · COCA COLA 500CC BULTO 6X12/.test(hist) && /\+144/.test(hist) && /Remito \(foto\) · SPRITE 500/.test(hist), 'en el historial de movimientos');
  await foto('5-historial');
  await page.locator('[data-act=stk-tab][data-v=inv]').click();
  assert.ok(/154/.test(await page.locator('tr', { hasText: 'Coca-Cola 500 ml' }).first().innerText()), 'el inventario muestra el stock nuevo');
  // quedó guardado en el programa, no sólo en la pantalla
  await cargar();
  assert.strictEqual(await stock('p1'), antes.p1 + 144);
  assert.strictEqual(await ev(() => __pos.DB.movimientos.filter(m => (m.ref || '').startsWith('remito:')).length), 6);
  const aud = await ev(async () => (await (await fetch('/api/auditoria?limite=500&t=' + encodeURIComponent(PREDIO.token))).json()).filas.map(f => f.resumen));
  assert.ok(aud.some(t => /COCA COLA 500CC/.test(t) || /Remito \(foto\)/.test(t)), 'también en el registro de cambios: ' + JSON.stringify(aud.slice(0, 5)));

  // ---- 5. el mismo remito (misma foto) otra vez: avisa antes de duplicar
  await abrir();
  await elegirFoto();
  await page.locator('#mact-1').click();
  await page.locator('#rm-tabla').waitFor({ timeout: 15000 });
  assert.ok(/ya se cargó hoy a las \d\d:\d\d/.test(await page.locator('#rm-dup').innerText()), 'aviso arriba de la tabla');
  await page.locator('#rm-ck-1').uncheck();                                      // se saca el pan: ese cambio tiene que sobrevivir al "volver a revisar"
  await page.locator('#mact-1').click();
  await page.locator('.modal header h3', { hasText: 'Este remito ya se cargó hoy' }).waitFor();
  await page.locator('.modal footer button', { hasText: 'Volver a revisar' }).click();
  await page.locator('#rm-tabla').waitFor();
  assert.strictEqual(await tildado(1), false, 'volvió a la tabla como estaba');
  assert.strictEqual(await stock('p1'), antes.p1 + 144, 'todavía no se duplicó nada');
  await page.locator('#mact-1').click();
  await page.locator('.modal footer button', { hasText: 'Cargar igual' }).click();
  await page.locator('.toast', { hasText: /Se cargaron 3 renglones al stock \(174 unidades\)/ }).waitFor();
  assert.strictEqual(await stock('p1'), antes.p1 + 288);
  assert.strictEqual(await stock('p5'), antes.p5 + 30, 'el pan no se volvió a cargar');
  assert.strictEqual(await stock('p11'), antes.p11 + 8 + 6);

  // ---- 6. errores: mensajes claros y siempre una salida
  await abrir(); await elegirFoto(); await forzar(429, errorGoogle(429, 'RESOURCE_EXHAUSTED', 'Quota exceeded', [{ '@type': 'type.googleapis.com/google.rpc.QuotaFailure', violations: [{ quotaId: 'GenerateRequestsPerDayPerProjectPerModel-FreeTier' }] }]));
  await page.locator('#mact-1').click();
  await page.locator('#rm-error').waitFor();
  assert.ok(/límite gratis de hoy/.test(await page.locator('#rm-error').innerText()));
  assert.strictEqual(await page.locator('.modal footer button', { hasText: 'Probar de nuevo' }).count(), 0, 'hoy ya no hay cupo: no tiene sentido reintentar');
  await foto('6-cupo-dia');
  await page.locator('.modal footer button', { hasText: 'Elegir otra foto' }).click();
  await page.locator('#foto-file').waitFor();
  await elegirFoto(); await forzar(429, errorGoogle(429, 'RESOURCE_EXHAUSTED', 'Quota exceeded per minute'));
  await page.locator('#mact-1').click();
  await page.locator('#rm-error').waitFor();
  assert.ok(/Esperá un minuto y probá de nuevo/.test(await page.locator('#rm-error').innerText()));
  await page.locator('.modal footer button', { hasText: 'Probar de nuevo' }).click();       // ahora sí responde bien
  await page.locator('#rm-tabla').waitFor({ timeout: 15000 });
  await page.locator('.modal footer button', { hasText: 'Cancelar' }).click();
  assert.strictEqual(await page.locator('#modal-root .modal').count(), 0);
  await abrir(); await elegirFoto(); await forzar(400, errorGoogle(400, 'INVALID_ARGUMENT', 'API key not valid. Please pass a valid API key.'));
  await page.locator('#mact-1').click();
  await page.locator('#rm-error').waitFor();
  assert.ok(/Google no aceptó la clave/.test(await page.locator('#rm-error').innerText()) && !(await page.locator('.modal').innerText()).includes(process.env.CLAVE));
  assert.strictEqual(await page.locator('.modal footer button', { hasText: 'Abrir la carpeta' }).count(), 1);
  await page.locator('.modal footer button', { hasText: 'Cerrar' }).click();
  await abrir(); await elegirFoto(); await forzar(200, '{ esto no es json');
  await page.locator('#mact-1').click();
  await page.locator('#rm-error').waitFor();
  assert.ok(/no se pudo leer/.test(await page.locator('#rm-error').innerText()));
  await page.locator('.modal footer button', { hasText: 'Cerrar' }).click();
  await abrir(); await elegirFoto(); await forzar(200, { candidates: [{ content: { parts: [{ text: JSON.stringify({ items: [] }) }] } }] });
  await page.locator('#mact-1').click();
  await page.locator('.modal header h3', { hasText: 'No se encontraron renglones' }).waitFor();
  await page.locator('.modal footer button', { hasText: 'Cerrar' }).click();

  // ---- 7. cancelar mientras lee: no aparece nada después
  await abrir(); await elegirFoto(); await forzar(200, { candidates: [{ content: { parts: [{ text: JSON.stringify({ items: [{ texto: 'GRIP', cantidad: 1, presentacion: 'unidad', unidades_por_presentacion: null, producto_id: null, confianza: 0, dudoso: false }] }) }] } }] }, 1.5);
  await page.locator('#mact-1').click();
  await page.locator('#rm-leyendo').waitFor();
  await page.waitForTimeout(600);                                                  // ya salió el pedido y el programa está esperando a Google
  await page.locator('.modal footer button', { hasText: 'Cancelar' }).click();
  assert.strictEqual(await page.locator('#modal-root .modal').count(), 0);
  await page.waitForTimeout(2200);
  assert.strictEqual(await page.locator('#modal-root .modal').count(), 0, 'la respuesta tardía no reabre nada');

  // ---- 8. la clave se vuelve a leer cada vez: si se borra o el archivo se rompe, avisa distinto
  fs.writeFileSync(CONFIG, JSON.stringify({ gemini_clave: '' }));
  await abrir();
  await page.locator('#modal-root').getByText('Falta la clave de Google').first().waitFor();
  await page.locator('.modal footer button', { hasText: 'Cerrar' }).click();
  fs.writeFileSync(CONFIG, '{ "gemini_clave": "AIza... falta cerrar la comilla');
  await abrir();
  await page.locator('#modal-root').getByText('Falta la clave de Google').first().waitFor();
  assert.ok(/error de escritura/.test(await page.locator('.mbody').innerText()), 'archivo roto: no dice que falta la clave sino que hay un error');
  await foto('7-archivo-roto');
  await page.locator('.modal footer button', { hasText: 'Cerrar' }).click();

  // ---- 9. pantalla angosta (notebook chica): la tabla se apila y se puede usar
  fs.writeFileSync(CONFIG, JSON.stringify({ gemini_clave: process.env.CLAVE }));
  await page.setViewportSize({ width: 900, height: 700 });
  await abrir(); await elegirFoto();
  await page.locator('#mact-1').click();
  await page.locator('#rm-tabla').waitFor({ timeout: 15000 });
  assert.strictEqual(await page.locator('.rm-head').isHidden(), true);
  await foto('8-angosta');
  const ancho = await ev(() => { const m = document.querySelector('#modal-root .modal'); return [m.scrollWidth, m.clientWidth]; });
  assert.ok(ancho[0] <= ancho[1] + 1, 'sin scroll horizontal: ' + ancho);
  await page.keyboard.press('Escape');
  assert.strictEqual(await page.locator('#modal-root .modal').count(), 0);

  assert.deepStrictEqual(errs, [], 'errores en la consola: ' + errs.join('\n'));
  await b.close();
  console.log('E2E remito OK');
})().catch(e => { console.error(e); process.exit(1); });
