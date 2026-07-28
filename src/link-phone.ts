// Vincula un teléfono real a un usuario existente, para probar el bot desde el
// celular.
//
//   npm run link-phone -- --list
//   npm run link-phone -- 1 +5491155551234
//
// Es un script aparte y no una edición del seed porque seed() solo corre con la
// base vacía o con --reset, y --reset BORRA los datos (incluido este vínculo).
import { initSchema, db } from './db.ts';
import { normalizeTelefono, phoneVariants } from './phone.ts';

function listar(): void {
  const filas = db.prepare(`
    SELECT u.id, u.nombre, u.rol, u.telefono, p.nombre AS productor
    FROM usuario u JOIN productor p ON p.id = u.productor_id ORDER BY u.id
  `).all() as any[];
  console.log('\n id · nombre           · rol          · teléfono        · productor');
  console.log(' ' + '─'.repeat(74));
  for (const f of filas) {
    console.log(` ${String(f.id).padEnd(2)} · ${f.nombre.padEnd(16)} · ${f.rol.padEnd(12)} · ${f.telefono.padEnd(15)} · ${f.productor}`);
  }
  console.log('\n Uso: npm run link-phone -- <usuarioId> <+telefono>\n');
}

function vincular(id: number, telefonoRaw: string): void {
  const actual = db.prepare('SELECT id, nombre, telefono FROM usuario WHERE id = ?').get(id) as any;
  if (!actual) {
    console.error(`✗ No existe el usuario con id ${id}. Corré --list para ver los disponibles.`);
    process.exit(1);
  }

  const nuevo = normalizeTelefono(telefonoRaw);
  if (!nuevo || nuevo.length < 8) {
    console.error(`✗ Teléfono inválido: "${telefonoRaw}". Usá formato internacional, ej. +5491155551234`);
    process.exit(1);
  }

  const ocupado = db.prepare('SELECT id, nombre FROM usuario WHERE telefono = ? AND id != ?').get(nuevo, id) as any;
  if (ocupado) {
    console.error(`✗ El teléfono ${nuevo} ya lo tiene ${ocupado.nombre} (id ${ocupado.id}).`);
    process.exit(1);
  }

  db.prepare('UPDATE usuario SET telefono = ? WHERE id = ?').run(nuevo, id);

  console.log(`\n✓ ${actual.nombre} (id ${id})`);
  console.log(`   antes:   ${actual.telefono}`);
  console.log(`   ahora:   ${nuevo}`);
  console.log(`\n   Formas que van a matchear cuando escriba por WhatsApp:`);
  for (const v of phoneVariants(nuevo)) console.log(`     · ${v}`);
  console.log('');
}

initSchema();

const args = process.argv.slice(2);
if (args.length === 0 || args[0] === '--list' || args[0] === '-l') {
  listar();
} else if (args.length === 2) {
  vincular(Number(args[0]), args[1]);
} else {
  console.error('Uso: npm run link-phone -- --list | npm run link-phone -- <usuarioId> <+telefono>');
  process.exit(1);
}
