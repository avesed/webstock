import { useSearchParams } from 'react-router-dom'
import { useCallback, useMemo } from 'react'

/**
 * Primary tab values for stock detail page
 */
export type PrimaryTab = 'traditional' | 'ai'

/**
 * Sub-tab values for Traditional tab
 */
export type TraditionalSubTab = 'financials' | 'news'

/**
 * Sub-tab values for AI tab
 */
export type AISubTab = 'analysis' | 'extension'

/**
 * Combined sub-tab type
 */
export type SubTab = TraditionalSubTab | AISubTab

/**
 * Valid primary tab values for validation
 */
const VALID_PRIMARY_TABS: PrimaryTab[] = ['traditional', 'ai']

/**
 * Valid sub-tab values grouped by primary tab
 */
const VALID_SUB_TABS: Record<PrimaryTab, SubTab[]> = {
  traditional: ['financials', 'news'],
  ai: ['analysis', 'extension'],
}

/**
 * Default sub-tab for each primary tab
 */
const DEFAULT_SUB_TABS: Record<PrimaryTab, SubTab> = {
  traditional: 'financials',
  ai: 'analysis',
}

/**
 * Tab navigation state
 */
interface TabNavigationState {
  primaryTab: PrimaryTab
  subTab: SubTab
}

/**
 * Tab navigation actions
 */
interface TabNavigationActions {
  setPrimaryTab: (tab: PrimaryTab) => void
  setSubTab: (tab: SubTab) => void
  setTabs: (primary: PrimaryTab, sub: SubTab) => void
}

/**
 * Validates if a string is a valid primary tab
 */
function isValidPrimaryTab(value: string | null): value is PrimaryTab {
  return value !== null && VALID_PRIMARY_TABS.includes(value as PrimaryTab)
}

/**
 * Validates if a string is a valid sub-tab for a given primary tab
 */
function isValidSubTab(primaryTab: PrimaryTab, value: string | null): value is SubTab {
  return value !== null && VALID_SUB_TABS[primaryTab].includes(value as SubTab)
}

/**
 * Hook for managing tab navigation state synchronized with URL search params.
 *
 * URL format: ?tab=ai&sub=chat
 *
 * Features:
 * - Validates URL parameters and falls back to defaults for invalid values
 * - Ensures sub-tab is valid for the selected primary tab
 * - Uses replace mode to avoid polluting browser history
 *
 * @example
 * ```tsx
 * function StockDetailPage() {
 *   const { primaryTab, subTab, setPrimaryTab, setSubTab } = useTabNavigation()
 *
 *   return (
 *     <Tabs value={primaryTab} onValueChange={setPrimaryTab}>
 *       ...
 *     </Tabs>
 *   )
 * }
 * ```
 */
export function useTabNavigation(): TabNavigationState & TabNavigationActions {
  const [searchParams, setSearchParams] = useSearchParams()

  const state = useMemo<TabNavigationState>(() => {
    const tabParam = searchParams.get('tab')
    const subParam = searchParams.get('sub')

    // Validate primary tab, default to 'traditional'
    const primaryTab: PrimaryTab = isValidPrimaryTab(tabParam) ? tabParam : 'traditional'

    // Validate sub-tab for the primary tab, default to the primary tab's default
    const subTab: SubTab = isValidSubTab(primaryTab, subParam)
      ? (subParam as SubTab)
      : DEFAULT_SUB_TABS[primaryTab]

    return { primaryTab, subTab }
  }, [searchParams])

  const setPrimaryTab = useCallback(
    (tab: PrimaryTab) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          next.set('tab', tab)
          // Reset sub-tab to default when switching primary tab
          next.set('sub', DEFAULT_SUB_TABS[tab])
          return next
        },
        { replace: true }
      )
    },
    [setSearchParams]
  )

  const setSubTab = useCallback(
    (sub: SubTab) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          next.set('sub', sub)
          return next
        },
        { replace: true }
      )
    },
    [setSearchParams]
  )

  const setTabs = useCallback(
    (primary: PrimaryTab, sub: SubTab) => {
      setSearchParams({ tab: primary, sub }, { replace: true })
    },
    [setSearchParams]
  )

  return {
    ...state,
    setPrimaryTab,
    setSubTab,
    setTabs,
  }
}
