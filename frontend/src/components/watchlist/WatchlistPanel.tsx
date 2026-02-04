import { useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
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
import { cn } from '@/lib/utils'
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
        title: 'Watchlist created',
        description: `"${newWatchlist.name}" has been created successfully.`,
      })
    },
    onError: () => {
      toast({
        title: 'Error',
        description: 'Failed to create watchlist. Please try again.',
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
        title: 'Watchlist deleted',
        description: 'The watchlist has been deleted successfully.',
      })
    },
    onError: () => {
      toast({
        title: 'Error',
        description: 'Failed to delete watchlist. Please try again.',
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
        title: 'Watchlist renamed',
        description: 'The watchlist has been renamed successfully.',
      })
    },
    onError: () => {
      toast({
        title: 'Error',
        description: 'Failed to rename watchlist. Please try again.',
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
        title: 'Stock removed',
        description: 'The stock has been removed from the watchlist.',
      })
    },
    onError: () => {
      toast({
        title: 'Error',
        description: 'Failed to remove stock. Please try again.',
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
        <p className="text-sm text-muted-foreground">Failed to load watchlists</p>
        <Button
          variant="outline"
          size="sm"
          onClick={() => queryClient.invalidateQueries({ queryKey: ['watchlists'] })}
        >
          Try again
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
                  {selectedWatchlist?.name ?? 'Select watchlist'}
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
              Create new watchlist
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
                Rename
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                onClick={() => handleDeleteWatchlist(selectedWatchlist)}
                className="text-destructive"
              >
                <Trash2 className="mr-2 h-4 w-4" />
                Delete
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
              return (
                <div
                  key={symbol}
                  className="group flex items-center gap-3 px-3 py-2 hover:bg-accent/50 transition-colors cursor-pointer"
                  onClick={() => handleStockClick(symbol)}
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-medium">{symbol}</span>
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
              No stocks in this watchlist
            </p>
            <p className="text-xs text-muted-foreground">
              Search for stocks and add them to track their performance
            </p>
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center gap-2 p-8 text-center">
            <Star className="h-8 w-8 text-muted-foreground/50" />
            <p className="text-sm text-muted-foreground">
              Create a watchlist to get started
            </p>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setIsCreateDialogOpen(true)}
            >
              <Plus className="mr-2 h-4 w-4" />
              Create watchlist
            </Button>
          </div>
        )}
      </ScrollArea>

      {/* Create watchlist dialog */}
      <Dialog open={isCreateDialogOpen} onOpenChange={setIsCreateDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Create Watchlist</DialogTitle>
            <DialogDescription>
              Create a new watchlist to track your favorite stocks.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 py-4">
            <div className="grid gap-2">
              <Label htmlFor="name">Name</Label>
              <Input
                id="name"
                value={newWatchlistName}
                onChange={(e) => setNewWatchlistName(e.target.value)}
                placeholder="My Watchlist"
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
              Cancel
            </Button>
            <Button
              onClick={handleCreateWatchlist}
              disabled={!newWatchlistName.trim() || createMutation.isPending}
            >
              {createMutation.isPending && (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              )}
              Create
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete watchlist dialog */}
      <Dialog open={isDeleteDialogOpen} onOpenChange={setIsDeleteDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete Watchlist</DialogTitle>
            <DialogDescription>
              Are you sure you want to delete "{watchlistToDelete?.name}"? This action
              cannot be undone.
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

      {/* Rename watchlist dialog */}
      <Dialog open={isRenameDialogOpen} onOpenChange={setIsRenameDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Rename Watchlist</DialogTitle>
            <DialogDescription>
              Enter a new name for this watchlist.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 py-4">
            <div className="grid gap-2">
              <Label htmlFor="rename">Name</Label>
              <Input
                id="rename"
                value={newWatchlistName}
                onChange={(e) => setNewWatchlistName(e.target.value)}
                placeholder="Watchlist name"
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
              Cancel
            </Button>
            <Button
              onClick={handleConfirmRename}
              disabled={!newWatchlistName.trim() || renameMutation.isPending}
            >
              {renameMutation.isPending && (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              )}
              Rename
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
