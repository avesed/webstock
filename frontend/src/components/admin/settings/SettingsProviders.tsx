import { useCallback } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'

import { LlmProviders } from '../LlmProviders'
import { adminApi } from '@/api/admin'

export default function SettingsProviders() {
  const queryClient = useQueryClient()

  const { data: providers = [], isLoading } = useQuery({
    queryKey: ['admin-llm-providers'],
    queryFn: adminApi.listLlmProviders,
  })

  const handleRefresh = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['admin-llm-providers'] })
  }, [queryClient])

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return <LlmProviders providers={providers} onRefresh={handleRefresh} />
}
