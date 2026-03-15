import { create } from 'zustand'
import { persist } from 'zustand/middleware'

interface AdminState {
  /** Active standard backtest task ID (survives refresh) */
  activeBacktestTaskId: string | null
  /** Market of the active backtest */
  activeBacktestMarket: string | null
  /** Active ML Agent backtest ID (survives refresh) */
  activeAgentTaskId: string | null
}

interface AdminActions {
  setActiveBacktestTask: (taskId: string | null, market?: string | null) => void
  setActiveAgentTask: (taskId: string | null) => void
  clearAllTasks: () => void
}

type AdminStore = AdminState & AdminActions

export const useAdminStore = create<AdminStore>()(
  persist(
    (set) => ({
      activeBacktestTaskId: null,
      activeBacktestMarket: null,
      activeAgentTaskId: null,

      setActiveBacktestTask: (taskId, market = null) =>
        set({ activeBacktestTaskId: taskId, activeBacktestMarket: market }),

      setActiveAgentTask: (taskId) =>
        set({ activeAgentTaskId: taskId }),

      clearAllTasks: () =>
        set({
          activeBacktestTaskId: null,
          activeBacktestMarket: null,
          activeAgentTaskId: null,
        }),
    }),
    {
      name: 'webstock-admin-tasks',
      partialize: (state) => ({
        activeBacktestTaskId: state.activeBacktestTaskId,
        activeBacktestMarket: state.activeBacktestMarket,
        activeAgentTaskId: state.activeAgentTaskId,
      }),
    }
  )
)
