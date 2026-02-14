import { useTranslation } from 'react-i18next'
import { ExternalLink, FileText } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

interface DetailedSummaryDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  detailedSummary: string
  originalUrl: string
}

export default function DetailedSummaryDialog({
  open,
  onOpenChange,
  title,
  detailedSummary,
  originalUrl,
}: DetailedSummaryDialogProps) {
  const { t } = useTranslation('dashboard')

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl h-[85vh] flex flex-col p-0">
        <DialogHeader className="px-6 pt-6 pb-4 shrink-0">
          <DialogTitle className="flex items-center gap-2">
            <FileText className="h-5 w-5 flex-shrink-0" />
            <span className="line-clamp-2">{t('news.detailedSummary', 'Detailed Summary')}</span>
          </DialogTitle>
          <DialogDescription className="line-clamp-2">
            {title}
          </DialogDescription>
        </DialogHeader>

        <div className="flex-1 overflow-y-auto px-6">
          <div className="prose prose-sm dark:prose-invert max-w-none pb-4">
            <p className="whitespace-pre-wrap leading-relaxed">{detailedSummary}</p>
          </div>
        </div>

        <div className="flex justify-end px-6 py-4 border-t shrink-0">
          <Button
            variant="outline"
            size="sm"
            onClick={() => window.open(originalUrl, '_blank', 'noopener,noreferrer')}
          >
            <ExternalLink className="mr-2 h-4 w-4" />
            {t('news.viewOriginal', 'View Original')}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
