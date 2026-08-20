/**
 * Shared file-path context menu: Open in default app, Reveal in Finder / file
 * manager, Copy path. Exposed as a right-click wrapper (`FilePathMenu`); the
 * item rows themselves are a private building block (`FilePathMenuItems`).
 *
 * The two OTHER file-location surfaces (MarkdownPanel's overflow and
 * FileViewer's overflow) are custom plain-button dropdowns, not Radix
 * ContextMenus, so they cannot host Radix `ContextMenuItem`s. They dedupe by
 * reusing the shared `revealOrOpen` failure path and `useRevealLabel` platform
 * label instead of the row component — so `FilePathMenuItems` has no consumer
 * beyond this file and is deliberately NOT exported (a zero-consumer export is
 * dead surface area).
 *
 * Open/Reveal items render only when `directLocal` is true (the backend reports
 * the request comes from a browser on the same machine). Remote and tunneled
 * sessions see Copy path only, because opening Finder on a host the user is not
 * looking at is useless.
 *
 * The reveal label is platform-aware — it reuses the same gatewayPlatform-driven
 * wording MarkdownPanel's overflow menu uses, so the two menus name the identical
 * action identically ("Open in Finder" on macOS, "Open in File Explorer" on
 * Windows, "Show in file manager" otherwise) instead of drifting apart.
 *
 * "Open with default app" is hidden for directories: `/api/reveal` rejects an
 * `open` action on a directory (400), so offering it would be a guaranteed-fail
 * click. Reveal still applies — it shows the folder in the file manager.
 */
import { type ReactNode } from 'react'
import { ExternalLink, FolderOpen, Copy } from 'lucide-react'
import {
  ContextMenu,
  ContextMenuTrigger,
  ContextMenuContent,
  ContextMenuItem,
} from './ui/context-menu'
import { useBranding } from '../hooks/useBranding'
import { useGatewayPlatform } from '../hooks/useGatewayPlatform'
import { api } from '../api/client'
import { copyToClipboard } from '../utils/clipboard'
import { i18nT } from '../i18n/t'

/** What the wrapped path is on disk. Directories cannot be "opened". */
export type FilePathKind = 'file' | 'dir'

/**
 * Perform a reveal/open and, on failure, show the SHARED i18n failure message.
 *
 * Exported so every file-location surface (this menu, MarkdownPanel's overflow,
 * FileViewer's overflow) funnels its reveal through one failure path instead of
 * each re-deriving its own `alert(err.message)` — a raw server string leaks
 * internal wording and drifts per surface.
 */
export async function revealOrOpen(filePath: string, action: 'open' | 'reveal' = 'reveal') {
  try {
    await api.revealPath(filePath, action)
  } catch (err) {
    // eslint-disable-next-line no-console -- surface reveal failures for diagnostics
    console.error('revealPath failed', err)
    alert(i18nT('components.filePathMenu.reveal_failed'))
  }
}

/**
 * The platform-aware reveal label ("Open in Finder" / "Open in File Explorer" /
 * "Show in file manager"), read from the GATEWAY's platform.
 *
 * The single owner of this wording: MarkdownPanel's overflow and FileViewer's
 * overflow both call this instead of re-deriving the same three-arm ternary, so
 * every file-location surface names the identical action identically. `/api/reveal`
 * shells out on the gateway, so the gateway's platform is the one to name.
 */
export function useRevealLabel(): string {
  const gatewayPlatform = useGatewayPlatform()
  return gatewayPlatform === 'darwin'
    ? i18nT('components.markdownPanel.open_in_finder')
    : gatewayPlatform === 'windows'
      ? i18nT('components.markdownPanel.open_in_file_explorer')
      : i18nT('components.markdownPanel.show_in_file_manager')
}

// ── Menu-item building blocks ────────────────────────────────────────────────

interface FilePathMenuItemsProps {
  /** Absolute file path to act on. */
  filePath: string
  /** Whether the path is a file or a directory. The Open item is suppressed for
   *  directories, which the reveal endpoint cannot `open`. */
  kind?: FilePathKind
}

/**
 * Renders the file-path action items (Open / Reveal / Copy path) as
 * ContextMenu items. Drop these into any ContextMenuContent.
 */
function FilePathMenuItems({ filePath, kind }: FilePathMenuItemsProps) {
  const branding = useBranding()
  const isLocal = branding.directLocal
  // Shared owner of the platform-aware reveal label (see useRevealLabel) — the
  // same wording MarkdownPanel's overflow and FileViewer's overflow use.
  const revealLabel = useRevealLabel()
  const openLabel = i18nT('components.markdownPanel.open_with_default_app')

  return (
    <>
      {isLocal && kind !== 'dir' && (
        <ContextMenuItem
          onSelect={() => { void revealOrOpen(filePath, 'open') }}
          aria-label={openLabel}
        >
          <ExternalLink size={14} className="lucide-inline" />
          {openLabel}
        </ContextMenuItem>
      )}
      {isLocal && (
        <ContextMenuItem
          onSelect={() => { void revealOrOpen(filePath, 'reveal') }}
          aria-label={revealLabel}
        >
          <FolderOpen size={14} className="lucide-inline" />
          {revealLabel}
        </ContextMenuItem>
      )}
      <ContextMenuItem
        onSelect={() => { copyToClipboard(filePath) }}
        aria-label={i18nT('components.filePathMenu.copy_path')}
      >
        <Copy size={14} className="lucide-inline" />
        {i18nT('components.filePathMenu.copy_path')}
      </ContextMenuItem>
    </>
  )
}

// ── Right-click wrapper ──────────────────────────────────────────────────────

export interface FilePathMenuProps {
  /** Absolute file path to act on. */
  filePath: string
  /** The element that triggers the context menu on right-click. */
  children: ReactNode
  /** File or directory — directories hide the Open item (see FilePathMenuItems). */
  kind?: FilePathKind
}

/**
 * Wrap any element to give it a right-click menu with file-path actions.
 *
 * ```tsx
 * <FilePathMenu filePath="/home/user/report.md">
 *   <span className="file-title">report.md</span>
 * </FilePathMenu>
 * ```
 */
export default function FilePathMenu({ filePath, children, kind }: FilePathMenuProps) {
  return (
    <ContextMenu>
      <ContextMenuTrigger asChild>
        {children}
      </ContextMenuTrigger>
      <ContextMenuContent className="min-w-[180px]" onClick={e => e.stopPropagation()}>
        <FilePathMenuItems
          filePath={filePath}
          kind={kind}
        />
      </ContextMenuContent>
    </ContextMenu>
  )
}
