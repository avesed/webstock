import { useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { cn } from '@/lib/utils'
import NewsFeed from './NewsFeed'

interface NewsPageContentProps {
  className?: string
}

const VALID_TABS = ['feed', 'market'] as const
type NewsTab = (typeof VALID_TABS)[number]

export default function NewsPageContent({ className }: NewsPageContentProps) {
  const { t } = useTranslation('dashboard')
  const [searchParams, setSearchParams] = useSearchParams()

  const rawTab = searchParams.get('tab')
  const activeTab: NewsTab = VALID_TABS.includes(rawTab as NewsTab) ? (rawTab as NewsTab) : 'feed'

  const handleTabChange = (value: string) => {
    setSearchParams(value === 'feed' ? {} : { tab: value }, { replace: true })
  }

  return (
    <div className={cn('space-y-4', className)}>
      <Tabs value={activeTab} onValueChange={handleTabChange}>
        {/* Header row: title + tabs inline */}
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <h1 className="text-2xl font-bold tracking-tight">{t('news.title')}</h1>
          <TabsList>
            <TabsTrigger value="feed">{t('news.myFeed')}</TabsTrigger>
            <TabsTrigger value="market">{t('news.market')}</TabsTrigger>
          </TabsList>
        </div>

        {/* Feed content — full width, no card wrapper */}
        <TabsContent value="feed" className="mt-4">
          <NewsFeed mode="feed" />
        </TabsContent>

        <TabsContent value="market" className="mt-4">
          <NewsFeed mode="market" />
        </TabsContent>
      </Tabs>
    </div>
  )
}
