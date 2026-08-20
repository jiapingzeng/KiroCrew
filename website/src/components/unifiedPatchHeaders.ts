/**
 * Wraps a bare patch body in the `diff --git` / `---` / `+++` headers Pierre
 * needs to identify a file. The text is git's wire format, parsed by Pierre --
 * never read as words -- which is why it lives here rather than in the panel
 * that renders it (see this path in `eslint.i18n.config.js`).
 */
export function withUnifiedPatchHeaders(path: string, patch: string): string {
  return `diff --git a/${path} b/${path}\n--- a/${path}\n+++ b/${path}\n${patch}`
}

/** Placeholder path for a patch that arrived with no file section at all, so it
 *  can satisfy Pierre's named-header requirement. Never a real file, and never
 *  shown: surfaces that display Pierre's file header hide it when the patch
 *  named no file of its own. */
export const PATCH_SNIPPET_NAME = 'snippet'

/** The `---`/`+++` pair alone, for a caller assembling a patch line by line. */
export function snippetFileHeaderLines(): [string, string] {
  return [`--- a/${PATCH_SNIPPET_NAME}`, `+++ b/${PATCH_SNIPPET_NAME}`]
}
