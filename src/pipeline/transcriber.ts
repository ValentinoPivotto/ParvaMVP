// Transcripción de notas de voz.
// En el MVP el simulador manda texto, así que el mock devuelve el texto tal cual.
// La Cloud API entrega el AUDIO (no el texto), así que Parva tendría que
// transcribir del lado del back. El camino real todavía NO está cableado: iría
// por Amazon Transcribe, para no depender de un proveedor que no sea AWS.
// Mientras tanto, un audio devuelve un placeholder.

export interface AudioInput {
  texto?: string;        // en el simulador llega texto directo
  audioUrl?: string;     // en real: URL del media descargado de la Cloud API
}

export async function transcribe(input: AudioInput): Promise<string> {
  if (input.texto != null) return input.texto;

  // Sin transcripción cableada, un audio no se puede leer todavía.
  return '[audio sin transcribir]';
}
