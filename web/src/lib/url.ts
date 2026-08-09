/** Source URLs come from live search results, so they can be schemeless or
 *  malformed. `new URL()` throws on those, and thrown during render it takes
 *  the whole board down — hence the guard. */
export function safeHostname(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return url.replace(/^https?:\/\//, '').replace(/^www\./, '').split('/')[0] || url
  }
}
