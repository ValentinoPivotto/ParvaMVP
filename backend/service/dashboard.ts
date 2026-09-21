// Estado completo del dashboard para un tenant.
import * as repo from '../repository/repo.ts';
import { margenPorLote } from './margin.ts';

export function buildState(productorId: number) {
  const productor = repo.getProductor(productorId);
  if (!productor) return null;
  return {
    productor,
    campos: repo.listCampos(productorId),
    lotes: repo.listLotes(productorId),
    campanias: repo.listCampanias(productorId),
    movimientos: repo.listMovimientos(productorId),
    hacienda: repo.listHacienda(productorId),
    sanidad: repo.listEventosSanitarios(productorId),
    margenes: margenPorLote(productorId),
    mensajes: repo.listRawMessages(productorId, 15),
  };
}
