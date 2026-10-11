-- Migraciones de la base D1 de la nube. NO se ejecutan solas: se pegan a mano en la consola de Cloudflare (D1 > predio-reportes > Console)
-- sólo si hace falta. Cada bloque dice para qué sirve y se puede correr una sola vez.

-- ---------------------------------------------------------------------------------------------------------------------------------
-- 1) Horarios y nombres de las canchas como en la planilla de papel
--
-- Fútbol: turnos de 1 hora, de 14:00 a 23:00 (el último empieza a las 22:00 y termina 23:00).
-- Pádel: turnos de 90 minutos, 10:00, 11:30, 13:00, 14:30, 16:00, 17:30, 19:00, 20:30 y 22:00 (el último termina 23:30).
-- No se tocan los precios.
--
-- Normalmente NO hace falta correrlo: el programa de la PC manda las canchas a la nube cada vez que cambian (nube/src/sync.js las actualiza)
-- y, al abrirse el programa actualizado, la PC pone estos mismos horarios y nombres (y amplía el rango si ya había turnos fuera de él).
-- Este bloque es el equivalente por si se quiere dejar la nube al día antes de que se abra el programa.
-- Las claves (id) no cambian: la nube y el programa las usan para reconocer cada cancha.
UPDATE canchas SET nombre = 'Fútbol 5 · Cancha 1',         dur = 60, ap = 840, ci = 1380 WHERE id = 'f5a';
UPDATE canchas SET nombre = 'Fútbol 5 · Cancha 2',         dur = 60, ap = 840, ci = 1380 WHERE id = 'f5b';
UPDATE canchas SET nombre = 'Fútbol 7 · Cancha completa',  dur = 60, ap = 840, ci = 1380 WHERE id = 'f7';
UPDATE canchas SET nombre = 'Pádel · Cancha 1',            dur = 90, ap = 600, ci = 1410 WHERE id = 'padelT';
UPDATE canchas SET nombre = 'Pádel · Cancha 2',            dur = 90, ap = 600, ci = 1410 WHERE id = 'padelA';

-- Para revisar si había turnos o turnos fijos cargados fuera de esos horarios (quedarían fuera de la grilla de la web):
--   SELECT t.id, t.cancha_id, t.fecha, t.ini, t.fin, t.cliente FROM turnos t JOIN canchas c ON c.id = t.cancha_id
--    WHERE t.estado != 'cancelado' AND (t.ini < c.ap OR t.fin > c.ci);
--   SELECT f.id, f.cancha_id, f.dow, f.ini, f.cliente FROM fijos f JOIN canchas c ON c.id = f.cancha_id
--    WHERE f.ini < c.ap OR f.ini + c.dur > c.ci;
-- Si aparece alguno, el programa de la PC ya amplió el rango de esa cancha (se ve en el calendario del programa) y lo manda a la nube solo.
