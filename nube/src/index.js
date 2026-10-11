// Reportes del Predio: recibe el resumen del día que sube la PC del local y lo muestra en sólo lectura.
// Claves: se guardan (como huella SHA-256) en la tabla config de la base D1; no hay secretos en el código.
import PAGINA from "./pagina.html";
import PAGINA_TURNOS from "./pagina_turnos.html";
import { manejarTurnos } from "./turnos.js";
import { manejarSync } from "./sync.js";
import { manejarReportes } from "./reportes.js";

const enc = new TextEncoder();
const sha = async (t) => [...new Uint8Array(await crypto.subtle.digest("SHA-256", enc.encode(t)))].map((b) => b.toString(16).padStart(2, "0")).join("");
const igual = (a, b) => { if (a.length !== b.length) return false; let r = 0; for (let i = 0; i < a.length; i++) r |= a.charCodeAt(i) ^ b.charCodeAt(i); return r === 0; };
const json = (o, s = 200, h = {}) => new Response(JSON.stringify(o), { status: s, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store", ...h } });
const DIA = /^\d{4}-\d{2}-\d{2}$/;
const SEG = { "x-content-type-options": "nosniff", "referrer-policy": "no-referrer", "x-frame-options": "DENY" };

async function claves(env) {
  const { results } = await env.DB.prepare("SELECT clave, valor FROM config").all();
  const c = {}; for (const r of results) c[r.clave] = r.valor; return c;
}
function cookie(req, nombre) { const m = (req.headers.get("cookie") || "").match(new RegExp("(?:^|; )" + nombre + "=([^;]+)")); return m ? m[1] : ""; }

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    const ruta = url.pathname;
    const cfg = await claves(env).catch(() => ({}));
    if (!cfg.hash_pc || !cfg.hash_ver) return new Response("Falta configurar las claves.", { status: 503 });

    // --- la PC sube el día ---
    if (ruta === "/api/subir" && req.method === "POST") {
      const t = (req.headers.get("authorization") || "").replace(/^Bearer\s+/i, "");
      if (!igual(await sha(t), cfg.hash_pc)) return json({ error: "clave incorrecta" }, 401);
      const texto = await req.text();
      if (texto.length > 1_800_000) return json({ error: "demasiado grande" }, 413);
      let p; try { p = JSON.parse(texto); } catch { return json({ error: "json inválido" }, 400); }
      if (!DIA.test(p.dia || "") || typeof p.resumen !== "object") return json({ error: "pedido inválido" }, 400);
      const b64 = typeof p.xlsx === "string" ? p.xlsx : null;          // el Excel se guarda en base64 (texto): D1 devuelve los binarios como listas de números
      await env.DB.prepare("INSERT INTO dias (dia, resumen, xlsx, subido) VALUES (?1, ?2, ?3, ?4) ON CONFLICT(dia) DO UPDATE SET resumen = ?2, xlsx = ?3, subido = ?4")
        .bind(p.dia, JSON.stringify(p.resumen), b64, Date.now()).run();
      return json({ ok: true });
    }

    // --- la PC sincroniza los turnos ---
    if (ruta === "/api/sync") {
      const t = (req.headers.get("authorization") || "").replace(/^Bearer\s+/i, "");
      if (!igual(await sha(t), cfg.hash_pc)) return json({ error: "clave incorrecta" }, 401);
      try { return await manejarSync(req, env, url); } catch (e) { return json({ error: "error del servidor: " + (e && e.message) }, 500); }
    }

    // --- ver (sólo lectura): clave de acceso ---
    const sesion = await sha(cfg.hash_ver + "|sesion");
    if (ruta === "/entrar" && req.method === "POST") {
      const f = await req.formData();
      if (igual(await sha(String(f.get("clave") || "").trim()), cfg.hash_ver))
        return new Response(null, { status: 303, headers: { location: String(f.get("volver")) === "/turnos" ? "/turnos" : "/", "set-cookie": `s=${sesion}; Path=/; Max-Age=2592000; HttpOnly; Secure; SameSite=Strict` } });
      return new Response(null, { status: 303, headers: { location: (String(f.get("volver")) === "/turnos" ? "/turnos" : "/") + "?mal=1" } });
    }
    if (ruta === "/salir") return new Response(null, { status: 303, headers: { location: url.searchParams.get("volver") === "/turnos" ? "/turnos" : "/", "set-cookie": "s=; Path=/; Max-Age=0" } });
    const dentro = igual(cookie(req, "s"), sesion);
    // períodos (semana, mes, fechas) para la web y lista de días que tiene la nube para la PC
    const rep = await manejarReportes(req, env, ruta, url, { dentro, pc: async () => igual(await sha((req.headers.get("authorization") || "").replace(/^Bearer\s+/i, "")), cfg.hash_pc) });
    if (rep) return rep;
    if (ruta === "/" || ruta === "/index.html" || ruta === "/turnos") {
      const mal = url.searchParams.get("mal") ? "1" : "0";
      return new Response((ruta === "/turnos" ? PAGINA_TURNOS : PAGINA).replace("__DENTRO__", dentro ? "1" : "0").replace("__MAL__", mal), { headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store", ...SEG, "content-security-policy": "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src 'self' data:; form-action 'self'" } });
    }
    if (!dentro) return json({ error: "no autorizado" }, 401);

    if (ruta.startsWith("/api/turnos")) return (await manejarTurnos(req, env, ruta, url)) || json({ error: "no existe" }, 404);
    if (ruta === "/api/dias") {
      const { results } = await env.DB.prepare("SELECT dia, subido, json_extract(resumen,'$.kpis.total_vendido') AS total FROM dias ORDER BY dia DESC LIMIT 400").all();
      return json({ dias: results });
    }
    let m;
    if ((m = ruta.match(/^\/api\/dia\/(\d{4}-\d{2}-\d{2})$/))) {
      const r = await env.DB.prepare("SELECT resumen, subido FROM dias WHERE dia = ?").bind(m[1]).first();
      if (!r) return json({ error: "sin datos" }, 404);
      return json({ resumen: JSON.parse(r.resumen), subido: r.subido, ahora: Date.now() });
    }
    if ((m = ruta.match(/^\/excel\/(\d{4}-\d{2}-\d{2})$/))) {
      const r = await env.DB.prepare("SELECT xlsx FROM dias WHERE dia = ?").bind(m[1]).first();
      if (!r || !r.xlsx) return new Response("No hay Excel de ese día", { status: 404 });
      return new Response(Uint8Array.from(atob(r.xlsx), (c) => c.charCodeAt(0)), { headers: { "content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "content-disposition": `attachment; filename="${m[1]}_resumen.xlsx"`, "cache-control": "no-store" } });
    }
    return json({ error: "no existe" }, 404);
  },
};
