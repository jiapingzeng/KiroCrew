import { readFileSync } from 'node:fs'

/**
 * Read a source file as text for a test that asserts on its SHAPE, with line
 * endings normalized to LF.
 *
 * The normalization is the whole point. A Windows checkout materializes these
 * files with CRLF endings (git's `core.autocrlf`), which puts a `\r` before
 * every line break — so `/…\)$/` cannot match, `.split('\n')` leaves a trailing
 * `\r` on each element, and a `toContain('a\n  b')` over two lines fails. Those
 * assertions then fail for every Windows contributor while passing on CI's Linux
 * runner, which is the worst shape a gate can have: it reads as "your change
 * broke this" to the one person who cannot reproduce the green run.
 *
 * Deliberately NOT in `helpers.tsx`: that module pulls React, RTL, the Redux
 * store and a QueryClient, none of which a source-text assertion needs, and
 * every import of it is paid per fork.
 *
 * No DOM, no path resolution — pass whatever path the caller already uses.
 */
export function readSource(path: string): string {
  return readFileSync(path, 'utf-8').replace(/\r\n/g, '\n')
}
