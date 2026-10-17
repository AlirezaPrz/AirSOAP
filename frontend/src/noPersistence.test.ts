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
]

describe('no persistence or logging of encounter data', () => {
  it('scans the application sources', () => {
    expect(Object.keys(sources).length).toBeGreaterThan(10)
  })

  it.each(Object.entries(sources))('%s uses no storage, cookie, beacon, or console APIs', (_path, source) => {
    for (const pattern of FORBIDDEN) expect(source).not.toMatch(pattern)
  })
})
