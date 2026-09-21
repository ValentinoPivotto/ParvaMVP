// Transcripción de notas de voz.
// Un mensaje de texto pasa de largo. La Cloud API entrega el AUDIO (no el
// texto), así que Parva tendría que transcribir del lado del back: el camino
// real todavía NO está cableado, iría por Amazon Transcribe para no depender de
// un proveedor que no sea AWS. Mientras tanto, un audio devuelve un placeholder.

export interface AudioInput {
  texto?: string;        // mensaje de texto: ya viene listo
  audioUrl?: string;     // nota de voz: URL del media descargado de la Cloud API
}

export async function transcribe(input: AudioInput): Promise<string> {
  if (input.texto != null) return input.texto;

  // Sin transcripción cableada, un audio no se puede leer todavía.
  return '[audio sin transcribir]';
}
