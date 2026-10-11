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

class Err extends Error { constructor(m, s = 400) { super(m); this.status = s; } }
const json = (o, s = 200) => new Response(JSON.stringify(o), { status: s, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" } });

async function canchas(env) { return (await env.DB.prepare("SELECT * FROM canchas ORDER BY orden").all()).results; }

// turnos reales + ocurrencias de turnos fijos (virtuales) de un día
async function ocupacion(env, fecha, cs) {
  const reales = (await env.DB.prepare("SELECT * FROM turnos WHERE fecha = ? AND estado != 'cancelado'").bind(fecha).all()).results;
  const fijos = (await env.DB.prepare(
    "SELECT f.* FROM fijos f WHERE f.dow = ?1 AND f.desde <= ?2 AND (f.hasta IS NULL OR f.hasta >= ?2) AND NOT EXISTS (SELECT 1 FROM turnos t WHERE t.fijo_id = f.id AND t.ocurrencia = ?2)"
  ).bind(dow(fecha), fecha).all()).results;
  const virt = fijos.map((f) => {
    const c = cs.find((x) => x.id === f.cancha_id);
    return { virtual: true, id: `F:${f.id}:${fecha}`, cancha_id: f.cancha_id, fecha, ini: f.ini, fin: f.ini + c.dur, cliente: f.cliente, tel: f.tel, nota: f.nota, precio: c.precio_fijo, sena: 0, estado: "reservado", tipo_precio: "fijo", fijo_id: f.id, ocurrencia: fecha };
  });
  return reales.concat(virt);
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
async function insertarLibre(env, t, relIds) {
  const ph = relIds.map(() => "?").join(",");
  const r = await env.DB.prepare(
    `INSERT INTO turnos (id, cancha_id, fecha, ini, fin, cliente, tel, nota, precio, sena, estado, tipo_precio, fijo_id, ocurrencia, creado, modificado)
     SELECT ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,? WHERE NOT EXISTS (SELECT 1 FROM turnos x WHERE x.fecha = ? AND x.estado != 'cancelado' AND x.ini < ? AND ? < x.fin AND x.cancha_id IN (${ph}))`
  ).bind(t.id, t.cancha_id, t.fecha, t.ini, t.fin, t.cliente, t.tel, t.nota, t.precio, t.sena, "reservado", t.tipo_precio, t.fijo_id || null, t.ocurrencia || null, Date.now(), Date.now(), t.fecha, t.fin, t.ini, ...relIds).run();
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
      const id = uid("fx");
      await env.DB.prepare("INSERT INTO fijos (id, cancha_id, dow, ini, cliente, tel, nota, desde, hasta) VALUES (?,?,?,?,?,?,?,?,NULL)").bind(id, c.id, dow(b.fecha), ini, base.cliente, base.tel, base.nota, b.fecha).run();
      await anotar(env, quien, "fijo creado", `Turno FIJO: ${base.cliente || "sin nombre"}, ${c.nombre}, todos los ${DIAS[dow(b.fecha)]} a las ${hm(ini)} (desde el ${b.fecha})`);
      return { ok: true };
    }
    const sena = Math.max(0, Math.round(Number(b.sena) || 0));
    if (sena >= c.precio) throw new Err("La seña tiene que ser menor al precio");
    const oc = ocupante(cs, await ocupacion(env, b.fecha, cs), c.id, ini, ini + c.dur);
    if (oc) throw new Err(`Ese horario ya está ocupado por ${cancha(oc.cancha_id).nombre}${oc.cliente ? " (" + oc.cliente + ")" : ""}`, 409);
    const t = { id: uid("t"), cancha_id: c.id, fecha: b.fecha, ini, fin: ini + c.dur, ...base, precio: c.precio, sena, tipo_precio: "comun" };
    await insertarLibre(env, t, relIds(c));
    await anotar(env, quien, "turno creado", `Reservado: ${desc(t, c)}${sena ? ", seña $" + sena : ""}`);
    return { ok: true };
  }

  if (a === "cancelar") { // turno común o una fecha de un fijo (suspender)
    let t;
    if (String(b.id).startsWith("F:")) {
      const [, fid, fecha] = b.id.split(":");
      const f = await env.DB.prepare("SELECT * FROM fijos WHERE id = ?").bind(fid).first(); if (!f) throw new Err("El fijo ya no existe");
      const c = cancha(f.cancha_id), id = uid("t");
      await env.DB.prepare("INSERT INTO turnos (id, cancha_id, fecha, ini, fin, cliente, tel, nota, precio, sena, estado, tipo_precio, fijo_id, ocurrencia, creado, modificado) VALUES (?,?,?,?,?,?,?,?,?,0,'cancelado','fijo',?,?,?,?)")
        .bind(id, f.cancha_id, fecha, f.ini, f.ini + c.dur, f.cliente, f.tel, f.nota, c.precio_fijo, fid, fecha, Date.now(), Date.now()).run();
      await anotar(env, quien, "fijo suspendido", `Se suspendió el fijo de ${f.cliente || "sin nombre"}: ${c.nombre}, ${fecha} ${hm(f.ini)}. El horario quedó libre ese día.`);
      return { ok: true };
    }
    t = await env.DB.prepare("SELECT * FROM turnos WHERE id = ?").bind(b.id).first();
    if (!t || t.estado !== "reservado") throw new Err("Ese turno no se puede cancelar");
    await env.DB.prepare("UPDATE turnos SET estado='cancelado', modificado=? WHERE id=?").bind(Date.now(), t.id).run();
    await anotar(env, quien, "turno cancelado", `Cancelado: ${desc(t, cancha(t.cancha_id))}${t.sena ? " (tenía seña $" + t.sena + ")" : ""}`);
    return { ok: true };
  }

  if (a === "editar") {
    const t = await env.DB.prepare("SELECT * FROM turnos WHERE id = ?").bind(b.id).first();
    if (!t || t.estado !== "reservado") throw new Err("Ese turno no se puede editar");
    const n = { cliente: txt(b.cliente, 60), tel: txt(b.tel, 30), nota: txt(b.nota, 200), sena: Math.max(0, Math.round(Number(b.sena) || 0)) };
    if (n.sena >= t.precio) throw new Err("La seña tiene que ser menor al precio");
    await env.DB.prepare("UPDATE turnos SET cliente=?, tel=?, nota=?, sena=?, modificado=? WHERE id=?").bind(n.cliente, n.tel, n.nota, n.sena, Date.now(), t.id).run();
    await anotar(env, quien, "turno editado", `Editado: ${desc(t, cancha(t.cancha_id))}. Antes: ${t.cliente || "sin nombre"}, tel ${t.tel || "-"}, seña $${t.sena}. Ahora: ${n.cliente || "sin nombre"}, tel ${n.tel || "-"}, seña $${n.sena}.`);
    return { ok: true };
  }

  if (a === "editar_fijo") {
    const f = await env.DB.prepare("SELECT * FROM fijos WHERE id = ?").bind(b.fijo_id).first(); if (!f) throw new Err("El fijo ya no existe");
    await env.DB.prepare("UPDATE fijos SET cliente=?, tel=?, nota=? WHERE id=?").bind(txt(b.cliente, 60), txt(b.tel, 30), txt(b.nota, 200), f.id).run();
    await anotar(env, quien, "fijo editado", `Fijo de ${f.cliente || "sin nombre"} → ${txt(b.cliente, 60) || "sin nombre"} (${cancha(f.cancha_id).nombre}, ${DIAS[f.dow]} ${hm(f.ini)})`);
    return { ok: true };
  }

  if (a === "terminar_fijo") {
    const f = await env.DB.prepare("SELECT * FROM fijos WHERE id = ?").bind(b.fijo_id).first(); if (!f) throw new Err("El fijo ya no existe");
    if (!FECHA.test(b.desde || "")) throw new Err("Fecha inválida");
    await env.DB.prepare("UPDATE fijos SET hasta=? WHERE id=?").bind(sumarDias(b.desde, -1), f.id).run();
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
    }
    const tipo = b.tipo_precio === "fijo" || (b.tipo_precio !== "comun" && t.tipo_precio === "fijo") ? "fijo" : "comun";
    const precio = tipo === "fijo" ? c.precio_fijo : c.precio;
    if (t.sena >= precio) throw new Err("La seña alcanza o supera el nuevo precio");
    const oc = ocupante(cs, await ocupacion(env, b.fecha, cs), c.id, ini, ini + c.dur, b.id);
    if (oc) throw new Err(`Ese horario ya está ocupado por ${cancha(oc.cancha_id).nombre}${oc.cliente ? " (" + oc.cliente + ")" : ""}`, 409);
    const antes = desc(t, cancha(t.cancha_id));
    if (virtual) { // el fijo de esa semana se traslada: la fecha original queda libre y se crea el turno nuevo
      const ahora = Date.now(), k = cancha(t.cancha_id);
      await insertarLibre(env, { id: uid("t"), cancha_id: c.id, fecha: b.fecha, ini, fin: ini + c.dur, cliente: t.cliente, tel: t.tel, nota: t.nota, precio, sena: 0, tipo_precio: tipo }, relIds(c));
      await env.DB.prepare("INSERT INTO turnos (id, cancha_id, fecha, ini, fin, cliente, tel, nota, precio, sena, estado, tipo_precio, fijo_id, ocurrencia, creado, modificado) VALUES (?,?,?,?,?,?,?,?,?,0,'cancelado','fijo',?,?,?,?)")
        .bind(uid("t"), t.cancha_id, t.ocurrencia, t.ini, t.ini + k.dur, t.cliente, t.tel, t.nota, k.precio_fijo, t.fijo_id, t.ocurrencia, ahora, ahora).run();
    } else {
      const r = await env.DB.prepare("UPDATE turnos SET cancha_id=?, fecha=?, ini=?, fin=?, precio=?, tipo_precio=?, modificado=? WHERE id=? AND estado='reservado' AND NOT EXISTS (SELECT 1 FROM turnos x WHERE x.id != ? AND x.fecha = ? AND x.estado != 'cancelado' AND x.ini < ? AND ? < x.fin AND x.cancha_id IN (" + relIds(c).map(() => "?").join(",") + "))")
        .bind(c.id, b.fecha, ini, ini + c.dur, precio, tipo, Date.now(), t.id, t.id, b.fecha, ini + c.dur, ini, ...relIds(c)).run();
      if (!r.meta.changes) throw new Err("Ese horario se acaba de ocupar. Actualizá la pantalla.", 409);
    }
    await anotar(env, quien, "turno reprogramado", `Reprogramado. Antes: ${antes}. Ahora: ${c.nombre}, ${b.fecha} ${hm(ini)}, precio ${tipo === "fijo" ? "de fijo" : "común"} ($${precio}).`);
    return { ok: true };
  }
  throw new Err("Acción desconocida");
}

export async function manejarTurnos(req, env, ruta, url) {
  try {
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
