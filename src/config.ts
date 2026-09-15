// Configuración por entorno. Todo tiene default para correr local sin secrets.
import { fileURLToPath } from 'node:url';

const dbDefault = fileURLToPath(new URL('../data/parva.db', import.meta.url));

export const config = {
  port: Number(process.env.PORT ?? 3000),
  dbPath: process.env.DB_PATH ?? dbDefault,

  // IA: si hay key, se podría usar OpenAI (GPT-4o mini) real.
  // Por default (sin key) el parser es un MOCK determinístico en español.
  openaiApiKey: process.env.OPENAI_API_KEY ?? '',

  // Modo del parser: auto | mock | local | openai | bedrock
  //  - auto (default): bedrock si hay credenciales AWS → openai si hay key →
  //    modelo local si Ollama responde → mock.
  //  - local: usa un modelo chico vía Ollama (server local en :11434).
  parserMode: process.env.PARSER_MODE ?? 'auto',
  localModel: process.env.LOCAL_MODEL ?? 'qwen2.5:3b',
  ollamaUrl: process.env.OLLAMA_URL ?? 'http://localhost:11434',

  // Bedrock (Nova Lite). Las credenciales del SSO son temporales y vencen; se
  // exportan al entorno con `aws configure export-credentials` (ver README).
  // Región us-east-2 por default: el rol del curso tiene deny explícito fuera
  // de ahí, y los modelos que exigen perfil de inferencia entre regiones rutean
  // a us-west-2 y fallan con AccessDenied.
  awsRegion: process.env.AWS_REGION ?? 'us-east-2',
  awsAccessKeyId: process.env.AWS_ACCESS_KEY_ID ?? '',
  awsSecretAccessKey: process.env.AWS_SECRET_ACCESS_KEY ?? '',
  awsSessionToken: process.env.AWS_SESSION_TOKEN ?? '',
  bedrockModelId: process.env.BEDROCK_MODEL_ID ?? 'amazon.nova-lite-v1:0',

  // Umbral de confianza del parser para pedir confirmación antes de persistir.
  confidenceThreshold: Number(process.env.CONFIDENCE_THRESHOLD ?? 0.7),

  // WhatsApp: 'sim' (default) responde en el body del webhook — simulador web y
  // curl local. 'meta' habla con la Cloud API real: exige firma y envía saliente.
  whatsappMode: (process.env.WHATSAPP_MODE ?? 'sim') as 'sim' | 'meta',

  metaVerifyToken: process.env.META_VERIFY_TOKEN ?? 'parva-dev',
  metaAppId: process.env.META_APP_ID ?? '',
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

/** ¿Hay credenciales de AWS en el entorno para firmarle a Bedrock? */
export function useBedrock(): boolean {
  return config.awsAccessKeyId.trim().length > 0 && config.awsSecretAccessKey.trim().length > 0;
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
