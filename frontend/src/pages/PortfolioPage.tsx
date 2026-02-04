import { PortfolioPanel } from '@/components/portfolio'

export default function PortfolioPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Portfolio</h1>
        <p className="text-muted-foreground">
          Manage your investment portfolio and track performance
        </p>
      </div>

      <PortfolioPanel />
    </div>
  )
}
