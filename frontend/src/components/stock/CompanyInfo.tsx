import { Building2, Globe, Users, Calendar, ExternalLink } from 'lucide-react'
import { Separator } from '@/components/ui/separator'
import { formatCompactNumber } from '@/lib/utils'
import type { StockInfo } from '@/types'

interface CompanyInfoProps {
  info: StockInfo
}

/**
 * Company info component for displaying company details.
 * Shows description, sector, website, employees, and other company information.
 */
export function CompanyInfo({ info }: CompanyInfoProps) {
  return (
    <div className="space-y-4">
      {info.description && (
        <p className="text-sm text-muted-foreground line-clamp-4">{info.description}</p>
      )}

      <Separator />

      <div className="space-y-3">
        {info.sector && (
          <div className="flex items-center gap-2">
            <Building2 className="h-4 w-4 text-muted-foreground" />
            <span className="text-sm">
              {info.sector}
              {info.industry && ` - ${info.industry}`}
            </span>
          </div>
        )}

        {info.website && (
          <div className="flex items-center gap-2">
            <Globe className="h-4 w-4 text-muted-foreground" />
            <a
              href={info.website}
              target="_blank"
              rel="noopener noreferrer"
              className="text-sm text-primary hover:underline flex items-center gap-1"
            >
              {new URL(info.website).hostname}
              <ExternalLink className="h-3 w-3" />
            </a>
          </div>
        )}

        {info.employees && (
          <div className="flex items-center gap-2">
            <Users className="h-4 w-4 text-muted-foreground" />
            <span className="text-sm">{formatCompactNumber(info.employees)} employees</span>
          </div>
        )}

        {info.founded && (
          <div className="flex items-center gap-2">
            <Calendar className="h-4 w-4 text-muted-foreground" />
            <span className="text-sm">Founded {info.founded}</span>
          </div>
        )}

        {info.headquarters && (
          <p className="text-sm text-muted-foreground">{info.headquarters}</p>
        )}

        {info.ceo && (
          <p className="text-sm">
            <span className="text-muted-foreground">CEO:</span> {info.ceo}
          </p>
        )}
      </div>
    </div>
  )
}
