import {
  Users,
  Settings,
  Activity,
  Filter,
  Rss,
  GitBranch,
  DollarSign,
  Database,
  TrendingUp,
  FlaskConical,
  Target,
  BarChart3,
  History,
  Brain,
  Play,
  Server,
  Cpu,
  Newspaper,
  ToggleLeft,
  Eye,
  type LucideIcon,
} from 'lucide-react'

export interface AdminRouteChild {
  path: string
  icon: LucideIcon
  labelKey: string
}

export interface AdminRouteItem {
  path: string
  icon: LucideIcon
  labelKey: string
  children?: AdminRouteChild[]
}

export interface AdminRouteGroup {
  groupKey: string
  items: AdminRouteItem[]
}

export const adminRouteGroups: AdminRouteGroup[] = [
  {
    groupKey: 'sidebar.general',
    items: [
      { path: 'users', icon: Users, labelKey: 'tabs.users' },
      {
        path: 'settings',
        icon: Settings,
        labelKey: 'tabs.modelSettings',
        children: [
          { path: 'providers', icon: Server, labelKey: 'tabs.settProviders' },
          { path: 'models', icon: Cpu, labelKey: 'tabs.settModels' },
          { path: 'news', icon: Newspaper, labelKey: 'tabs.settNews' },
        ],
      },
      { path: 'features', icon: ToggleLeft, labelKey: 'tabs.features' },
      {
        path: 'monitor',
        icon: Activity,
        labelKey: 'tabs.monitor',
        children: [
          { path: 'overview', icon: Eye, labelKey: 'tabs.monOverview' },
          { path: 'filter', icon: Filter, labelKey: 'tabs.monFilter' },
          { path: 'pipeline', icon: GitBranch, labelKey: 'tabs.monPipeline' },
        ],
      },
    ],
  },
  {
    groupKey: 'sidebar.content',
    items: [
      { path: 'rss', icon: Rss, labelKey: 'tabs.rss' },
    ],
  },
  {
    groupKey: 'sidebar.analytics',
    items: [
      { path: 'costs', icon: DollarSign, labelKey: 'tabs.costs' },
      { path: 'knowledge', icon: Database, labelKey: 'tabs.knowledge' },
      {
        path: 'predictions',
        icon: TrendingUp,
        labelKey: 'tabs.predictions',
        children: [
          { path: 'status', icon: Activity, labelKey: 'tabs.predStatus' },
          { path: 'results', icon: Target, labelKey: 'tabs.predResults' },
          { path: 'trigger', icon: Play, labelKey: 'tabs.predTrigger' },
          { path: 'performance', icon: BarChart3, labelKey: 'tabs.predPerformance' },
          { path: 'models', icon: History, labelKey: 'tabs.predModels' },
          { path: 'backtest', icon: FlaskConical, labelKey: 'tabs.predBacktest' },
          { path: 'rdagent', icon: Brain, labelKey: 'tabs.predRdagent' },
        ],
      },
      { path: 'backtests', icon: FlaskConical, labelKey: 'tabs.backtests' },
    ],
  },
]

/** Flat lookup: path segment -> labelKey (for breadcrumbs) */
export const adminPathLabels: Record<string, string> = Object.fromEntries(
  adminRouteGroups.flatMap((g) =>
    g.items.flatMap((i) => {
      const entries: [string, string][] = [[i.path, i.labelKey]]
      if (i.children) {
        for (const c of i.children) {
          entries.push([`${i.path}/${c.path}`, c.labelKey])
        }
      }
      return entries
    })
  )
)
