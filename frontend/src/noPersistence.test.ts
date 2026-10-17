import { describe, expect, it } from 'vitest'

// Every non-test source file, as raw text.
const sources = import.meta.glob<string>(['./**/*.{ts,tsx}', '!./**/*.test.{ts,tsx}', '!./test/**'], {
  query: '?raw',
  import: 'default',
  eager: true,
})

const FORBIDDEN = [
  /\blocalStorage\b/,
  /\bsessionStorage\b/,
  /\bindexedDB\b/,
  /\bdocument\.cookie\b/,
  /\bconsole\.\w+\(/,
  /\bnavigator\.sendBeacon\b/,
  // Credentials never travel in a URL (they would land in history and server logs).
  /[?&](password|token|session)=/i,
]

describe('no persistence or logging of encounter data or credentials', () => {
  it('scans the application sources', () => {
    expect(Object.keys(sources).length).toBeGreaterThan(10)
  })

  it.each(Object.entries(sources))('%s uses no storage, cookie, beacon or console APIs and puts no secret in a URL', (_path, source) => {
    for (const pattern of FORBIDDEN) expect(source).not.toMatch(pattern)
  })
})
