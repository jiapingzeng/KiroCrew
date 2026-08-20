import { createContext, useContext, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

interface Branding { botName: string; avatar: string; directLocal: boolean }

const defaults: Branding = { botName: 'Kiro Crew', avatar: '/logo.png', directLocal: false }
const BrandingContext = createContext<Branding>(defaults)

export function BrandingProvider({ children }: { children: ReactNode }) {
  // Runs on the app-wide QueryClient (both production mounts sit inside it).
  // Its ambient default (retry: 3) is what keeps a transient startup fetch
  // failure from leaving directLocal permanently false — local file actions
  // reappear once a retry succeeds, without a page reload (the old one-shot
  // useEffect .catch(() => {}) stayed false forever).
  const { data } = useQuery({
    queryKey: ['branding'],
    queryFn: () => api.branding(),
    staleTime: Infinity,
  })
  const b: Branding = data
    ? { botName: data.bot_name || defaults.botName, avatar: data.avatar || defaults.avatar, directLocal: !!data.direct_local }
    : defaults
  return <BrandingContext.Provider value={b}>{children}</BrandingContext.Provider>
}

export const useBranding = () => useContext(BrandingContext)
