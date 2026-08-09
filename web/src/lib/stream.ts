export async function streamSSE<E>(
  url: string,
  body: unknown,
  onEvent: (event: E) => void,
): Promise<void> {
  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!resp.ok || !resp.body) throw new Error(`API ${resp.status}`)
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    // Split on CRLF as well as LF: a proxy that rewrites line endings would
    // otherwise never produce a frame boundary and the run would hang.
    const chunks = buffer.split(/\r?\n\r?\n/)
    buffer = chunks.pop() ?? ''
    for (const chunk of chunks) {
      const match = /^data: ?(.*)$/s.exec(chunk.trim())
      if (!match) continue // ": keepalive" comment frames land here
      try {
        onEvent(JSON.parse(match[1]) as E)
      } catch {
        // One unparseable frame must not kill a run that is otherwise fine.
      }
    }
  }
}
