"""Vincula un teléfono real a un usuario existente, para probar el bot desde el
celular.

  python3 -m backend.scripts.link_phone --list
  python3 -m backend.scripts.link_phone 1 +5491155551234

Es un script aparte y no una edición del seed porque seed() solo corre con la
base vacía o con --reset, y --reset BORRA los datos (incluido este vínculo).
"""
import sys

from ..jscompat import bindeable, numero
from ..phone import normalize_telefono, phone_variants
from ..repository import db

USO = 'Uso: python3 -m backend.scripts.link_phone --list | python3 -m backend.scripts.link_phone <usuarioId> <+telefono>'


def listar() -> None:
    filas = db.all("""
    SELECT u.id, u.nombre, u.rol, u.telefono, p.nombre AS productor
    FROM usuario u JOIN productor p ON p.id = u.productor_id ORDER BY u.id
  """)
    print('\n id · nombre           · rol          · teléfono        · productor')
    print(' ' + '─' * 74)
    for f in filas:
        print(f' {str(f["id"]).ljust(2)} · {f["nombre"].ljust(16)} · {f["rol"].ljust(12)} · '
              f'{f["telefono"].ljust(15)} · {f["productor"]}')
    print('\n Uso: python3 -m backend.scripts.link_phone <usuarioId> <+telefono>\n')


def vincular(id: object, telefono_raw: str) -> None:
    actual = db.get('SELECT id, nombre, telefono FROM usuario WHERE id = ?', id)
    if not actual:
        print(f'✗ No existe el usuario con id {id}. Corré --list para ver los disponibles.', file=sys.stderr)
        sys.exit(1)

    nuevo = normalize_telefono(telefono_raw)
    if not nuevo or len(nuevo) < 8:
        print(f'✗ Teléfono inválido: "{telefono_raw}". Usá formato internacional, ej. +5491155551234',
              file=sys.stderr)
        sys.exit(1)

    ocupado = db.get('SELECT id, nombre FROM usuario WHERE telefono = ? AND id != ?', nuevo, id)
    if ocupado:
        print(f'✗ El teléfono {nuevo} ya lo tiene {ocupado["nombre"]} (id {ocupado["id"]}).', file=sys.stderr)
        sys.exit(1)

    db.run('UPDATE usuario SET telefono = ? WHERE id = ?', nuevo, id)

    print(f'\n✓ {actual["nombre"]} (id {id})')
    print(f'   antes:   {actual["telefono"]}')
    print(f'   ahora:   {nuevo}')
    print('\n   Formas que van a matchear cuando escriba por WhatsApp:')
    for v in phone_variants(nuevo):
        print(f'     · {v}')
    print('')


def main() -> None:
    db.init_schema()

    args = sys.argv[1:]
    if len(args) == 0 or args[0] in ('--list', '-l'):
        listar()
    elif len(args) == 2:
        # `Number(args[0])`: un id no numérico queda en NaN y no matchea ninguna
        # fila, que es lo que hace caer en el "No existe el usuario".
        vincular(bindeable(numero(args[0])), args[1])
    else:
        print(USO, file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
