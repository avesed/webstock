import { useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Plus,
  MoreVertical,
  Trash2,
  Edit2,
  ChevronRight,
  TrendingUp,
  TrendingDown,
  Loader2,
  Star,
  AlertCircle,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'
import { cn, isMetal } from '@/lib/utils'
import { formatCurrency, formatPercent, getPriceChangeColor } from '@/lib/utils'
import { watchlistApi, stockApi } from '@/api'
import { useToast } from '@/hooks'
import type { Watchlist, StockQuote } from '@/types'

interface WatchlistPanelProps {
  className?: string
  compact?: boolean
  onStockSelect?: (symbol: string) => void
}

interface WatchlistWithQuotes extends Watchlist {
  quotes: Map<string, StockQuote>
}

export default function WatchlistPanel({
  className,
  compact = false,
  onStockSelect,
}: WatchlistPanelProps) {
  const { t } = useTranslation('dashboard')
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { toast } = useToast()

  const [selectedWatchlistId, setSelectedWatchlistId] = useState<number | null>(null)
  const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false)
  const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false)
  const [isRenameDialogOpen, setIsRenameDialogOpen] = useState(false)
  const [newWatchlistName, setNewWatchlistName] = useState('')
  const [watchlistToDelete, setWatchlistToDelete] = useState<Watchlist | null>(null)
  const [watchlistToRename, setWatchlistToRename] = useState<Watchlist | null>(null)

  // Fetch watchlists
  const {
    data: watchlistsData,
    isLoading: isLoadingWatchlists,
    error: watchlistsError,
  } = useQuery({
    queryKey: ['watchlists'],
    queryFn: watchlistApi.getAll,
  })

  // Ensure watchlists is always an array
  const watchlists = Array.isArray(watchlistsData) ? watchlistsData : []

  // Fetch quotes for selected watchlist
  const {
    data: watchlistWithQuotes,
    isLoading: isLoadingQuotes,
  } = useQuery({
    queryKey: ['watchlist-quotes', selectedWatchlistId],
    queryFn: async (): Promise<WatchlistWithQuotes | null> => {
      if (!selectedWatchlistId || !watchlists) return null

      const watchlist = watchlists.find((w) => w.id === selectedWatchlistId)
      if (!watchlist) return null

      const quotes = new Map<string, StockQuote>()

      // Fetch quotes for all symbols in parallel
      await Promise.all(
        (watchlist.symbols || []).map(async (symbol) => {
          try {
            const quote = await stockApi.getQuote(symbol)
            quotes.set(symbol, quote)
          } catch {
            // Skip failed quotes
          }
        })
      )

      return { ...watchlist, quotes }
    },
    enabled: !!selectedWatchlistId && !!watchlists,
    refetchInterval: 30000, // Refresh quotes every 30 seconds
  })

  // Create watchlist mutation
  const createMutation = useMutation({
    mutationFn: (name: string) => watchlistApi.create(name),
    onSuccess: (newWatchlist) => {
      queryClient.invalidateQueries({ queryKey: ['watchlists'] })
      setSelectedWatchlistId(newWatchlist.id)
      setIsCreateDialogOpen(false)
      setNewWatchlistName('')
      toast({
        title: t('watchlist.createWatchlist'),
        description: `"${newWatchlist.name}"`,
      })
    },
    onError: () => {
      toast({
        title: t('common:status.error', 'Error'),
        description: t('common:errors.unknown', 'Failed to create.'),
        variant: 'destructive',
      })
    },
  })

  // Delete watchlist mutation
  const deleteMutation = useMutation({
    mutationFn: (id: number) => watchlistApi.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['watchlists'] })
      if (selectedWatchlistId === watchlistToDelete?.id) {
        setSelectedWatchlistId(null)
      }
      setIsDeleteDialogOpen(false)
      setWatchlistToDelete(null)
      toast({
        title: t('watchlist.deleteWatchlist'),
        description: t('common:status.success', 'Success'),
      })
    },
    onError: () => {
      toast({
        title: t('common:status.error', 'Error'),
        description: t('common:errors.unknown', 'Failed to delete.'),
        variant: 'destructive',
      })
    },
  })

  // Rename watchlist mutation
  const renameMutation = useMutation({
    mutationFn: ({ id, name }: { id: number; name: string }) =>
      watchlistApi.update(id, { name }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['watchlists'] })
      setIsRenameDialogOpen(false)
      setWatchlistToRename(null)
      setNewWatchlistName('')
      toast({
        title: t('watchlist.editWatchlist'),
        description: t('common:status.success', 'Success'),
      })
    },
    onError: () => {
      toast({
        title: t('common:status.error', 'Error'),
        description: t('common:errors.unknown', 'Failed to rename.'),
        variant: 'destructive',
      })
    },
  })

  // Remove symbol mutation
  const removeSymbolMutation = useMutation({
    mutationFn: ({ watchlistId, symbol }: { watchlistId: number; symbol: string }) =>
      watchlistApi.removeSymbol(watchlistId, symbol),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['watchlists'] })
      queryClient.invalidateQueries({ queryKey: ['watchlist-quotes', selectedWatchlistId] })
      toast({
        title: t('watchlist.removeSymbol'),
        description: t('common:status.success', 'Success'),
      })
    },
    onError: () => {
      toast({
        title: t('common:status.error', 'Error'),
        description: t('common:errors.unknown', 'Failed to remove.'),
        variant: 'destructive',
      })
    },
  })

  // Handle stock click
  const handleStockClick = useCallback(
    (symbol: string) => {
      if (onStockSelect) {
        onStockSelect(symbol)
      } else {
        navigate(`/stock/${symbol}`)
      }
    },
    [navigate, onStockSelect]
  )

  // Handle create watchlist
  const handleCreateWatchlist = useCallback(() => {
    if (newWatchlistName.trim()) {
      createMutation.mutate(newWatchlistName.trim())
    }
  }, [newWatchlistName, createMutation])

  // Handle delete watchlist
  const handleDeleteWatchlist = useCallback((watchlist: Watchlist) => {
    setWatchlistToDelete(watchlist)
    setIsDeleteDialogOpen(true)
  }, [])

  // Handle rename watchlist
  const handleRenameWatchlist = useCallback((watchlist: Watchlist) => {
    setWatchlistToRename(watchlist)
    setNewWatchlistName(watchlist.name)
    setIsRenameDialogOpen(true)
  }, [])

  // Handle confirm delete
  const handleConfirmDelete = useCallback(() => {
    if (watchlistToDelete) {
      deleteMutation.mutate(watchlistToDelete.id)
    }
  }, [watchlistToDelete, deleteMutation])

  // Handle confirm rename
  const handleConfirmRename = useCallback(() => {
    if (watchlistToRename && newWatchlistName.trim()) {
      renameMutation.mutate({
        id: watchlistToRename.id,
        name: newWatchlistName.trim(),
      })
    }
  }, [watchlistToRename, newWatchlistName, renameMutation])

  // Handle remove symbol
  const handleRemoveSymbol = useCallback(
    (symbol: string) => {
      if (selectedWatchlistId) {
        removeSymbolMutation.mutate({
          watchlistId: selectedWatchlistId,
          symbol,
        })
      }
    },
    [selectedWatchlistId, removeSymbolMutation]
  )

  // Auto-select first watchlist
  if (watchlists && watchlists.length > 0 && !selectedWatchlistId) {
    setSelectedWatchlistId(watchlists[0]?.id ?? null)
  }

  if (isLoadingWatchlists) {
    return (
      <div className={cn('flex items-center justify-center p-8', className)}>
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (watchlistsError) {
    return (
      <div className={cn('flex flex-col items-center justify-center gap-2 p-8', className)}>
        <AlertCircle className="h-8 w-8 text-destructive" />
        <p className="text-sm text-muted-foreground">{t('common:status.error', 'Failed to load')}</p>
        <Button
          variant="outline"
          size="sm"
          onClick={() => queryClient.invalidateQueries({ queryKey: ['watchlists'] })}
        >
          {t('common:actions.retry', 'Try again')}
        </Button>
      </div>
    )
  }

  const selectedWatchlist = watchlists.find((w) => w.id === selectedWatchlistId)

  return (
    <div className={cn('flex flex-col', className)}>
      {/* Watchlist selector */}
      <div className="flex items-center gap-2 border-b p-3">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" className="flex-1 justify-between">
              <div className="flex items-center gap-2">
                <Star className="h-4 w-4" />
                <span className="truncate">
                  {selectedWatchlist?.name ?? t('common:actions.select', 'Select')}
                </span>
              </div>
              <ChevronRight className="h-4 w-4 rotate-90 opacity-50" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-56">
            {watchlists?.map((watchlist) => (
              <DropdownMenuItem
                key={watchlist.id}
                onClick={() => setSelectedWatchlistId(watchlist.id)}
                className={cn(
                  'flex items-center justify-between',
                  selectedWatchlistId === watchlist.id && 'bg-accent'
                )}
              >
                <span className="truncate">{watchlist.name}</span>
                <span className="text-xs text-muted-foreground">
                  {watchlist.symbols?.length ?? 0}
                </span>
              </DropdownMenuItem>
            ))}
            {watchlists && watchlists.length > 0 && <DropdownMenuSeparator />}
            <DropdownMenuItem onClick={() => setIsCreateDialogOpen(true)}>
              <Plus className="mr-2 h-4 w-4" />
              {t('watchlist.createWatchlist')}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>

        {selectedWatchlist && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon">
                <MoreVertical className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={() => handleRenameWatchlist(selectedWatchlist)}>
                <Edit2 className="mr-2 h-4 w-4" />
                {t('common:actions.edit', 'Rename')}
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                onClick={() => handleDeleteWatchlist(selectedWatchlist)}
                className="text-destructive"
              >
                <Trash2 className="mr-2 h-4 w-4" />
                {t('common:actions.delete', 'Delete')}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </div>

      {/* Stock list */}
      <ScrollArea className="flex-1">
        {isLoadingQuotes ? (
          <div className="flex items-center justify-center p-8">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        ) : watchlistWithQuotes?.symbols && watchlistWithQuotes.symbols.length > 0 ? (
          <div className="divide-y">
            {watchlistWithQuotes.symbols.map((symbol) => {
              const quote = watchlistWithQuotes.quotes.get(symbol)
              const isMetalSymbol = isMetal(symbol)
              return (
                <div
                  key={symbol}
                  className="group flex items-center gap-3 px-3 py-2 hover:bg-accent/50 transition-colors cursor-pointer"
                  onClick={() => handleStockClick(symbol)}
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-medium">{symbol}</span>
                      {isMetalSymbol && (
                        <span
                          className="inline-flex items-center rounded-full bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-200 px-1.5 py-0.5 text-xs font-medium"
                          aria-label="Precious metal asset"
                        >
                          {t('market.metal', 'METAL')}
                        </span>
                      )}
                      {quote && (
                        <span
                          className={cn(
                            'flex items-center text-xs',
                            getPriceChangeColor(quote.change)
                          )}
                        >
                          {quote.change >= 0 ? (
                            <TrendingUp className="h-3 w-3" />
                          ) : (
                            <TrendingDown className="h-3 w-3" />
                          )}
                        </span>
                      )}
                    </div>
                    {quote && !compact && (
                      <p className="truncate text-xs text-muted-foreground">
                        {quote.name}
                      </p>
                    )}
                  </div>
                  <div className="text-right">
                    {quote ? (
                      <>
                        <div className="font-medium">
                          {formatCurrency(quote.price)}
                        </div>
                        <div
                          className={cn(
                            'text-xs',
                            getPriceChangeColor(quote.change)
                          )}
                        >
                          {formatPercent(quote.changePercent)}
                        </div>
                      </>
                    ) : (
                      <span className="text-sm text-muted-foreground">--</span>
                    )}
                  </div>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-6 w-6 opacity-0 group-hover:opacity-100 transition-opacity"
                    onClick={(e) => {
                      e.stopPropagation()
                      handleRemoveSymbol(symbol)
                    }}
                  >
                    <Trash2 className="h-3 w-3 text-muted-foreground hover:text-destructive" />
                  </Button>
                </div>
              )
            })}
          </div>
        ) : selectedWatchlist ? (
          <div className="flex flex-col items-center justify-center gap-2 p-8 text-center">
            <Star className="h-8 w-8 text-muted-foreground/50" />
            <p className="text-sm text-muted-foreground">
              {t('watchlist.noSymbols')}
            </p>
            <p className="text-xs text-muted-foreground">
              {t('watchlist.addFirst')}
            </p>
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center gap-2 p-8 text-center">
            <Star className="h-8 w-8 text-muted-foreground/50" />
            <p className="text-sm text-muted-foreground">
              {t('watchlist.noWatchlists')}
            </p>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setIsCreateDialogOpen(true)}
            >
              <Plus className="mr-2 h-4 w-4" />
              {t('watchlist.createFirst')}
            </Button>
          </div>
        )}
      </ScrollArea>

      {/* Create watchlist dialog */}
      <Dialog open={isCreateDialogOpen} onOpenChange={setIsCreateDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('watchlist.createWatchlist')}</DialogTitle>
            <DialogDescription>
              {t('watchlist.title')}
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 py-4">
            <div className="grid gap-2">
              <Label htmlFor="name">{t('watchlist.name')}</Label>
              <Input
                id="name"
                value={newWatchlistName}
                onChange={(e) => setNewWatchlistName(e.target.value)}
                placeholder={t('watchlist.watchlistPlaceholder')}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    handleCreateWatchlist()
                  }
                }}
              />
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setIsCreateDialogOpen(false)
                setNewWatchlistName('')
              }}
            >
              {t('common:actions.cancel', 'Cancel')}
            </Button>
            <Button
              onClick={handleCreateWatchlist}
              disabled={!newWatchlistName.trim() || createMutation.isPending}
            >
              {createMutation.isPending && (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              )}
              {t('common:actions.create', 'Create')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete watchlist dialog */}
      <Dialog open={isDeleteDialogOpen} onOpenChange={setIsDeleteDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('watchlist.deleteWatchlist')}</DialogTitle>
            <DialogDescription>
              {t('common:confirmation.deleteMessage', 'Are you sure?')} "{watchlistToDelete?.name}"
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setIsDeleteDialogOpen(false)
                setWatchlistToDelete(null)
              }}
            >
              {t('common:actions.cancel', 'Cancel')}
            </Button>
            <Button
              variant="destructive"
              onClick={handleConfirmDelete}
              disabled={deleteMutation.isPending}
            >
              {deleteMutation.isPending && (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              )}
              {t('common:actions.delete', 'Delete')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Rename watchlist dialog */}
      <Dialog open={isRenameDialogOpen} onOpenChange={setIsRenameDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('watchlist.editWatchlist')}</DialogTitle>
            <DialogDescription>
              {t('watchlist.watchlistName')}
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 py-4">
            <div className="grid gap-2">
              <Label htmlFor="rename">{t('watchlist.name')}</Label>
              <Input
                id="rename"
                value={newWatchlistName}
                onChange={(e) => setNewWatchlistName(e.target.value)}
                placeholder={t('watchlist.watchlistPlaceholder')}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    handleConfirmRename()
                  }
                }}
              />
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setIsRenameDialogOpen(false)
                setWatchlistToRename(null)
                setNewWatchlistName('')
              }}
            >
              {t('common:actions.cancel', 'Cancel')}
            </Button>
            <Button
              onClick={handleConfirmRename}
              disabled={!newWatchlistName.trim() || renameMutation.isPending}
            >
              {renameMutation.isPending && (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              )}
              {t('common:actions.save', 'Save')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
