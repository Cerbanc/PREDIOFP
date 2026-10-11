// Sincronización con la PC del local (clave de la PC). La PC manda lo que cambió allá y baja lo que cambió acá (celulares).
// Cada registro lleva `modificado`: la PC dice sobre qué versión se basó (base_mod); si en el medio cambió otra cosa, se rechaza y la PC baja la nueva.
import { listarCanchas, ocupacionDia, ocupante } from "./turnos.js";

const json = (o, s = 200) => new Response(JSON.stringify(o), { status: s, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" } });
const FECHA = /^\d{4}-\d{2}-\d{2}$/;
const ent = (v, d = 0) => (Number.isFinite(Number(v)) ? Math.round(Number(v)) : d);
const txt = (v, n) => String(v ?? "").slice(0, n);

export async function manejarSync(req, env, url) {
  if (req.method === "GET") {
    const desde = ent(url.searchParams.get("desde"), 0);
    const [turnos, fijos, canchas] = await Promise.all([
      env.DB.prepare("SELECT * FROM turnos WHERE modificado >= ? ORDER BY modificado LIMIT 2000").bind(desde).all(),
      env.DB.prepare("SELECT * FROM fijos WHERE modificado >= ? ORDER BY modificado LIMIT 500").bind(desde).all(),
      env.DB.prepare("SELECT * FROM canchas ORDER BY orden").all(),
    ]);
    return json({ turnos: turnos.results, fijos: fijos.results, canchas: canchas.results, ahora: Date.now() });
  }
  if (req.method !== "POST") return json({ error: "método no permitido" }, 405);
  let b; try { b = await req.json(); } catch { return json({ error: "json inválido" }, 400); }
  const resultados = { canchas: 0, fijos: [], turnos: [] };
  const cs0 = await listarCanchas(env);

  for (const c of (b.canchas || []).slice(0, 20)) {
    const id = txt(c.id, 20); if (!id || !c.nombre) continue;
    await env.DB.prepare("INSERT INTO canchas (id, clave, nombre, deporte, dur, ap, ci, precio, precio_fijo, orden) VALUES (?1,?1,?2,?3,?4,?5,?6,?7,?8,?9) ON CONFLICT(id) DO UPDATE SET nombre=?2, deporte=?3, dur=?4, ap=?5, ci=?6, precio=?7, precio_fijo=?8, orden=?9")
      .bind(id, txt(c.nombre, 60), txt(c.deporte, 20), ent(c.dur, 60), ent(c.ap, 0), ent(c.ci, 1440), ent(c.precio), ent(c.precio_fijo), ent(c.orden)).run();
    resultados.canchas++;
  }
  const cs = resultados.canchas ? await listarCanchas(env) : cs0;

  for (const f of (b.fijos || []).slice(0, 200)) {
    const id = txt(f.id, 80); if (!id || !FECHA.test(f.desde || "") || !cs.find((x) => x.id === f.cancha_id)) { resultados.fijos.push({ id, conflicto: "datos" }); continue; }
    const ex = await env.DB.prepare("SELECT modificado FROM fijos WHERE id = ?").bind(id).first();
    if (ex && ent(f.base_mod, -1) !== ex.modificado) { resultados.fijos.push({ id, conflicto: "version" }); continue; }
    const mod = Date.now();
    await env.DB.prepare("INSERT INTO fijos (id, cancha_id, dow, ini, cliente, tel, nota, desde, hasta, modificado) VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10) ON CONFLICT(id) DO UPDATE SET cancha_id=?2, dow=?3, ini=?4, cliente=?5, tel=?6, nota=?7, desde=?8, hasta=?9, modificado=?10")
      .bind(id, f.cancha_id, ent(f.dow), ent(f.ini), txt(f.cliente, 60), txt(f.tel, 30), txt(f.nota, 200), f.desde, FECHA.test(f.hasta || "") ? f.hasta : null, mod).run();
    resultados.fijos.push({ id, ok: true, mod });
  }

  for (const t of (b.turnos || []).slice(0, 500)) {
    const id = txt(t.id, 80), c = cs.find((x) => x.id === t.cancha_id);
    if (!id || !c || !FECHA.test(t.fecha || "")) { resultados.turnos.push({ id, conflicto: "datos" }); continue; }
    const ex = await env.DB.prepare("SELECT modificado FROM turnos WHERE id = ?").bind(id).first();
    if (ex && ent(t.base_mod, -1) !== ex.modificado) { resultados.turnos.push({ id, conflicto: "version" }); continue; }
    const estado = ["reservado", "cancelado", "cobrado"].includes(t.estado) ? t.estado : "reservado";
    if (estado !== "cancelado") { // no puede pisar a otro turno ya cargado (el propio lugar de un fijo no cuenta)
      const lista = (await ocupacionDia(env, t.fecha, cs)).filter((x) => !(x.fijo_id && x.fijo_id === (t.fijo_id || null) && x.ocurrencia === (t.ocurrencia || null)));
      const oc = ocupante(cs, lista, c.id, ent(t.ini), ent(t.fin), id);
      if (oc) { resultados.turnos.push({ id, conflicto: "choque", con: `${cs.find((x) => x.id === oc.cancha_id).nombre}${oc.cliente ? " (" + oc.cliente + ")" : ""}` }); continue; }
    }
    const mod = Date.now();
    await env.DB.prepare(
      `INSERT INTO turnos (id, cancha_id, fecha, ini, fin, cliente, tel, nota, precio, sena, estado, tipo_precio, fijo_id, ocurrencia, creado, modificado, bloqueado)
       VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,?14,?15,?15,?16)
       ON CONFLICT(id) DO UPDATE SET cancha_id=?2, fecha=?3, ini=?4, fin=?5, cliente=?6, tel=?7, nota=?8, precio=?9, sena=?10, estado=?11, tipo_precio=?12, fijo_id=?13, ocurrencia=?14, modificado=?15, bloqueado=?16`
    ).bind(id, c.id, t.fecha, ent(t.ini), ent(t.fin), txt(t.cliente, 60), txt(t.tel, 30), txt(t.nota, 200), ent(t.precio), ent(t.sena), estado, t.tipo_precio === "fijo" ? "fijo" : "comun", t.fijo_id || null, FECHA.test(t.ocurrencia || "") ? t.ocurrencia : null, mod, t.bloqueado ? 1 : 0).run();
    resultados.turnos.push({ id, ok: true, mod });
  }
  return json({ ok: true, ...resultados, ahora: Date.now() });
}
