import { PortfolioPanel } from '@/components/portfolio'
import PortfolioOptimizer from '@/components/qlib/PortfolioOptimizer'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useTranslation } from 'react-i18next'

export default function PortfolioPage() {
  const { t } = useTranslation('common')
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">{t('qlib.portfolio')}</h1>
        <p className="text-muted-foreground">{t('qlib.portfolioDescription')}</p>
      </div>
      <Tabs defaultValue="holdings">
        <TabsList>
          <TabsTrigger value="holdings">{t('qlib.holdings')}</TabsTrigger>
          <TabsTrigger value="optimizer">{t('qlib.optimizer')}</TabsTrigger>
        </TabsList>
        <TabsContent value="holdings">
          <PortfolioPanel />
        </TabsContent>
        <TabsContent value="optimizer">
          <PortfolioOptimizer />
        </TabsContent>
      </Tabs>
    </div>
  )
}
