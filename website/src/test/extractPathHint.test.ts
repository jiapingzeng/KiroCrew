import { describe, it, expect } from 'vitest'
import { extractPathHintFromText } from '../components/MarkdownRenderer'

/** The hint labels a diff block and drives its Open button, so a false positive
 *  is not cosmetic: it titles the block with a file that does not exist and
 *  sends a probe after it. The text it reads is ordinary prose, where a
 *  slash-joined pair is far more often a grouping than a path. */
describe('extractPathHintFromText', () => {
  it('ignores a slash-joined grouping in the middle of a sentence', () => {
    // Observed live: this produced "/Beta/Prod", which titled a snippet block.
    expect(extractPathHintFromText('during DARU migration EU-ZAZ shares EU/Beta/Prod accounts')).toBeUndefined()
  })

  it('ignores other mid-prose slashes', () => {
    expect(extractPathHintFromText('it splits 1/2 of the load')).toBeUndefined()
    expect(extractPathHintFromText('choose split/unified from the menu')).toBeUndefined()
  })

  it('still reads a verb-introduced path', () => {
    expect(extractPathHintFromText('Created /home/me/Thing.java:')).toBe('/home/me/Thing.java')
    expect(extractPathHintFromText('Modified `/tmp/x.ts`')).toBe('/tmp/x.ts')
  })

  it('still reads a line that is nothing but a path', () => {
    expect(extractPathHintFromText('/abs/path/file.ts')).toBe('/abs/path/file.ts')
    expect(extractPathHintFromText('~/notes/todo.md')).toBe('~/notes/todo.md')
  })

  it('scans back past the blank line directly above a fence', () => {
    expect(extractPathHintFromText('Wrote /srv/app/main.py\n\n')).toBe('/srv/app/main.py')
  })
})
