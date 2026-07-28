// Configuración por entorno. Todo tiene default para correr local sin secrets.
import { fileURLToPath } from 'node:url';

const dbDefault = fileURLToPath(new URL('../data/parva.db', import.meta.url));

export const config = {
  port: Number(process.env.PORT ?? 3000),
  dbPath: process.env.DB_PATH ?? dbDefault,

  // IA: si hay key, se podría usar OpenAI (GPT-4o mini) real.
  // Por default (sin key) el parser es un MOCK determinístico en español.
  openaiApiKey: process.env.OPENAI_API_KEY ?? '',

  // Modo del parser: auto | mock | local | openai
  //  - auto  (default): openai si hay key, si no mock.
  //  - local: usa un modelo chico vía Ollama (server local en :11434).
  parserMode: process.env.PARSER_MODE ?? 'auto',
  localModel: process.env.LOCAL_MODEL ?? 'qwen2.5:3b',
  ollamaUrl: process.env.OLLAMA_URL ?? 'http://localhost:11434',

  // Umbral de confianza del parser para pedir confirmación antes de persistir.
  confidenceThreshold: Number(process.env.CONFIDENCE_THRESHOLD ?? 0.7),

  // WhatsApp: 'sim' (default) responde en el body del webhook — simulador web y
  // curl local. 'meta' habla con la Cloud API real: exige firma y envía saliente.
  whatsappMode: (process.env.WHATSAPP_MODE ?? 'sim') as 'sim' | 'meta',

  metaVerifyToken: process.env.META_VERIFY_TOKEN ?? 'parva-dev',
  metaAppSecret: process.env.META_APP_SECRET ?? '',
  metaAccessToken: process.env.META_ACCESS_TOKEN ?? '',
  metaPhoneNumberId: process.env.META_PHONE_NUMBER_ID ?? '',
  metaGraphVersion: process.env.META_GRAPH_VERSION ?? 'v25.0',

  // Responder a números no registrados. Default off: cuesta plata por
  // conversación y degrada el quality rating del número.
  metaReplyToUnknown: process.env.META_REPLY_TO_UNKNOWN === '1',
};

export function useRealAI(): boolean {
  return config.openaiApiKey.trim().length > 0;
}

export function modoMeta(): boolean {
  return config.whatsappMode === 'meta';
}

/** Variables que faltan para el modo meta ([] = listo para arrancar). */
export function faltaConfigMeta(): string[] {
  const faltan: string[] = [];
  if (!config.metaAccessToken.trim()) faltan.push('META_ACCESS_TOKEN');
  if (!config.metaPhoneNumberId.trim()) faltan.push('META_PHONE_NUMBER_ID');
  if (!config.metaAppSecret.trim()) faltan.push('META_APP_SECRET');
  return faltan;
}
