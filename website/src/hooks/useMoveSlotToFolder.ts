import { useCallback } from 'react'
import { useMutation } from '@tanstack/react-query'
import { api } from '../api/client'
import { store, useAppDispatch } from '../store'
import { updateSlotFolder } from '../store/dashboardSlice'

/** Options for a single move. */
export type MoveSlotOptions = {
  /**
   * Make the write a compare-and-set against the folder the caller believes the
   * session is in RIGHT NOW (`null` = unfiled). The server refuses with 409
   * `folder_conflict` when it has since moved, and the conflict's authoritative
   * `folder_id` is applied to the store instead of the caller's value.
   *
   * Pass it for a REPLAYED decision — the sidebar's drag-move undo, which acts
   * on a move made seconds ago and must not overwrite a newer placement from
   * another client whose broadcast has not arrived yet. A live user choice
   * (a menu "Move to folder…") omits it and stays unconditional.
   */
  expectFolderId?: string | null
  /**
   * Called once the server has ACKNOWLEDGED this move.
   *
   * The optimistic write lands in the store immediately, which is not the same
   * fact: a caller that treats the store as proof (the drag-move undo bar) would
   * offer an undo whose compare-and-set the server must refuse, because the
   * original move is still in flight and the session is still at its old folder.
   * The original write would then land afterwards and silently reverse the undo.
   */
  onCommitted?: () => void
}

/** The 409 body this hook understands, as sent by `api_chat_slot_folder`. */
type FolderConflict = { code?: string; folder_id?: string }

/**
 * Read a folder-conflict payload out of a rejected request, or null.
 *
 * Duck-typed on `status` + `body` rather than `instanceof ApiError` on purpose:
 * the tests mock the whole api module, and a class identity check would depend
 * on the mock re-exporting that class — a coupling that fails as a TypeError
 * rather than as a missed branch.
 */
function folderConflict(err: unknown): FolderConflict | null {
  const e = err as { status?: number; body?: string } | null
  if (!e || e.status !== 409 || typeof e.body !== 'string') return null
  try {
    const body = JSON.parse(e.body) as FolderConflict
    return body?.code === 'folder_conflict' ? body : null
  } catch {
    return null
  }
}

/**
 * Single source of truth for moving a chat session into a folder (or to root).
 *
 * Both the sidebar row menus and the session-header dropdown — plus the
 * sidebar's drag-to-folder — assign a slot to a folder with the same optimistic
 * semantics: update Redux immediately (`onMutate`), fire `api.setSlotFolder`,
 * and roll back to the prior `folder_id` if the request fails (`onError`). This
 * hook collapses what were two near-identical implementations (`assignToFolder`
 * in ChatSidebar and `moveToFolder` in ChatHeaderMenu) into one, removing the
 * desync risk.
 *
 * Uses `useMutation` (the package's standard server-write pattern, cf.
 * `pinMutation` in useSessionActions) so it gets proper pending/error state.
 * The previous folder is read from the store at mutate time (not captured from
 * a passed-in slots array), so callers stay stateless — they just call
 * `move(slotKey, folderId)`.
 */
export function useMoveSlotToFolder(): (
  slotKey: string,
  folderId: string | null,
  opts?: MoveSlotOptions,
) => void {
  const dispatch = useAppDispatch()
  const { mutate } = useMutation({
    // Two-arg when unconditional, so the existing call contract (and every
    // assertion on it) is unchanged by the optional third parameter.
    mutationFn: ({ slotKey, folderId, expectFolderId }: { slotKey: string; folderId: string | null; expectFolderId?: string | null }) =>
      expectFolderId === undefined
        ? api.setSlotFolder(slotKey, folderId)
        : api.setSlotFolder(slotKey, folderId, expectFolderId),
    onMutate: ({ slotKey, folderId }) => {
      const prev = store.getState().dashboard.slots.find(s => s.key === slotKey)?.folder_id ?? ''
      const target = folderId || ''
      dispatch(updateSlotFolder({ key: slotKey, folderId: target }))
      return { slotKey, prev, target }
    },
    onError: (err, _vars, ctx) => {
      if (!ctx) return
      // Guarded rollback: only revert if a later move hasn't already changed
      // this slot's folder. Without this, a rapid move A→B where the A call
      // fails would clobber B's optimistic update even though B succeeded.
      const current = store.getState().dashboard.slots.find(s => s.key === ctx.slotKey)?.folder_id ?? ''
      if (current !== ctx.target) return
      // A refused compare-and-set is not a failed move — it means the session
      // is somewhere newer than this caller knew. Land on the server's
      // authoritative folder rather than the stale `prev`, so the sidebar shows
      // where the session actually is instead of a third, invented placement.
      const conflict = folderConflict(err)
      dispatch(updateSlotFolder({
        key: ctx.slotKey,
        folderId: conflict ? (conflict.folder_id ?? '') : ctx.prev,
      }))
    },
  })
  // `mutate` is referentially stable across renders, so the returned callback is too.
  return useCallback((slotKey: string, folderId: string | null, opts?: MoveSlotOptions) => {
    mutate({ slotKey, folderId, expectFolderId: opts?.expectFolderId }, {
      // Per-call callback, running in addition to the hook's own rollback
      // handler: the caller learns the server ACKNOWLEDGED the write without
      // taking over the rollback.
      onSuccess: () => opts?.onCommitted?.(),
    })
  }, [mutate])
}
