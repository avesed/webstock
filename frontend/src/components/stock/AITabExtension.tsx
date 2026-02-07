import { useTranslation } from 'react-i18next'
import { Sparkles } from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

/**
 * AI Tab extension placeholder component.
 * Shows a "coming soon" message for future AI features.
 */
export function AITabExtension() {
  const { t } = useTranslation('dashboard')

  return (
    <Card className="min-h-[400px] flex flex-col items-center justify-center">
      <CardHeader className="text-center">
        <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-primary/10">
          <Sparkles className="h-8 w-8 text-primary" />
        </div>
        <CardTitle>{t('stock.tabs.extension', 'Extensions')}</CardTitle>
        <CardDescription className="max-w-md">
          {t(
            'stock.extensionComingSoon',
            'Advanced AI features coming soon. Stay tuned for portfolio optimization, risk analysis, and more.'
          )}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="rounded-lg border border-dashed border-muted-foreground/25 bg-muted/50 px-6 py-3 text-sm text-muted-foreground">
          {t('common:status.comingSoon', 'Coming Soon')}
        </div>
      </CardContent>
    </Card>
  )
}
