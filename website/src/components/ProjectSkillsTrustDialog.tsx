import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Loader2, ShieldAlert } from 'lucide-react'

import { api } from '../api/client'
import { i18nT } from '../i18n/t'
import Modal from './Modal'
import { Btn } from './ui'

// — Consent gate for a project's own `.kiro/skills`.
//
// A SKILL.md is prose, not code, but it enters the agent's context and can
// instruct it to run anything, so loading one out of whatever repository the
// operator happens to have open is an execution-adjacent decision. The copy
// therefore names the CONSEQUENCE rather than explaining the mechanism, and
// both choices state what actually happens — a consent prompt whose decline
// path is unexplained trains reflexive approval.

interface Props {
  open: boolean
  /** Leaf token the operator was trying to use, e.g. "oncall-handover". */
  skillLeaf: string
  /** Real chat-slot key, so the server grants THIS chat's project. */
  slotKey?: string
  onClose: () => void
  /** Called with the leaf once the grant has landed. */
  onTrusted: (leaf: string) => void
}

export default function ProjectSkillsTrustDialog({
  open, skillLeaf, slotKey, onClose, onTrusted,
}: Props) {
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const queryClient = useQueryClient()

  const confirm = async () => {
    setPending(true)
    setError(null)
    try {
      await api.grantSkillTrust(slotKey)
      // The catalog's `trusted` flags are now stale — every ['skills', …] entry
      // must be refetched or the row the operator just unlocked still reads as
      // gated. Prefix match covers the per-slot keys.
      await queryClient.invalidateQueries({ queryKey: ['skills'] })
      onTrusted(skillLeaf)
    } catch (err: unknown) {
      // Duck-typed rather than `instanceof`: a partially-mocked api module
      // leaves the error class undefined, and two bundle realms give the same
      // class different identities.
      const body = (err as { body?: unknown })?.body
      let detail = ''
      if (typeof body === 'string') {
        try {
          detail = String((JSON.parse(body) as { error?: string }).error ?? '')
        } catch {
          detail = ''
        }
      } else if (body && typeof body === 'object') {
        detail = String((body as { error?: string }).error ?? '')
      }
      setError(detail || i18nT('components.projectSkillsTrust.grant_failed'))
    } finally {
      setPending(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      maxWidth={560}
      title={i18nT('components.projectSkillsTrust.title')}
      footer={
        <>
          <Btn onClick={onClose} disabled={pending}>
            {i18nT('components.projectSkillsTrust.decline')}
          </Btn>
          <Btn primary onClick={confirm} disabled={pending}>
            {pending
              ? <><Loader2 size={14} className="animate-spin" /> {i18nT('components.projectSkillsTrust.working')}</>
              : <><ShieldAlert size={14} /> {i18nT('components.projectSkillsTrust.confirm')}</>}
          </Btn>
        </>
      }
    >
      <div className="flex flex-col gap-3.5 text-[13px]">
        <p>
          {i18nT('components.projectSkillsTrust.body', { skill: skillLeaf })}
        </p>
        <p className="text-muted">
          {i18nT('components.projectSkillsTrust.consequence')}
        </p>
        <p className="text-muted">
          {i18nT('components.projectSkillsTrust.decline_consequence')}
        </p>
        <p className="text-muted">
          {i18nT('components.projectSkillsTrust.withdraw_hint')}
        </p>
        {error && (
          <p role="alert" className="text-warn">
            {error}
          </p>
        )}
      </div>
    </Modal>
  )
}
