interface CommodityInfoProps {
  symbol: string
}

/**
 * Commodity info component for precious metals.
 * Shows type, exchange, unit, and contract size information.
 */
export function CommodityInfo({ symbol }: CommodityInfoProps) {
  const upperSym = symbol.toUpperCase()

  // Determine exchange and contract size based on symbol
  const getExchange = (): string => {
    if (upperSym.includes('GC') || upperSym.includes('SI')) {
      return 'COMEX'
    }
    return 'NYMEX'
  }

  const getContractSize = (): string => {
    if (upperSym.includes('GC')) return '100 troy oz (Gold)'
    if (upperSym.includes('SI')) return '5,000 troy oz (Silver)'
    if (upperSym.includes('PL')) return '50 troy oz (Platinum)'
    if (upperSym.includes('PA')) return '100 troy oz (Palladium)'
    return 'N/A'
  }

  return (
    <div className="space-y-2 text-sm">
      <div>
        <span className="text-muted-foreground">Type: </span>
        <span>Precious Metal Futures</span>
      </div>
      <div>
        <span className="text-muted-foreground">Exchange: </span>
        <span>{getExchange()}</span>
      </div>
      <div>
        <span className="text-muted-foreground">Unit: </span>
        <span>USD per troy ounce</span>
      </div>
      <div>
        <span className="text-muted-foreground">Contract Size: </span>
        <span>{getContractSize()}</span>
      </div>
    </div>
  )
}
