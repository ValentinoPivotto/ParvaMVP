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
};

export function useRealAI(): boolean {
  return config.openaiApiKey.trim().length > 0;
}
