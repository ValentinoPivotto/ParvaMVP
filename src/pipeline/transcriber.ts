// Transcripción de notas de voz.
// En el MVP el simulador manda texto, así que el mock devuelve el texto tal cual.
// El camino real (Whisper / gpt-4o-mini-transcribe) queda cableado por env: como
// la Cloud API entrega el AUDIO (no el texto), Parva transcribe del lado del back.
import { useRealAI, config } from '../config.ts';

export interface AudioInput {
  texto?: string;        // en el simulador llega texto directo
  audioUrl?: string;     // en real: URL del media descargado de la Cloud API
}

export async function transcribe(input: AudioInput): Promise<string> {
  if (input.texto != null) return input.texto;

  if (input.audioUrl && useRealAI()) {
    // Camino real (no se ejercita en el MVP local sin key).
    const audio = await fetch(input.audioUrl).then((r) => r.arrayBuffer());
    const form = new FormData();
    form.append('model', 'gpt-4o-mini-transcribe');
    form.append('file', new Blob([audio], { type: 'audio/ogg' }), 'nota.ogg');
    const res = await fetch('https://api.openai.com/v1/audio/transcriptions', {
      method: 'POST',
      headers: { Authorization: `Bearer ${config.openaiApiKey}` },
      body: form,
    });
    const json = (await res.json()) as { text?: string };
    return json.text ?? '';
  }

  // Mock sin texto ni key: placeholder.
  return '[audio sin transcribir]';
}
