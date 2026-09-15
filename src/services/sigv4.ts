// Firma SigV4 para AWS, sin dependencias.
//
// Bedrock no acepta una API key pelada en un header como OpenAI: cada request
// va firmado con las credenciales (temporales, en el caso del SSO del curso).
// Meter el SDK de AWS solo para esto traería medio árbol de dependencias y
// rompería la premisa del repo, así que el algoritmo va acá. Está fijado por
// AWS y no tiene variantes: es código que se escribe una vez y no se toca.
import { createHash, createHmac } from 'node:crypto';

const ALGO = 'AWS4-HMAC-SHA256';

export interface AwsCreds {
  accessKeyId: string;
  secretAccessKey: string;
  /** Presente solo en credenciales temporales (SSO/STS). */
  sessionToken?: string;
}

export interface RequestFirmado {
  url: string;
  headers: Record<string, string>;
  body: string;
}

function sha256Hex(data: string): string {
  return createHash('sha256').update(data, 'utf8').digest('hex');
}

function hmac(key: Buffer | string, data: string): Buffer {
  return createHmac('sha256', key).update(data, 'utf8').digest();
}

// Clave derivada en cadena: secreto → fecha → región → servicio → aws4_request.
function claveDeFirma(secret: string, fecha: string, region: string, servicio: string): Buffer {
  const kDate = hmac('AWS4' + secret, fecha);
  const kRegion = hmac(kDate, region);
  const kService = hmac(kRegion, servicio);
  return hmac(kService, 'aws4_request');
}

// RFC 3986 reserva estos cuatro, que encodeURIComponent deja pasar.
function encodeRfc3986(s: string): string {
  return s.replace(/[!'()*]/g, (c) => '%' + c.charCodeAt(0).toString(16).toUpperCase());
}

/**
 * Path canónico: el que se firma NO es el que se envía.
 *
 * Para todo servicio que no sea S3, AWS percent-encodea de nuevo un path que ya
 * viene encodeado. Un id de modelo como `amazon.nova-lite-v1:0` viaja en la URL
 * como `...v1%3A0` y se firma como `...v1%253A0`. Verificado contra el
 * canonical request que imprime botocore con `--debug`.
 */
function pathCanonico(pathEnviado: string): string {
  return encodeRfc3986(encodeURIComponent(pathEnviado)).replace(/%2F/g, '/');
}

/**
 * Firma un POST y devuelve la URL, los headers y el body listos para `fetch`.
 *
 * `path` es el path tal como se envía, ya percent-encodeado por quien llama
 * (los ids de modelo de Bedrock traen `:`). La doble codificación que exige la
 * firma se aplica acá adentro.
 */
export function firmarAws(opts: {
  method: string;
  host: string;
  path: string;
  region: string;
  service: string;
  body: string;
  creds: AwsCreds;
  contentType?: string;
  ahora?: Date;
}): RequestFirmado {
  const { method, host, path, region, service, body, creds } = opts;
  const now = opts.ahora ?? new Date();

  // YYYYMMDDTHHMMSSZ
  const amzDate = now.toISOString().replace(/[:-]|\.\d{3}/g, '');
  const fecha = amzDate.slice(0, 8);

  // `host` se firma pero no se manda: lo pone el runtime desde la URL, y
  // mandarlo a mano lo duplicaría.
  const firmados: Record<string, string> = {
    'content-type': opts.contentType ?? 'application/json',
    host,
    'x-amz-date': amzDate,
  };
  if (creds.sessionToken) firmados['x-amz-security-token'] = creds.sessionToken;

  const nombres = Object.keys(firmados).sort();
  const canonicalHeaders = nombres.map((n) => `${n}:${firmados[n].trim()}\n`).join('');
  const signedHeaders = nombres.join(';');

  // method / uri / query / headers / signedHeaders / hash(payload).
  // El join intercala el \n que separa el bloque de headers (que ya termina
  // en \n) de la lista de signedHeaders.
  const canonicalRequest = [method, pathCanonico(path), '', canonicalHeaders, signedHeaders, sha256Hex(body)].join('\n');

  const scope = `${fecha}/${region}/${service}/aws4_request`;
  const stringToSign = [ALGO, amzDate, scope, sha256Hex(canonicalRequest)].join('\n');
  const firma = hmac(claveDeFirma(creds.secretAccessKey, fecha, region, service), stringToSign).toString('hex');

  const { host: _omitido, ...headers } = firmados;
  return {
    url: `https://${host}${path}`,
    headers: {
      ...headers,
      Authorization: `${ALGO} Credential=${creds.accessKeyId}/${scope}, SignedHeaders=${signedHeaders}, Signature=${firma}`,
    },
    body,
  };
}
