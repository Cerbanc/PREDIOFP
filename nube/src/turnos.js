// Turnos de cancha. La nube es la única fuente de verdad: todo choque se resuelve acá, en un solo lugar.
// Regla fija: Fútbol 7 usa las dos canchas de Fútbol 5 → si hay un F5 no hay F7, y si hay F7 no hay F5. Las de pádel son independientes.
const F5 = ["f5a", "f5b"];
const relacionadas = (a, b) => a.id === b.id || (a.clave === "f7" && F5.includes(b.clave)) || (b.clave === "f7" && F5.includes(a.clave));
const FECHA = /^\d{4}-\d{2}-\d{2}$/;
const hm = (m) => String(Math.floor(m / 60) % 24).padStart(2, "0") + ":" + String(m % 60).padStart(2, "0");
const dow = (f) => new Date(f + "T12:00:00Z").getUTCDay();
const sumarDias = (f, n) => new Date(new Date(f + "T12:00:00Z").getTime() + n * 864e5).toISOString().slice(0, 10);
const DIAS = ["domingo", "lunes", "martes", "miércoles", "jueves", "viernes", "sábado"];
const uid = (p) => p + Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
const txt = (v, n) => String(v ?? "").trim().slice(0, n);

const BLOQ = "Este turno ya está en una cuenta o con pagos en la caja del local: se cambia desde la PC del predio.";
class Err extends Error { constructor(m, s = 400) { super(m); this.status = s; } }
export const ocupacionDia = (env, fecha, cs) => ocupacion(env, fecha, cs);
export { canchas as listarCanchas, ocupante, relacionadas };
const json = (o, s = 200) => new Response(JSON.stringify(o), { status: s, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" } });

// Columnas nuevas de la versión 2 de turnos (quién cargó / cambió): están en nube/migraciones.sql y en schema.sql.
// Si la base de producción todavía no las tiene, se agregan solas la primera vez que alguien usa los turnos desde la web,
// así da lo mismo si primero se publica el Worker o primero se corre la migración. No toca nada de lo que ya hay.
const COLUMNAS_NUEVAS = [
  ["turnos", "creado_por", "TEXT NOT NULL DEFAULT ''"], ["turnos", "modificado_por", "TEXT NOT NULL DEFAULT ''"], ["turnos", "modificado_en", "INTEGER NOT NULL DEFAULT 0"],
  ["fijos", "creado", "INTEGER NOT NULL DEFAULT 0"], ["fijos", "creado_por", "TEXT NOT NULL DEFAULT ''"], ["fijos", "modificado_por", "TEXT NOT NULL DEFAULT ''"], ["fijos", "modificado_en", "INTEGER NOT NULL DEFAULT 0"],
];
let columnasListas = null;
function asegurarColumnas(env) {
  return (columnasListas ??= (async () => {
    for (const tabla of ["turnos", "fijos"]) {
      const tiene = new Set((await env.DB.prepare(`PRAGMA table_info(${tabla})`).all()).results.map((r) => r.name));
      for (const [, col, def] of COLUMNAS_NUEVAS.filter((x) => x[0] === tabla)) {
        if (tiene.has(col)) continue;
        try { await env.DB.prepare(`ALTER TABLE ${tabla} ADD COLUMN ${col} ${def}`).run(); } catch (e) { if (!/duplicate column/i.test(String(e && e.message))) throw e; }
      }
    }
  })().catch((e) => { columnasListas = null; throw e; }));
}

async function canchas(env) { return (await env.DB.prepare("SELECT * FROM canchas ORDER BY orden").all()).results; }

// turnos reales + ocurrencias de turnos fijos (virtuales) de un día
function ocupacion(env, fecha, cs) { return ocupacionRango(env, fecha, 1, cs); }

// lo mismo para varios días seguidos (n días desde `desde`): una sola consulta de turnos y una de fijos
async function ocupacionRango(env, desde, n, cs) {
  const hasta = sumarDias(desde, n - 1);
  const reales = (await env.DB.prepare("SELECT * FROM turnos WHERE fecha >= ? AND fecha <= ?").bind(desde, hasta).all()).results;
  const fijos = (await env.DB.prepare("SELECT * FROM fijos WHERE desde <= ? AND (hasta IS NULL OR hasta >= ?)").bind(hasta, desde).all()).results;
  const salida = reales.filter((t) => t.estado !== "cancelado");
  const tomados = new Set(reales.filter((t) => t.fijo_id && t.ocurrencia).map((t) => t.fijo_id + "|" + t.ocurrencia));      // un fijo ya suspendido o movido ese día no cuenta
  for (let i = 0; i < n; i++) {
    const fecha = sumarDias(desde, i), d = dow(fecha);
    for (const f of fijos) {
      const c = cs.find((x) => x.id === f.cancha_id);
      if (!c || f.dow !== d || f.desde > fecha || (f.hasta && f.hasta < fecha) || tomados.has(f.id + "|" + fecha)) continue;
      salida.push({ virtual: true, id: `F:${f.id}:${fecha}`, cancha_id: f.cancha_id, fecha, ini: f.ini, fin: f.ini + c.dur, cliente: f.cliente, tel: f.tel, nota: f.nota, precio: c.precio_fijo, sena: 0, estado: "reservado", tipo_precio: "fijo", fijo_id: f.id, ocurrencia: fecha,
        creado: f.creado, creado_por: f.creado_por, modificado: f.modificado, modificado_por: f.modificado_por, modificado_en: f.modificado_en });
    }
  }
  return salida;
}
function ocupante(cs, lista, canchaId, ini, fin, ignorar) {
  const c = cs.find((x) => x.id === canchaId);
  return lista.find((t) => t.ini < fin && ini < t.fin && relacionadas(c, cs.find((x) => x.id === t.cancha_id)) && !(ignorar && t.id === ignorar));
}
async function anotar(env, quien, accion, detalle) {
  await env.DB.prepare("INSERT INTO registro_turnos (ts, quien, accion, detalle) VALUES (?, ?, ?, ?)").bind(Date.now(), quien, accion, detalle).run();
}
function validarHorario(c, ini) {
  if (!Number.isInteger(ini) || ini < c.ap || ini + c.dur > c.ci || (ini - c.ap) % c.dur !== 0) throw new Err("Ese horario no existe en " + c.nombre);
}
const desc = (t, c) => `${c.nombre}, ${t.fecha} ${hm(t.ini)}${t.cliente ? " (" + t.cliente + ")" : ""}`;

// inserta sólo si en ese instante nadie ocupó el lugar (cierra la carrera entre dos celulares)
async function insertarLibre(env, t, relIds, quien) {
  const ph = relIds.map(() => "?").join(","), ahora = Date.now();
  const r = await env.DB.prepare(
    `INSERT INTO turnos (id, cancha_id, fecha, ini, fin, cliente, tel, nota, precio, sena, estado, tipo_precio, fijo_id, ocurrencia, creado, modificado, creado_por, modificado_por, modificado_en)
     SELECT ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,? WHERE NOT EXISTS (SELECT 1 FROM turnos x WHERE x.fecha = ? AND x.estado != 'cancelado' AND x.ini < ? AND ? < x.fin AND x.cancha_id IN (${ph}))`
  ).bind(t.id, t.cancha_id, t.fecha, t.ini, t.fin, t.cliente, t.tel, t.nota, t.precio, t.sena, "reservado", t.tipo_precio, t.fijo_id || null, t.ocurrencia || null, ahora, ahora, quien, quien, ahora, t.fecha, t.fin, t.ini, ...relIds).run();
  if (!r.meta.changes) throw new Err("Ese horario se acaba de ocupar. Actualizá la pantalla.", 409);
}

async function accion(env, b) {
  const quien = txt(b.quien, 40);
  if (!quien) throw new Err("Poné tu nombre para anotar el cambio");
  const cs = await canchas(env);
  const cancha = (id) => cs.find((x) => x.id === id);
  const relIds = (c) => cs.filter((x) => relacionadas(c, x)).map((x) => x.id);
  const a = b.accion;

  if (a === "crear") {
    const c = cancha(b.cancha); if (!c) throw new Err("La cancha no existe");
    if (!FECHA.test(b.fecha || "")) throw new Err("Fecha inválida");
    const ini = Number(b.ini); validarHorario(c, ini);
    const base = { cliente: txt(b.cliente, 60), tel: txt(b.tel, 30), nota: txt(b.nota, 200) };
    if (b.fijo) {
      for (let i = 0; i < 12; i++) {
        const f = sumarDias(b.fecha, 7 * i), oc = ocupante(cs, await ocupacion(env, f, cs), c.id, ini, ini + c.dur);
        if (oc) throw new Err(`Choca con otro turno el ${f.slice(8)}/${f.slice(5, 7)}. Liberá ese horario antes de crear el fijo.`, 409);
      }
      const id = uid("fx"), ahora = Date.now(), ph = relIds(c).map(() => "?").join(",");
      // sólo se crea si en ese instante otro celular no creó un fijo que se pise con éste (misma carrera que en los turnos comunes)
      const r = await env.DB.prepare(
        `INSERT INTO fijos (id, cancha_id, dow, ini, cliente, tel, nota, desde, hasta, modificado, creado, creado_por, modificado_por, modificado_en)
         SELECT ?,?,?,?,?,?,?,?,NULL,?,?,?,?,? WHERE NOT EXISTS (SELECT 1 FROM fijos x WHERE x.dow = ? AND x.cancha_id IN (${ph}) AND x.ini < ? AND ? < x.ini + (SELECT dur FROM canchas WHERE id = x.cancha_id)
           AND (x.hasta IS NULL OR (x.hasta >= x.desde AND x.hasta >= ?)))`
      ).bind(id, c.id, dow(b.fecha), ini, base.cliente, base.tel, base.nota, b.fecha, ahora, ahora, quien, quien, ahora, dow(b.fecha), ...relIds(c), ini + c.dur, ini, b.fecha).run();
      if (!r.meta.changes) throw new Err("Ese horario fijo se acaba de ocupar. Actualizá la pantalla.", 409);
      await anotar(env, quien, "fijo creado", `Turno FIJO: ${base.cliente || "sin nombre"}, ${c.nombre}, todos los ${DIAS[dow(b.fecha)]} a las ${hm(ini)} (desde el ${b.fecha})`);
      return { ok: true };
    }
    const sena = 0;       // la seña se cobra en la caja de la PC; desde la web sólo se anota en la nota
    const oc = ocupante(cs, await ocupacion(env, b.fecha, cs), c.id, ini, ini + c.dur);
    if (oc) throw new Err(`Ese horario ya está ocupado por ${cancha(oc.cancha_id).nombre}${oc.cliente ? " (" + oc.cliente + ")" : ""}`, 409);
    const t = { id: uid("t"), cancha_id: c.id, fecha: b.fecha, ini, fin: ini + c.dur, ...base, precio: c.precio, sena, tipo_precio: "comun" };
    await insertarLibre(env, t, relIds(c), quien);
    await anotar(env, quien, "turno creado", `Reservado: ${desc(t, c)}`);
    return { ok: true };
  }

  if (a === "cancelar") { // turno común o una fecha de un fijo (suspender)
    let t;
    if (String(b.id).startsWith("F:")) {
      const [, fid, fecha] = b.id.split(":");
      const f = await env.DB.prepare("SELECT * FROM fijos WHERE id = ?").bind(fid).first(); if (!f) throw new Err("El fijo ya no existe");
      const c = cancha(f.cancha_id), id = uid("t"), ahora = Date.now();
      await env.DB.prepare("INSERT INTO turnos (id, cancha_id, fecha, ini, fin, cliente, tel, nota, precio, sena, estado, tipo_precio, fijo_id, ocurrencia, creado, modificado, creado_por, modificado_por, modificado_en) VALUES (?,?,?,?,?,?,?,?,?,0,'cancelado','fijo',?,?,?,?,?,?,?)")
        .bind(id, f.cancha_id, fecha, f.ini, f.ini + c.dur, f.cliente, f.tel, f.nota, c.precio_fijo, fid, fecha, ahora, ahora, quien, quien, ahora).run();
      await anotar(env, quien, "fijo suspendido", `Se suspendió el fijo de ${f.cliente || "sin nombre"}: ${c.nombre}, ${fecha} ${hm(f.ini)}. El horario quedó libre ese día.`);
      return { ok: true };
    }
    t = await env.DB.prepare("SELECT * FROM turnos WHERE id = ?").bind(b.id).first();
    if (!t || t.estado !== "reservado") throw new Err("Ese turno no se puede cancelar");
    if (t.bloqueado) throw new Err(BLOQ);
    const ahora = Date.now();
    await env.DB.prepare("UPDATE turnos SET estado='cancelado', modificado=?, modificado_por=?, modificado_en=? WHERE id=?").bind(ahora, quien, ahora, t.id).run();
    await anotar(env, quien, "turno cancelado", `Cancelado: ${desc(t, cancha(t.cancha_id))}${t.sena ? " (tenía seña $" + t.sena + ")" : ""}`);
    return { ok: true };
  }

  if (a === "editar") {
    const t = await env.DB.prepare("SELECT * FROM turnos WHERE id = ?").bind(b.id).first();
    if (!t || t.estado !== "reservado") throw new Err("Ese turno no se puede editar");
    if (t.bloqueado) throw new Err(BLOQ);
    const n = { cliente: txt(b.cliente, 60), tel: txt(b.tel, 30), nota: txt(b.nota, 200) };
    const ahora = Date.now();
    await env.DB.prepare("UPDATE turnos SET cliente=?, tel=?, nota=?, modificado=?, modificado_por=?, modificado_en=? WHERE id=?").bind(n.cliente, n.tel, n.nota, ahora, quien, ahora, t.id).run();
    await anotar(env, quien, "turno editado", `Editado: ${desc(t, cancha(t.cancha_id))}. Antes: ${t.cliente || "sin nombre"}, tel ${t.tel || "-"}, nota ${t.nota || "-"}. Ahora: ${n.cliente || "sin nombre"}, tel ${n.tel || "-"}, nota ${n.nota || "-"}.`);
    return { ok: true };
  }

  if (a === "editar_fijo") {
    const f = await env.DB.prepare("SELECT * FROM fijos WHERE id = ?").bind(b.fijo_id).first(); if (!f) throw new Err("El fijo ya no existe");
    const ahora = Date.now();
    await env.DB.prepare("UPDATE fijos SET cliente=?, tel=?, nota=?, modificado=?, modificado_por=?, modificado_en=? WHERE id=?").bind(txt(b.cliente, 60), txt(b.tel, 30), txt(b.nota, 200), ahora, quien, ahora, f.id).run();
    await anotar(env, quien, "fijo editado", `Fijo de ${f.cliente || "sin nombre"} → ${txt(b.cliente, 60) || "sin nombre"} (${cancha(f.cancha_id).nombre}, ${DIAS[f.dow]} ${hm(f.ini)})`);
    return { ok: true };
  }

  if (a === "terminar_fijo") {
    const f = await env.DB.prepare("SELECT * FROM fijos WHERE id = ?").bind(b.fijo_id).first(); if (!f) throw new Err("El fijo ya no existe");
    if (!FECHA.test(b.desde || "")) throw new Err("Fecha inválida");
    const ahora = Date.now();
    await env.DB.prepare("UPDATE fijos SET hasta=?, modificado=?, modificado_por=?, modificado_en=? WHERE id=?").bind(sumarDias(b.desde, -1), ahora, quien, ahora, f.id).run();
    await anotar(env, quien, "fijo terminado", `Fijo terminado: ${f.cliente || "sin nombre"}, ${cancha(f.cancha_id).nombre}, ${DIAS[f.dow]} ${hm(f.ini)}. Libre desde el ${b.desde}.`);
    return { ok: true };
  }

  if (a === "mover") { // reprogramar: otra fecha, hora o cancha
    const c = cancha(b.cancha); if (!c) throw new Err("La cancha no existe");
    if (!FECHA.test(b.fecha || "")) throw new Err("Fecha inválida");
    const ini = Number(b.ini); validarHorario(c, ini);
    let t, virtual = String(b.id).startsWith("F:");
    if (virtual) {
      const [, fid, fecha] = b.id.split(":");
      const f = await env.DB.prepare("SELECT * FROM fijos WHERE id = ?").bind(fid).first(); if (!f) throw new Err("El fijo ya no existe");
      t = { id: b.id, cancha_id: f.cancha_id, fecha, ini: f.ini, cliente: f.cliente, tel: f.tel, nota: f.nota, sena: 0, fijo_id: fid, ocurrencia: fecha, tipo_precio: "fijo" };
    } else {
      t = await env.DB.prepare("SELECT * FROM turnos WHERE id = ?").bind(b.id).first();
      if (!t || t.estado !== "reservado") throw new Err("Ese turno no se puede mover");
      if (t.bloqueado) throw new Err(BLOQ);
    }
    const tipo = b.tipo_precio === "fijo" || (b.tipo_precio !== "comun" && t.tipo_precio === "fijo") ? "fijo" : "comun";
    const precio = tipo === "fijo" ? c.precio_fijo : c.precio;
    if (t.sena >= precio) throw new Err("La seña alcanza o supera el nuevo precio");
    const oc = ocupante(cs, await ocupacion(env, b.fecha, cs), c.id, ini, ini + c.dur, b.id);
    if (oc) throw new Err(`Ese horario ya está ocupado por ${cancha(oc.cancha_id).nombre}${oc.cliente ? " (" + oc.cliente + ")" : ""}`, 409);
    const antes = desc(t, cancha(t.cancha_id));
    if (virtual) { // el fijo de esa semana se traslada: la fecha original queda libre y se crea el turno nuevo
      const ahora = Date.now(), k = cancha(t.cancha_id);
      await insertarLibre(env, { id: uid("t"), cancha_id: c.id, fecha: b.fecha, ini, fin: ini + c.dur, cliente: t.cliente, tel: t.tel, nota: t.nota, precio, sena: 0, tipo_precio: tipo }, relIds(c), quien);
      await env.DB.prepare("INSERT INTO turnos (id, cancha_id, fecha, ini, fin, cliente, tel, nota, precio, sena, estado, tipo_precio, fijo_id, ocurrencia, creado, modificado, creado_por, modificado_por, modificado_en) VALUES (?,?,?,?,?,?,?,?,?,0,'cancelado','fijo',?,?,?,?,?,?,?)")
        .bind(uid("t"), t.cancha_id, t.ocurrencia, t.ini, t.ini + k.dur, t.cliente, t.tel, t.nota, k.precio_fijo, t.fijo_id, t.ocurrencia, ahora, ahora, quien, quien, ahora).run();
    } else {
      const ahora = Date.now();
      const r = await env.DB.prepare("UPDATE turnos SET cancha_id=?, fecha=?, ini=?, fin=?, precio=?, tipo_precio=?, modificado=?, modificado_por=?, modificado_en=? WHERE id=? AND estado='reservado' AND NOT EXISTS (SELECT 1 FROM turnos x WHERE x.id != ? AND x.fecha = ? AND x.estado != 'cancelado' AND x.ini < ? AND ? < x.fin AND x.cancha_id IN (" + relIds(c).map(() => "?").join(",") + "))")
        .bind(c.id, b.fecha, ini, ini + c.dur, precio, tipo, ahora, quien, ahora, t.id, t.id, b.fecha, ini + c.dur, ini, ...relIds(c)).run();
      if (!r.meta.changes) throw new Err("Ese horario se acaba de ocupar. Actualizá la pantalla.", 409);
    }
    await anotar(env, quien, "turno reprogramado", `Reprogramado. Antes: ${antes}. Ahora: ${c.nombre}, ${b.fecha} ${hm(ini)}, precio ${tipo === "fijo" ? "de fijo" : "común"} ($${precio}).`);
    return { ok: true };
  }
  throw new Err("Acción desconocida");
}

// ---- búsqueda por nombre o teléfono ----
// Argentina no tiene horario de verano: siempre UTC-3. «Hoy» se calcula acá para que el celular y la nube coincidan.
const hoyAR = () => new Date(Date.now() - 3 * 36e5).toISOString().slice(0, 10);
const sinAcentos = (s) => String(s ?? "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
const soloDigitos = (s) => String(s ?? "").replace(/\D/g, "");
// el teléfono se compara sin el 0 de la característica, sin el 54/549 del país y sin el 15 de los celulares viejos:
// «011 15 5555-1234» y «+54 9 11 5555 1234» son el mismo número
function telCanon(d) {
  d = d.replace(/^0+/, "").replace(/^549?(?=\d{10})/, "");
  if (d.length === 12) for (const a of [2, 3, 4]) if (d.slice(a, a + 2) === "15") return d.slice(0, a) + d.slice(a + 2);
  return d;
}
// ¿ese teléfono guardado contiene lo tipeado? (se prueba el número «limpio» y también los dígitos tal cual, para quien tipea un pedazo)
const telContiene = (guardado, tipeado) => { const g = soloDigitos(guardado), q = soloDigitos(tipeado); return !!q && (telCanon(g).includes(telCanon(q)) || g.includes(q)); };
function distancia(a, b, max) { // distancia de edición, cortada en `max`
  if (Math.abs(a.length - b.length) > max) return max + 1;
  let prev = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i++) {
    const cur = [i];
    for (let j = 1; j <= b.length; j++) cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
    prev = cur;
  }
  return prev[b.length];
}
// arma el buscador de una consulta; `tolerante` perdona una letra de más, de menos o cambiada en palabras de 4 letras o más
function armarBusqueda(q, tolerante) {
  const crudo = String(q ?? "").trim().slice(0, 60);
  if (/^[\d\s\-+().]+$/.test(crudo)) { // sólo números: es un teléfono
    return soloDigitos(crudo).length >= 2 ? (t) => telContiene(t.tel, crudo) : null;
  }
  const palabras = sinAcentos(crudo).split(/\s+/).filter(Boolean);
  if (!palabras.length || palabras.join("").length < 2) return null;
  return (t) => {
    const nombre = sinAcentos(t.cliente), pals = nombre.split(/[^a-z0-9]+/).filter(Boolean);
    return palabras.every((p) => {
      if (/\d/.test(p)) return telContiene(t.tel, p);
      if (nombre.includes(p)) return true;
      return tolerante && p.length >= 4 && pals.some((w) => distancia(p, w, 1) <= 1 || distancia(p, w.slice(0, p.length), 1) <= 1);
    });
  };
}
// próxima fecha (desde `desde`) en que ese fijo tiene turno de verdad: salta las semanas suspendidas o movidas
function proximaDeFijo(f, desde, saltadas) {
  let d = f.desde > desde ? f.desde : desde;
  d = sumarDias(d, (f.dow - dow(d) + 7) % 7);
  for (let i = 0; i < 60; i++, d = sumarDias(d, 7)) {
    if (f.hasta && d > f.hasta) return null;
    if (!saltadas.has(f.id + "|" + d)) return d;
  }
  return null;
}
async function buscar(env, q) {
  const hoy = hoyAR(), desde = sumarDias(hoy, -30);
  if (!armarBusqueda(q, false)) return { q, hoy, turnos: [], fijos: [], corta: true };      // muy corta: ni se consulta la base
  const campos = "id, cancha_id, fecha, ini, fin, cliente, tel, nota, estado, tipo_precio, fijo_id";
  const todos = (await env.DB.prepare(`SELECT ${campos} FROM turnos WHERE fecha >= ? AND estado != 'cancelado' ORDER BY fecha, ini LIMIT 6000`).bind(desde).all()).results;
  const fijos = (await env.DB.prepare("SELECT * FROM fijos WHERE hasta IS NULL OR (hasta >= ? AND hasta >= desde)").bind(hoy).all()).results;
  const saltadas = new Set((await env.DB.prepare("SELECT fijo_id, ocurrencia FROM turnos WHERE estado = 'cancelado' AND fijo_id IS NOT NULL AND ocurrencia >= ?").bind(hoy).all()).results.map((r) => r.fijo_id + "|" + r.ocurrencia));
  let turnos = [], hallados = [];
  for (const tolerante of [false, true]) {
    const quiere = armarBusqueda(q, tolerante);
    turnos = todos.filter(quiere);
    hallados = fijos.filter(quiere);
    if (turnos.length || hallados.length) break;
  }
  // si hay muchísimos, se quedan los más cercanos a hoy: hasta 120 próximos y 60 anteriores
  const prox = turnos.filter((t) => t.fecha >= hoy), ant = turnos.filter((t) => t.fecha < hoy);
  const truncado = prox.length > 120 || ant.length > 60;
  turnos = ant.slice(-60).concat(prox.slice(0, 120));
  return { q, hoy, turnos, truncado, fijos: hallados.map((f) => ({ id: f.id, cancha_id: f.cancha_id, dow: f.dow, ini: f.ini, cliente: f.cliente, tel: f.tel, nota: f.nota, desde: f.desde, hasta: f.hasta, proxima: proximaDeFijo(f, hoy, saltadas) })).sort((a, b) => (a.proxima || "9").localeCompare(b.proxima || "9") || a.ini - b.ini) };
}

export async function manejarTurnos(req, env, ruta, url) {
  try {
    await asegurarColumnas(env).catch(() => {});      // si no se pudo, lo de abajo falla con su propio mensaje claro
    if (ruta === "/api/turnos" && req.method === "GET") {
      const f = url.searchParams.get("fecha") || "";
      if (!FECHA.test(f)) throw new Err("Fecha inválida");
      const cs = await canchas(env);
      return json({ canchas: cs, turnos: await ocupacion(env, f, cs), ahora: Date.now() });
    }
    if (ruta === "/api/turnos" && req.method === "POST") {
      let b; try { b = await req.json(); } catch { throw new Err("Pedido inválido"); }
      return json(await accion(env, b));
    }
    if (ruta === "/api/turnos/semana" && req.method === "GET") { // 7 días seguidos desde `desde`, de una sola vez
      const d = url.searchParams.get("desde") || "";
      if (!FECHA.test(d)) throw new Err("Fecha inválida");
      const cs = await canchas(env);
      return json({ canchas: cs, desde: d, turnos: await ocupacionRango(env, d, 7, cs), ahora: Date.now() });
    }
    if (ruta === "/api/turnos/buscar" && req.method === "GET") return json(await buscar(env, url.searchParams.get("q")));
    if (ruta === "/api/turnos/registro" && req.method === "GET") {
      const r = (await env.DB.prepare("SELECT ts, quien, accion, detalle FROM registro_turnos ORDER BY id DESC LIMIT 200").all()).results;
      return json({ registro: r });
    }
  } catch (e) {
    if (e instanceof Err) return json({ error: e.message }, e.status);
    return json({ error: "Error del servidor" }, 500);
  }
  return null;
}
