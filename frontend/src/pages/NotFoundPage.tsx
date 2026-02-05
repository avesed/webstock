import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { Home, ArrowLeft } from 'lucide-react'

export default function NotFoundPage() {
  const { t } = useTranslation('common')

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-background p-4">
      <div className="text-center">
        <h1 className="text-9xl font-bold text-primary/20">404</h1>
        <h2 className="mt-4 text-2xl font-bold">{t('errors.notFound')}</h2>
        <p className="mt-2 text-muted-foreground">
          {t('empty.description')}
        </p>
        <div className="mt-6 flex items-center justify-center gap-4">
          <Button variant="outline" onClick={() => window.history.back()}>
            <ArrowLeft className="mr-2 h-4 w-4" />
            {t('actions.back')}
          </Button>
          <Button asChild>
            <Link to="/">
              <Home className="mr-2 h-4 w-4" />
              {t('navigation.home')}
            </Link>
          </Button>
        </div>
      </div>
    </div>
  )
}
