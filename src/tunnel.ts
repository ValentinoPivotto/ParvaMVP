// Levanta el túnel público que Meta necesita para entregar los webhooks.
//
//   npm run tunnel
//
// Con NGROK_DOMAIN el dominio es FIJO: la callback URL se registra en Meta una
// sola vez y sobrevive a los restarts. Sin él, cada arranque da una URL nueva y
// hay que re-registrarla a mano — la trampa que deja al bot mudo sin un solo
// error visible, porque Meta sigue entregando los webhooks a la URL vieja.
import { spawn } from 'node:child_process';
import { config } from './config.ts';

// El puerto sale del .env porque este script corre bajo `node --env-file-if-exists`.
// Antes el script era shell puro (`${PORT:-3000}`) y leía el PORT del SHELL, que
// nunca ve el .env: si los dos diferían, el túnel apuntaba al puerto equivocado
// y no lo decía.
const destino = `http://localhost:${config.port}`;
const dominio = (process.env.NGROK_DOMAIN ?? '').trim().replace(/^https?:\/\//, '');

const args = ['http', destino];
if (dominio) args.push(`--url=https://${dominio}`);

console.log(`\n🔌 Túnel ngrok → ${destino}`);
if (dominio) {
  console.log(`   Callback en Meta: https://${dominio}/webhook/whatsapp`);
  console.log('   Dominio fijo: ya está registrada, no hay que re-pegarla.\n');
} else {
  console.log('   ⚠ Sin NGROK_DOMAIN la URL cambia en CADA restart y hay que volver');
  console.log('     a registrarla en Meta. Reservá un dominio gratis en');
  console.log('     https://dashboard.ngrok.com/domains y cargalo en .env.\n');
}

const p = spawn('ngrok', args, { stdio: 'inherit' });

p.on('error', (err: NodeJS.ErrnoException) => {
  if (err.code === 'ENOENT') {
    console.error('✗ ngrok no está instalado.  brew install ngrok');
    console.error('  Después: ngrok config add-authtoken <tu token>  (dashboard.ngrok.com)');
    process.exit(1);
  }
  console.error(`✗ no se pudo levantar ngrok: ${err.message}`);
  process.exit(1);
});

p.on('exit', (code) => process.exit(code ?? 0));
