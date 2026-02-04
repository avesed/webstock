import { useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Plus,
  MoreVertical,
  Trash2,
  Edit2,
  Loader2,
  TrendingUp,
  TrendingDown,
  AlertCircle,
  Briefcase,
  DollarSign,
  ChevronRight,
  ArrowUpRight,
  ArrowDownRight,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { cn } from '@/lib/utils'
import { formatCurrency, formatPercent, formatDate, getPriceChangeColor } from '@/lib/utils'
import { portfolioApi, stockApi } from '@/api'
import { useToast } from '@/hooks'
import type { Portfolio, Transaction, Holding, StockQuote } from '@/types'

interface PortfolioPanelProps {
  className?: string
}

interface HoldingWithQuote extends Holding {
  quote?: StockQuote
}

interface PortfolioWithQuotes extends Portfolio {
  holdingsWithQuotes: HoldingWithQuote[]
}

export default function PortfolioPanel({ className }: PortfolioPanelProps) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { toast } = useToast()

  const [selectedPortfolioId, setSelectedPortfolioId] = useState<string | null>(null)
  const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false)
  const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false)
  const [isTransactionDialogOpen, setIsTransactionDialogOpen] = useState(false)
  const [newPortfolioName, setNewPortfolioName] = useState('')
  const [newPortfolioDescription, setNewPortfolioDescription] = useState('')
  const [portfolioToDelete, setPortfolioToDelete] = useState<Portfolio | null>(null)
  const [transactionForm, setTransactionForm] = useState({
    symbol: '',
    type: 'BUY' as 'BUY' | 'SELL',
    quantity: '',
    price: '',
    fee: '0',
    date: new Date().toISOString().split('T')[0] ?? '',
    notes: '',
  })

  // Fetch portfolios
  const {
    data: portfoliosData,
    isLoading: isLoadingPortfolios,
    error: portfoliosError,
  } = useQuery({
    queryKey: ['portfolios'],
    queryFn: portfolioApi.getAll,
  })

  // Ensure portfolios is always an array
  const portfolios = Array.isArray(portfoliosData) ? portfoliosData : []

  // Fetch selected portfolio with quotes
  const {
    data: portfolioWithQuotes,
    isLoading: isLoadingQuotes,
  } = useQuery({
    queryKey: ['portfolio-quotes', selectedPortfolioId],
    queryFn: async (): Promise<PortfolioWithQuotes | null> => {
      if (!selectedPortfolioId || !portfolios) return null

      const portfolio = portfolios.find((p) => p.id === selectedPortfolioId)
      if (!portfolio) return null

      const holdingsWithQuotes: HoldingWithQuote[] = await Promise.all(
        portfolio.holdings.map(async (holding) => {
          try {
            const quote = await stockApi.getQuote(holding.symbol)
            const currentValue = quote.price * holding.quantity
            const gain = currentValue - holding.averageCost * holding.quantity
            const gainPercent = ((currentValue - holding.averageCost * holding.quantity) / (holding.averageCost * holding.quantity)) * 100

            return {
              ...holding,
              quote,
              currentPrice: quote.price,
              currentValue,
              gain,
              gainPercent,
            }
          } catch {
            return holding
          }
        })
      )

      return { ...portfolio, holdingsWithQuotes }
    },
    enabled: !!selectedPortfolioId && !!portfolios,
    refetchInterval: 30000,
  })

  // Fetch transactions
  const {
    data: transactionsData,
    isLoading: isLoadingTransactions,
  } = useQuery({
    queryKey: ['portfolio-transactions', selectedPortfolioId],
    queryFn: async () => {
      if (!selectedPortfolioId) return null
      return portfolioApi.getTransactions(selectedPortfolioId, 1, 50)
    },
    enabled: !!selectedPortfolioId,
  })

  // Create portfolio mutation
  const createMutation = useMutation({
    mutationFn: ({ name, description }: { name: string; description?: string }) =>
      portfolioApi.create(name, description),
    onSuccess: (newPortfolio) => {
      queryClient.invalidateQueries({ queryKey: ['portfolios'] })
      setSelectedPortfolioId(newPortfolio.id)
      setIsCreateDialogOpen(false)
      setNewPortfolioName('')
      setNewPortfolioDescription('')
      toast({
        title: 'Portfolio created',
        description: `"${newPortfolio.name}" has been created successfully.`,
      })
    },
    onError: () => {
      toast({
        title: 'Error',
        description: 'Failed to create portfolio. Please try again.',
        variant: 'destructive',
      })
    },
  })

  // Delete portfolio mutation
  const deleteMutation = useMutation({
    mutationFn: (id: string) => portfolioApi.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['portfolios'] })
      if (selectedPortfolioId === portfolioToDelete?.id) {
        setSelectedPortfolioId(null)
      }
      setIsDeleteDialogOpen(false)
      setPortfolioToDelete(null)
      toast({
        title: 'Portfolio deleted',
        description: 'The portfolio has been deleted successfully.',
      })
    },
    onError: () => {
      toast({
        title: 'Error',
        description: 'Failed to delete portfolio. Please try again.',
        variant: 'destructive',
      })
    },
  })

  // Add transaction mutation
  const addTransactionMutation = useMutation({
    mutationFn: (data: { portfolioId: string; transaction: Omit<Transaction, 'id' | 'portfolioId' | 'createdAt'> }) =>
      portfolioApi.addTransaction(data.portfolioId, data.transaction),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['portfolios'] })
      queryClient.invalidateQueries({ queryKey: ['portfolio-quotes', selectedPortfolioId] })
      queryClient.invalidateQueries({ queryKey: ['portfolio-transactions', selectedPortfolioId] })
      setIsTransactionDialogOpen(false)
      resetTransactionForm()
      toast({
        title: 'Transaction recorded',
        description: 'The transaction has been added successfully.',
      })
    },
    onError: () => {
      toast({
        title: 'Error',
        description: 'Failed to record transaction. Please try again.',
        variant: 'destructive',
      })
    },
  })

  const resetTransactionForm = useCallback(() => {
    setTransactionForm({
      symbol: '',
      type: 'BUY',
      quantity: '',
      price: '',
      fee: '0',
      date: new Date().toISOString().split('T')[0] ?? '',
      notes: '',
    })
  }, [])

  const handleCreatePortfolio = useCallback(() => {
    if (newPortfolioName.trim()) {
      const desc = newPortfolioDescription.trim()
      createMutation.mutate({
        name: newPortfolioName.trim(),
        ...(desc ? { description: desc } : {}),
      })
    }
  }, [newPortfolioName, newPortfolioDescription, createMutation])

  const handleDeletePortfolio = useCallback((portfolio: Portfolio) => {
    setPortfolioToDelete(portfolio)
    setIsDeleteDialogOpen(true)
  }, [])

  const handleConfirmDelete = useCallback(() => {
    if (portfolioToDelete) {
      deleteMutation.mutate(portfolioToDelete.id)
    }
  }, [portfolioToDelete, deleteMutation])

  const handleSubmitTransaction = useCallback(() => {
    if (!selectedPortfolioId) return

    const quantity = parseFloat(transactionForm.quantity)
    const price = parseFloat(transactionForm.price)
    const fee = parseFloat(transactionForm.fee) || 0

    if (!transactionForm.symbol || isNaN(quantity) || isNaN(price)) {
      toast({
        title: 'Validation error',
        description: 'Please fill in all required fields.',
        variant: 'destructive',
      })
      return
    }

    const notes = transactionForm.notes.trim()
    addTransactionMutation.mutate({
      portfolioId: selectedPortfolioId,
      transaction: {
        symbol: transactionForm.symbol.toUpperCase(),
        type: transactionForm.type,
        quantity,
        price,
        fee,
        date: transactionForm.date,
        ...(notes ? { notes } : {}),
      },
    })
  }, [selectedPortfolioId, transactionForm, addTransactionMutation, toast])

  const handleStockClick = useCallback((symbol: string) => {
    navigate(`/stock/${symbol}`)
  }, [navigate])

  // Auto-select first portfolio
  if (portfolios && portfolios.length > 0 && !selectedPortfolioId) {
    setSelectedPortfolioId(portfolios[0]?.id ?? null)
  }

  // Calculate summary stats
  const selectedPortfolio = Array.isArray(portfolios) ? portfolios.find((p) => p.id === selectedPortfolioId) : undefined
  const totalValue = portfolioWithQuotes?.holdingsWithQuotes.reduce(
    (sum, h) => sum + (h.currentValue ?? 0),
    0
  ) ?? selectedPortfolio?.totalValue ?? 0
  const totalCost = portfolioWithQuotes?.holdingsWithQuotes.reduce(
    (sum, h) => sum + h.averageCost * h.quantity,
    0
  ) ?? selectedPortfolio?.totalCost ?? 0
  const totalGain = totalValue - totalCost
  const totalGainPercent = totalCost > 0 ? (totalGain / totalCost) * 100 : 0

  if (isLoadingPortfolios) {
    return (
      <div className={cn('flex items-center justify-center p-8', className)}>
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (portfoliosError) {
    return (
      <div className={cn('flex flex-col items-center justify-center gap-2 p-8', className)}>
        <AlertCircle className="h-8 w-8 text-destructive" />
        <p className="text-sm text-muted-foreground">Failed to load portfolios</p>
        <Button
          variant="outline"
          size="sm"
          onClick={() => queryClient.invalidateQueries({ queryKey: ['portfolios'] })}
        >
          Try again
        </Button>
      </div>
    )
  }

  return (
    <div className={cn('space-y-6', className)}>
      {/* Portfolio selector and actions */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" className="min-w-[200px] justify-between">
                <div className="flex items-center gap-2">
                  <Briefcase className="h-4 w-4" />
                  <span className="truncate">
                    {selectedPortfolio?.name ?? 'Select portfolio'}
                  </span>
                </div>
                <ChevronRight className="h-4 w-4 rotate-90 opacity-50" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" className="w-56">
              {portfolios?.map((portfolio) => (
                <DropdownMenuItem
                  key={portfolio.id}
                  onClick={() => setSelectedPortfolioId(portfolio.id)}
                  className={cn(
                    'flex items-center justify-between',
                    selectedPortfolioId === portfolio.id && 'bg-accent'
                  )}
                >
                  <span className="truncate">{portfolio.name}</span>
                  <span className="text-xs text-muted-foreground">
                    {portfolio.holdings?.length ?? 0} holdings
                  </span>
                </DropdownMenuItem>
              ))}
              {portfolios && portfolios.length > 0 && <DropdownMenuSeparator />}
              <DropdownMenuItem onClick={() => setIsCreateDialogOpen(true)}>
                <Plus className="mr-2 h-4 w-4" />
                Create new portfolio
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>

          {selectedPortfolio && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon">
                  <MoreVertical className="h-4 w-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem onClick={() => setIsCreateDialogOpen(true)}>
                  <Edit2 className="mr-2 h-4 w-4" />
                  Rename
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  onClick={() => handleDeletePortfolio(selectedPortfolio)}
                  className="text-destructive"
                >
                  <Trash2 className="mr-2 h-4 w-4" />
                  Delete
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          )}
        </div>

        <Button onClick={() => setIsTransactionDialogOpen(true)} disabled={!selectedPortfolioId}>
          <Plus className="mr-2 h-4 w-4" />
          Add Transaction
        </Button>
      </div>

      {/* Summary cards */}
      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Total Value</CardTitle>
            <DollarSign className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{formatCurrency(totalValue)}</div>
            <p className="text-xs text-muted-foreground">
              Current portfolio value
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Total Cost</CardTitle>
            <Briefcase className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{formatCurrency(totalCost)}</div>
            <p className="text-xs text-muted-foreground">
              Total invested
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Total Gain/Loss</CardTitle>
            {totalGain >= 0 ? (
              <TrendingUp className="h-4 w-4 text-stock-up" />
            ) : (
              <TrendingDown className="h-4 w-4 text-stock-down" />
            )}
          </CardHeader>
          <CardContent>
            <div className={cn('text-2xl font-bold', getPriceChangeColor(totalGain))}>
              {formatCurrency(totalGain)}
            </div>
            <p className={cn('text-xs', getPriceChangeColor(totalGainPercent))}>
              {formatPercent(totalGainPercent)}
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Holdings and transactions tabs */}
      <Tabs defaultValue="holdings" className="space-y-4">
        <TabsList>
          <TabsTrigger value="holdings">Holdings</TabsTrigger>
          <TabsTrigger value="transactions">Transactions</TabsTrigger>
        </TabsList>

        <TabsContent value="holdings">
          <Card>
            <CardHeader>
              <CardTitle>Your Holdings</CardTitle>
              <CardDescription>
                Current stock positions in your portfolio
              </CardDescription>
            </CardHeader>
            <CardContent>
              {isLoadingQuotes ? (
                <div className="flex h-[300px] items-center justify-center">
                  <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                </div>
              ) : portfolioWithQuotes?.holdingsWithQuotes &&
                portfolioWithQuotes.holdingsWithQuotes.length > 0 ? (
                <ScrollArea className="h-[350px]">
                  <div className="space-y-1">
                    {/* Header */}
                    <div className="grid grid-cols-6 gap-4 px-4 py-2 text-xs font-medium text-muted-foreground border-b">
                      <div>Symbol</div>
                      <div className="text-right">Qty</div>
                      <div className="text-right">Avg Cost</div>
                      <div className="text-right">Price</div>
                      <div className="text-right">Value</div>
                      <div className="text-right">P/L</div>
                    </div>
                    {/* Holdings */}
                    {portfolioWithQuotes.holdingsWithQuotes.map((holding) => (
                      <div
                        key={holding.id}
                        className="grid grid-cols-6 gap-4 px-4 py-3 hover:bg-accent/50 transition-colors cursor-pointer rounded-md"
                        onClick={() => handleStockClick(holding.symbol)}
                      >
                        <div className="font-medium">{holding.symbol}</div>
                        <div className="text-right">{holding.quantity}</div>
                        <div className="text-right">{formatCurrency(holding.averageCost)}</div>
                        <div className="text-right">
                          {holding.currentPrice ? formatCurrency(holding.currentPrice) : '--'}
                        </div>
                        <div className="text-right">
                          {holding.currentValue ? formatCurrency(holding.currentValue) : '--'}
                        </div>
                        <div className={cn('text-right flex items-center justify-end gap-1', getPriceChangeColor(holding.gain ?? 0))}>
                          {holding.gain !== undefined && (
                            <>
                              {holding.gain >= 0 ? (
                                <ArrowUpRight className="h-3 w-3" />
                              ) : (
                                <ArrowDownRight className="h-3 w-3" />
                              )}
                              {formatPercent(holding.gainPercent ?? 0)}
                            </>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                </ScrollArea>
              ) : (
                <div className="flex h-[300px] items-center justify-center text-muted-foreground">
                  <div className="text-center">
                    <Briefcase className="mx-auto mb-4 h-12 w-12 text-muted-foreground/50" />
                    <p className="mb-4">No holdings yet</p>
                    <Button
                      variant="outline"
                      onClick={() => setIsTransactionDialogOpen(true)}
                    >
                      <Plus className="mr-2 h-4 w-4" />
                      Add your first transaction
                    </Button>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="transactions">
          <Card>
            <CardHeader>
              <CardTitle>Transaction History</CardTitle>
              <CardDescription>
                Record of all your buy and sell transactions
              </CardDescription>
            </CardHeader>
            <CardContent>
              {isLoadingTransactions ? (
                <div className="flex h-[300px] items-center justify-center">
                  <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                </div>
              ) : transactionsData?.items && transactionsData.items.length > 0 ? (
                <ScrollArea className="h-[350px]">
                  <div className="space-y-1">
                    {/* Header */}
                    <div className="grid grid-cols-6 gap-4 px-4 py-2 text-xs font-medium text-muted-foreground border-b">
                      <div>Date</div>
                      <div>Type</div>
                      <div>Symbol</div>
                      <div className="text-right">Qty</div>
                      <div className="text-right">Price</div>
                      <div className="text-right">Total</div>
                    </div>
                    {/* Transactions */}
                    {transactionsData.items.map((transaction) => (
                      <div
                        key={transaction.id}
                        className="grid grid-cols-6 gap-4 px-4 py-3 hover:bg-accent/50 transition-colors rounded-md"
                      >
                        <div className="text-sm">{formatDate(transaction.date)}</div>
                        <div>
                          <span
                            className={cn(
                              'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium',
                              transaction.type === 'BUY'
                                ? 'bg-stock-up/10 text-stock-up'
                                : 'bg-stock-down/10 text-stock-down'
                            )}
                          >
                            {transaction.type}
                          </span>
                        </div>
                        <div className="font-medium">{transaction.symbol}</div>
                        <div className="text-right">{transaction.quantity}</div>
                        <div className="text-right">{formatCurrency(transaction.price)}</div>
                        <div className="text-right">
                          {formatCurrency(transaction.quantity * transaction.price + transaction.fee)}
                        </div>
                      </div>
                    ))}
                  </div>
                </ScrollArea>
              ) : (
                <div className="flex h-[300px] items-center justify-center text-muted-foreground">
                  <div className="text-center">
                    <DollarSign className="mx-auto mb-4 h-12 w-12 text-muted-foreground/50" />
                    <p className="mb-4">No transactions yet</p>
                    <Button
                      variant="outline"
                      onClick={() => setIsTransactionDialogOpen(true)}
                    >
                      <Plus className="mr-2 h-4 w-4" />
                      Record your first transaction
                    </Button>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      {/* Create portfolio dialog */}
      <Dialog open={isCreateDialogOpen} onOpenChange={setIsCreateDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Create Portfolio</DialogTitle>
            <DialogDescription>
              Create a new portfolio to track your investments.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 py-4">
            <div className="grid gap-2">
              <Label htmlFor="portfolio-name">Name</Label>
              <Input
                id="portfolio-name"
                value={newPortfolioName}
                onChange={(e) => setNewPortfolioName(e.target.value)}
                placeholder="My Portfolio"
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="portfolio-description">Description (optional)</Label>
              <Input
                id="portfolio-description"
                value={newPortfolioDescription}
                onChange={(e) => setNewPortfolioDescription(e.target.value)}
                placeholder="Long-term growth stocks"
              />
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setIsCreateDialogOpen(false)
                setNewPortfolioName('')
                setNewPortfolioDescription('')
              }}
            >
              Cancel
            </Button>
            <Button
              onClick={handleCreatePortfolio}
              disabled={!newPortfolioName.trim() || createMutation.isPending}
            >
              {createMutation.isPending && (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              )}
              Create
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete portfolio dialog */}
      <Dialog open={isDeleteDialogOpen} onOpenChange={setIsDeleteDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete Portfolio</DialogTitle>
            <DialogDescription>
              Are you sure you want to delete "{portfolioToDelete?.name}"? This action
              cannot be undone and all transactions will be lost.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setIsDeleteDialogOpen(false)
                setPortfolioToDelete(null)
              }}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={handleConfirmDelete}
              disabled={deleteMutation.isPending}
            >
              {deleteMutation.isPending && (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              )}
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Add transaction dialog */}
      <Dialog open={isTransactionDialogOpen} onOpenChange={setIsTransactionDialogOpen}>
        <DialogContent className="sm:max-w-[425px]">
          <DialogHeader>
            <DialogTitle>Add Transaction</DialogTitle>
            <DialogDescription>
              Record a buy or sell transaction.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 py-4">
            <div className="grid grid-cols-2 gap-4">
              <div className="grid gap-2">
                <Label htmlFor="symbol">Symbol</Label>
                <Input
                  id="symbol"
                  value={transactionForm.symbol}
                  onChange={(e) =>
                    setTransactionForm((prev) => ({
                      ...prev,
                      symbol: e.target.value.toUpperCase(),
                    }))
                  }
                  placeholder="AAPL"
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="type">Type</Label>
                <Select
                  value={transactionForm.type}
                  onValueChange={(value: 'BUY' | 'SELL') =>
                    setTransactionForm((prev) => ({ ...prev, type: value }))
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="BUY">Buy</SelectItem>
                    <SelectItem value="SELL">Sell</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="grid gap-2">
                <Label htmlFor="quantity">Quantity</Label>
                <Input
                  id="quantity"
                  type="number"
                  value={transactionForm.quantity}
                  onChange={(e) =>
                    setTransactionForm((prev) => ({ ...prev, quantity: e.target.value }))
                  }
                  placeholder="100"
                  min="0"
                  step="1"
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="price">Price per share</Label>
                <Input
                  id="price"
                  type="number"
                  value={transactionForm.price}
                  onChange={(e) =>
                    setTransactionForm((prev) => ({ ...prev, price: e.target.value }))
                  }
                  placeholder="150.00"
                  min="0"
                  step="0.01"
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="grid gap-2">
                <Label htmlFor="date">Date</Label>
                <Input
                  id="date"
                  type="date"
                  value={transactionForm.date}
                  onChange={(e) =>
                    setTransactionForm((prev) => ({ ...prev, date: e.target.value }))
                  }
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="fee">Fee (optional)</Label>
                <Input
                  id="fee"
                  type="number"
                  value={transactionForm.fee}
                  onChange={(e) =>
                    setTransactionForm((prev) => ({ ...prev, fee: e.target.value }))
                  }
                  placeholder="0.00"
                  min="0"
                  step="0.01"
                />
              </div>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="notes">Notes (optional)</Label>
              <Input
                id="notes"
                value={transactionForm.notes}
                onChange={(e) =>
                  setTransactionForm((prev) => ({ ...prev, notes: e.target.value }))
                }
                placeholder="Earnings play..."
              />
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setIsTransactionDialogOpen(false)
                resetTransactionForm()
              }}
            >
              Cancel
            </Button>
            <Button
              onClick={handleSubmitTransaction}
              disabled={addTransactionMutation.isPending}
            >
              {addTransactionMutation.isPending && (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              )}
              Add Transaction
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
