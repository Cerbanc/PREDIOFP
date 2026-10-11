// Reportes por período (semana, mes, fechas libres) armados con los resúmenes diarios que sube la PC. Sólo lectura.
//  - GET /api/rango?desde=&hasta=[&ant_desde=&ant_hasta=]  (sesión de la web): el período ya sumado, día por día, y el período anterior para comparar.
//  - GET /api/subidos?desde=  (clave de la PC): qué días tiene la nube y con qué huella, para que la PC complete los que le faltan (ver predio/nube.py).
// Un día sin fila en la tabla `dias` es "falta" (la PC no lo subió): nunca se cuenta como cero. Un día con fila pero sin nada es "vacio" (sin movimiento).

const json = (o, s = 200) => new Response(JSON.stringify(o), { status: s, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" } });
const DIA = /^\d{4}-\d{2}-\d{2}$/;
export const MAX_DIAS = 400;
const n = (v) => { const x = Number(v); return Number.isFinite(x) ? x : 0; };
const lista = (v) => (Array.isArray(v) ? v : []);

const aMs = (d) => Date.UTC(+d.slice(0, 4), +d.slice(5, 7) - 1, +d.slice(8, 10));
const deMs = (ms) => new Date(ms).toISOString().slice(0, 10);
export const sumarDias = (d, k) => deMs(aMs(d) + k * 86400000);
export const cuentaDias = (a, b) => Math.round((aMs(b) - aMs(a)) / 86400000) + 1;
export const diaValido = (s) => DIA.test(s || "") && deMs(aMs(s)) === s;          // rechaza 2026-02-31
export const hoyArgentina = () => new Intl.DateTimeFormat("en-CA", { timeZone: "America/Argentina/Buenos_Aires", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());

// Un día con fila pero sin ventas, ni cajas, ni cobros, ni fiado: la PC estuvo pero no hubo movimiento.
function sinMovimiento(r) {
  const k = r.kpis || {}, fi = r.fiado || {};
  return !n(k.tickets) && !n(k.anulados) && !n(k.total_vendido) && !n(k.cobrado_neto) && !lista(r.cajas).length && !n(fi.cargos) && !n(fi.pagos) && !n(fi.anulaciones);
}

const sumarEn = (mapa, clave, campos, fila) => {
  const a = mapa.get(clave) || Object.fromEntries(campos.map((c) => [c, 0]));
  for (const c of campos) a[c] += n(fila[c]);
  mapa.set(clave, a);
};

// filas: [{ dia, subido, r }] con r = resumen ya convertido a objeto (o null si no se pudo leer).
export function agregarPeriodo(filas, desde, hasta, hoy) {
  const porDia = new Map(filas.map((f) => [f.dia, f]));
  const dias = [], cierresPorCaja = new Map();
  const t = { total_vendido: 0, tickets: 0, anulados: 0, descuentos: 0, cobrado_neto: 0 };
  const medios = new Map(), cats = new Map(), dests = new Map(), prods = new Map();
  const fiado = { cargos: 0, pagos: 0, anulaciones: 0 }, movs = {};
  let stock = null, ultimoConResumen = null;

  for (let d = desde; d <= hasta; d = sumarDias(d, 1)) {
    const f = porDia.get(d);
    if (!f || !f.r || typeof f.r !== "object") { dias.push({ dia: d, estado: d > hoy ? "futuro" : "falta" }); continue; }
    const r = f.r, k = r.kpis || {}, fi = r.fiado || {}, cajas = lista(r.cajas);
    const vacio = sinMovimiento(r);
    for (const c of cajas) {
      const clave = `${c.abierta}|${c.responsable}`, previo = cierresPorCaja.get(clave);      // una caja que cruza la medianoche aparece en dos días: se cuenta una sola vez
      if (previo && (previo.cerrada || !c.cerrada)) continue;
      cierresPorCaja.set(clave, {
        dia: previo ? previo.dia : d, responsable: String(c.responsable || "").slice(0, 60), abierta: c.abierta || null, cerrada: c.cerrada || null,
        fondo: n(c.fondo), esperado: n(c.esperado), contado: n(c.contado), diferencia: c.cerrada ? n(c.diferencia) : 0, nota: String(c.nota || "").slice(0, 300),
        digital: lista(c.digital).slice(0, 8).map((g) => ({ nombre: String(g.nombre || "").slice(0, 40), esperado: n(g.esperado), informado: g.informado == null ? null : n(g.informado), diferencia: g.diferencia == null ? null : n(g.diferencia) })),
      });
    }
    const dia = { dia: d, estado: vacio ? "vacio" : "datos", total: n(k.total_vendido), tickets: n(k.tickets), descuentos: n(k.descuentos), cobrado: n(k.cobrado_neto), dif: 0, dif_dig: 0, subido: f.subido || 0 };
    dias.push(dia);
    ultimoConResumen = d;

    t.total_vendido += dia.total; t.tickets += dia.tickets; t.anulados += n(k.anulados); t.descuentos += dia.descuentos; t.cobrado_neto += dia.cobrado;
    for (const x of lista(r.por_medio)) sumarEn(medios, String(x.medio || "?"), ["cobros", "devoluciones", "correcciones", "neto"], x);
    for (const x of lista(r.por_categoria)) sumarEn(cats, String(x.categoria || "Sin categoría"), ["unidades", "importe"], x);
    for (const x of lista(r.por_destino)) sumarEn(dests, String(x.destino || "Mostrador"), ["tickets", "total"], x);
    for (const x of lista(r.top_productos)) sumarEn(prods, String(x.producto || "?"), ["unidades", "importe"], x);
    for (const c of ["cargos", "pagos", "anulaciones"]) fiado[c] += n(fi[c]);
    for (const [a, b] of Object.entries(r.movimientos_caja || {})) movs[a] = (movs[a] || 0) + n(b);
    stock = { dia: d, lista: lista(r.stock_bajo) };
  }

  const delDia = new Map(dias.map((x) => [x.dia, x]));
  for (const c of cierresPorCaja.values()) if (c.cerrada && delDia.has(c.dia)) {            // la diferencia se anota una sola vez, en el día en que se abrió la caja
    const x = delDia.get(c.dia);
    x.dif += n(c.diferencia); x.dif_dig += c.digital.reduce((a, g) => a + n(g.diferencia), 0);
  }
  const conVentas = dias.filter((x) => x.estado === "datos" && x.tickets > 0);
  let mejor = null, peor = null;
  for (const x of conVentas) { if (!mejor || x.total > mejor.total) mejor = x; if (!peor || x.total < peor.total) peor = x; }
  if (conVentas.length < 2) peor = null;
  const pasados = dias.filter((x) => x.estado !== "futuro");
  const cierres = [...cierresPorCaja.values()].sort((a, b) => (b.dia < a.dia ? -1 : b.dia > a.dia ? 1 : n(b.abierta) - n(a.abierta)));
  const cerrados = cierres.filter((c) => c.cerrada);
  const conDif = (c) => n(c.diferencia) !== 0 || c.digital.some((g) => n(g.diferencia) !== 0);
  const orden = (m, campo) => [...m.entries()].map(([nombre, v]) => ({ nombre, ...v })).sort((a, b) => b[campo] - a[campo] || (a.nombre < b.nombre ? -1 : 1));

  return {
    desde, hasta, hoy,
    dias,
    totales: {
      ...t, ticket_promedio: t.tickets ? Math.round(t.total_vendido / t.tickets) : 0,
      dias_total: pasados.length, dias_con_datos: dias.filter((x) => x.estado === "datos").length, dias_vacios: dias.filter((x) => x.estado === "vacio").length,
      dias_sin_datos: dias.filter((x) => x.estado === "falta").length, dias_futuros: dias.length - pasados.length,
      promedio_dia: conVentas.length ? Math.round(t.total_vendido / conVentas.length) : 0,
    },
    mejor: mejor && { dia: mejor.dia, total: mejor.total, tickets: mejor.tickets },
    peor: peor && { dia: peor.dia, total: peor.total, tickets: peor.tickets },
    por_medio: orden(medios, "neto").map(({ nombre, ...v }) => ({ medio: nombre, ...v })),
    por_categoria: orden(cats, "importe").map(({ nombre, ...v }) => ({ categoria: nombre, ...v })),
    por_destino: orden(dests, "total").map(({ nombre, ...v }) => ({ destino: nombre, ...v })),
    top_productos: orden(prods, "unidades").slice(0, 20).map(({ nombre, ...v }) => ({ producto: nombre, ...v })),
    fiado, movimientos_caja: movs, stock_bajo: stock,
    cierres, cierres_resumen: {
      cajas: cierres.length, cerradas: cerrados.length, abiertas: cierres.length - cerrados.length,
      con_diferencia: cerrados.filter(conDif).length,
      dias_con_cierre: new Set(cerrados.map((c) => c.dia)).size, dias_con_diferencia: new Set(cerrados.filter(conDif).map((c) => c.dia)).size,
    },
    actualizado: Math.max(0, ...filas.map((f) => n(f.subido))), ultimo_dia: ultimoConResumen,
  };
}

const leer = (filas) => filas.map((f) => { let r = null; try { r = JSON.parse(f.r); } catch { /* fila ilegible: se trata como día que falta */ } return { dia: f.dia, subido: f.subido, r }; });

// Sin el listado de cambios (es lo más pesado y acá no se usa), para que 12 meses entren en una sola consulta liviana.
const SQL_PERIODO = "SELECT dia, subido, json_remove(resumen, '$.cambios_importantes') AS r FROM dias WHERE dia >= ?1 AND dia <= ?2 ORDER BY dia";
const SQL_ANTERIOR = "SELECT dia, subido, json_object('kpis', json_extract(resumen, '$.kpis'), 'fiado', json_extract(resumen, '$.fiado'), 'cajas', json_extract(resumen, '$.cajas')) AS r FROM dias WHERE dia >= ?1 AND dia <= ?2 ORDER BY dia";

function pedirRango(url, claveDesde, claveHasta) {
  const desde = url.searchParams.get(claveDesde) || "", hasta = url.searchParams.get(claveHasta) || "";
  if (!diaValido(desde) || !diaValido(hasta)) return { error: "Las fechas no son válidas." };
  if (desde > hasta) return { error: "La fecha «desde» tiene que ser anterior a «hasta»." };
  if (cuentaDias(desde, hasta) > MAX_DIAS) return { error: `El período es muy largo: el máximo es de ${MAX_DIAS} días.` };
  return { desde, hasta };
}

// auth = { dentro: la sesión de la web es válida, pc: async () => la clave de la PC es válida }
export async function manejarReportes(req, env, ruta, url, auth) {
  if (ruta === "/api/subidos") {
    if (req.method !== "GET") return json({ error: "método no permitido" }, 405);
    if (!(await auth.pc())) return json({ error: "clave incorrecta" }, 401);
    const desde = url.searchParams.get("desde") || "";
    if (!diaValido(desde)) return json({ error: "fecha inválida" }, 400);
    const { results } = await env.DB.prepare("SELECT dia, json_extract(resumen, '$.huella') AS huella FROM dias WHERE dia >= ?1 ORDER BY dia DESC LIMIT ?2").bind(desde, MAX_DIAS * 2).all();
    return json({ dias: Object.fromEntries(results.map((x) => [x.dia, x.huella || ""])), ahora: Date.now() });
  }
  if (ruta !== "/api/rango") return null;
  if (!auth.dentro) return json({ error: "no autorizado" }, 401);
  if (req.method !== "GET") return json({ error: "método no permitido" }, 405);
  const p = pedirRango(url, "desde", "hasta");
  if (p.error) return json({ error: p.error }, 400);
  const hoy = hoyArgentina();
  const salida = agregarPeriodo(leer((await env.DB.prepare(SQL_PERIODO).bind(p.desde, p.hasta).all()).results), p.desde, p.hasta, hoy);
  if (url.searchParams.get("ant_desde")) {
    const a = pedirRango(url, "ant_desde", "ant_hasta");
    if (a.error) return json({ error: a.error }, 400);
    const ant = agregarPeriodo(leer((await env.DB.prepare(SQL_ANTERIOR).bind(a.desde, a.hasta).all()).results), a.desde, a.hasta, hoy);
    salida.anterior = { desde: a.desde, hasta: a.hasta, totales: ant.totales };
  }
  return json({ ...salida, ahora: Date.now() });
}
