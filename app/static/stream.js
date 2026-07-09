// Parses a POST /brief/stream response into {event, data} objects.
//
// Deliberately not EventSource — EventSource is GET-only, and the backend
// requires POST (the protocol text is the request body). Reads the response
// body as a stream and buffers until a full "event: X\ndata: Y\n\n" frame is
// available, exactly matching UI_Design_Spec.md §14.3's wire format.
export async function* parseSSEStream(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let boundary;
    while ((boundary = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      if (!frame.trim()) continue;

      const lines = frame.split("\n");
      const eventLine = lines.find((l) => l.startsWith("event: "));
      const dataLine = lines.find((l) => l.startsWith("data: "));
      if (!eventLine || !dataLine) continue; // malformed frame — skip rather than crash the reader

      yield { event: eventLine.slice("event: ".length), data: JSON.parse(dataLine.slice("data: ".length)) };
    }
  }
}
