import { useTranslation } from 'react-i18next'
import { Users, Settings, Activity, Filter, GitBranch } from 'lucide-react'

import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { UserManagement, SystemSettings, SystemMonitor, FilterStats, PipelineTrace } from '@/components/admin'

export default function AdminDashboardPage() {
  const { t } = useTranslation('admin')

  return (
    <div className="flex flex-col h-[calc(100vh-10rem)]">
      <div className="flex-shrink-0 pb-4">
        <h1 className="text-3xl font-bold tracking-tight">{t('title')}</h1>
        <p className="text-muted-foreground">{t('subtitle')}</p>
      </div>

      <Tabs defaultValue="users" className="flex flex-col flex-1 min-h-0">
        <TabsList className="flex-shrink-0">
          <TabsTrigger value="users" className="flex items-center gap-2">
            <Users className="h-4 w-4" />
            {t('tabs.users')}
          </TabsTrigger>
          <TabsTrigger value="settings" className="flex items-center gap-2">
            <Settings className="h-4 w-4" />
            {t('tabs.settings')}
          </TabsTrigger>
          <TabsTrigger value="monitor" className="flex items-center gap-2">
            <Activity className="h-4 w-4" />
            {t('tabs.monitor')}
          </TabsTrigger>
          <TabsTrigger value="filter" className="flex items-center gap-2">
            <Filter className="h-4 w-4" />
            {t('tabs.filter')}
          </TabsTrigger>
          <TabsTrigger value="pipeline" className="flex items-center gap-2">
            <GitBranch className="h-4 w-4" />
            {t('tabs.pipeline')}
          </TabsTrigger>
        </TabsList>

        <TabsContent value="users" className="flex-1 overflow-y-auto mt-4 pr-2">
          <UserManagement />
        </TabsContent>

        <TabsContent value="settings" className="flex-1 overflow-y-auto mt-4 pr-2">
          <SystemSettings />
        </TabsContent>

        <TabsContent value="monitor" className="flex-1 overflow-y-auto mt-4 pr-2">
          <SystemMonitor />
        </TabsContent>

        <TabsContent value="filter" className="flex-1 overflow-y-auto mt-4 pr-2">
          <FilterStats />
        </TabsContent>

        <TabsContent value="pipeline" className="flex-1 overflow-y-auto mt-4 pr-2">
          <PipelineTrace />
        </TabsContent>
      </Tabs>
    </div>
  )
}
