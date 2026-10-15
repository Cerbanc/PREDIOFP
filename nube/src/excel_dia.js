// El Excel del día que se baja de la web: una sola hoja, simple y completa (como el resumen del día). Se arma al descargar, con lo último que subió la PC.
import { construirXlsx } from "./xlsx.js";

const ZONA = "America/Argentina/Buenos_Aires";
const hora = (ms) => new Date(ms).toLocaleTimeString("es-AR", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: ZONA });
const DIAS = ["domingo", "lunes", "martes", "miércoles", "jueves", "viernes", "sábado"];
const fechaLarga = (dia) => { const d = new Date(dia + "T12:00:00Z"); return `${DIAS[d.getUTCDay()]} ${String(d.getUTCDate()).padStart(2, "0")}/${String(d.getUTCMonth() + 1).padStart(2, "0")}/${d.getUTCFullYear()}`; };
const n = (v) => Math.round(Number(v) || 0);
const $ = (v) => ({ v: n(v), e: "$" });
const $b = (v) => ({ v: n(v), e: "$b" });
const H = (...c) => c.map((x) => ({ v: x, e: "h" }));

export function excelDelDia(dia, resumen, subido) {
  const k = resumen.kpis || {}, f = resumen.fiado || {}, cajas = resumen.cajas || [];
  const abierta = cajas.some((c) => !c.cerrada);
  const filas = [];
  filas.push([{ v: `${resumen.negocio || "Predio"} · Resumen del ${fechaLarga(dia)}`, e: "t" }]);
  filas.push([{ v: `Actualizado a las ${hora(subido)}`, e: "n" }]);
  if (abierta) filas.push([{ v: "La caja sigue abierta: estos números pueden cambiar hasta el cierre.", e: "aviso" }, { e: "aviso", v: "" }, { e: "aviso", v: "" }, { e: "aviso", v: "" }]);
  filas.push([]);

  filas.push(H("Ventas del día", ""));
  filas.push(["Total vendido", $b(k.total_vendido)]);
  filas.push(["Tickets", n(k.tickets)]);
  if (n(k.anulados)) filas.push(["Tickets anulados", n(k.anulados)]);
  filas.push(["Ticket promedio", $(k.ticket_promedio)]);
  filas.push(["Descuentos", $(k.descuentos)]);
  filas.push(["Cobrado neto (lo que entró)", $b(k.cobrado_neto)]);
  filas.push([]);

  const aVentas = n(k.total_vendido) - n(f.cargos), otros = n(k.cobrado_neto) - (aVentas + n(f.pagos));
  filas.push(H("De lo vendido a lo cobrado", ""));
  filas.push(["Total vendido", $(k.total_vendido)]);
  filas.push(["− Quedó en fiado", $(-n(f.cargos))]);
  filas.push(["+ Deudas de fiado que pagaron hoy", $(f.pagos)]);
  filas.push(["± Señas de turnos, correcciones y otros", $(otros)]);
  filas.push([{ v: "Cobrado neto", e: "b" }, $b(k.cobrado_neto)]);
  filas.push([]);

  filas.push(H("Cierres de caja", "Debía haber", "Contado", "Diferencia"));
  if (!cajas.length) filas.push(["No se abrió caja"]);
  for (const c of cajas) {
    const resp = c.responsable || "Caja";
    if (!c.cerrada) { filas.push([`${resp} (caja abierta)`]); continue; }
    filas.push([resp, $(c.esperado), $(c.contado), c.diferencia ? { v: n(c.diferencia), e: "$b" } : { v: "cuadra", e: "" }]);
    for (const g of c.digital || []) {
      filas.push([`   ${g.nombre}`, $(g.esperado), g.informado == null ? "sin controlar" : $(g.informado), g.informado == null ? "" : g.diferencia ? { v: n(g.diferencia), e: "$b" } : { v: "cuadra", e: "" }]);
    }
    if (c.nota) filas.push([{ v: `   Nota: ${c.nota}`, e: "n" }]);
  }
  filas.push([]);

  if ((resumen.por_medio || []).length) {
    filas.push(H("Por medio de pago", "Cobros", "Devoluciones", "Neto"));
    for (const m of resumen.por_medio) filas.push([m.medio, $(m.cobros), $(m.devoluciones), $b(m.neto)]);
    filas.push([]);
  }
  if ((resumen.por_categoria || []).length) {
    filas.push(H("Por categoría", "Unidades", "Importe"));
    for (const c of resumen.por_categoria) filas.push([c.categoria, n(c.unidades), $(c.importe)]);
    filas.push([]);
  }
  if ((resumen.por_destino || []).length) {
    filas.push(H("Por destino", "Tickets", "Total"));
    for (const d of resumen.por_destino) filas.push([d.destino, n(d.tickets), $(d.total)]);
    filas.push([]);
  }
  if ((resumen.top_productos || []).length) {
    filas.push(H("Lo más vendido", "Unidades", "Importe"));
    for (const p of resumen.top_productos) filas.push([p.producto, n(p.unidades), $(p.importe)]);
    filas.push([]);
  }
  if (n(f.cargos) || n(f.pagos)) {
    filas.push(H("Fiado", ""));
    filas.push(["Quedó a cuenta hoy", $(f.cargos)]);
    filas.push(["Pagaron de deudas", $(f.pagos)]);
    filas.push([]);
  }
  const NOMBRE = { gasto: "Gastos varios", proveedor: "Pagos a proveedores", retiro: "Retiros a caja mayor", ingreso: "Ingresos", perdida: "Pérdidas" };
  const mc = Object.entries(resumen.movimientos_caja || {}).filter(([, v]) => n(v));
  if (mc.length) {
    filas.push(H("Movimientos de caja", ""));
    for (const [t, v] of mc) filas.push([NOMBRE[t] || t, $(v)]);
    filas.push([]);
  }
  if ((resumen.stock_bajo || []).length) {
    filas.push(H("Stock para reponer", "Hay", "Mínimo"));
    for (const p of resumen.stock_bajo) filas.push([p.producto, n(p.stock), n(p.minimo)]);
  }
  return construirXlsx(filas, [44, 16, 16, 16], "Resumen del día");
}
