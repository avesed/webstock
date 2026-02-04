import { useState, useCallback, useMemo, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Plus,
  Search,
  MoreHorizontal,
  Trash2,
  Edit2,
  TrendingUp,
  TrendingDown,
  Loader2,
  Star,
  AlertCircle,
} from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
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
import { ScrollArea } from '@/components/ui/scroll-area'
import { StockSearch } from '@/components/search'
import { cn } from '@/lib/utils'
import { formatCurrency, formatPercent, getPriceChangeColor } from '@/lib/utils'
import { watchlistApi, stockApi } from '@/api'
import { useToast } from '@/hooks'
import type { Watchlist, StockQuote } from '@/types'

interface WatchlistWithQuotes extends Watchlist {
  quotes: Map<string, StockQuote>
}

export default function WatchlistPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { toast } = useToast()

  const [activeWatchlistId, setActiveWatchlistId] = useState<number | null>(null)
  const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false)
  const [isAddStockDialogOpen, setIsAddStockDialogOpen] = useState(false)
  const [isRenameDialogOpen, setIsRenameDialogOpen] = useState(false)
  const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false)
  const [newWatchlistName, setNewWatchlistName] = useState('')
  const [watchlistToModify, setWatchlistToModify] = useState<Watchlist | null>(null)

  // Fetch watchlists
  const {
    data: watchlistsData,
    isLoading: isLoadingWatchlists,
    error: watchlistsError,
  } = useQuery({
    queryKey: ['watchlists'],
    queryFn: watchlistApi.getAll,
  })

  // Ensure watchlists is always an array - handle undefined, null, or error cases
  const watchlists = useMemo(() => {
    if (Array.isArray(watchlistsData)) {
      return watchlistsData
    }
    return []
  }, [watchlistsData])

  // Auto-select first watchlist
  useEffect(() => {
    if (watchlists.length > 0 && !activeWatchlistId) {
      setActiveWatchlistId(watchlists[0]?.id ?? null)
    }
  }, [watchlists, activeWatchlistId])

  // Fetch quotes for active watchlist
  const {
    data: activeWatchlistWithQuotes,
    isLoading: isLoadingQuotes,
  } = useQuery({
    queryKey: ['watchlist-quotes', activeWatchlistId],
    queryFn: async (): Promise<WatchlistWithQuotes | null> => {
      if (!activeWatchlistId) return null

      // Fetch watchlist details to get items/symbols
      const watchlist = await watchlistApi.get(activeWatchlistId)
      if (!watchlist) return null

      const quotes = new Map<string, StockQuote>()

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
    enabled: !!activeWatchlistId,
    refetchInterval: 30000,
  })

  // Create watchlist mutation
  const createMutation = useMutation({
    mutationFn: (name: string) => watchlistApi.create(name),
    onSuccess: (newWatchlist) => {
      queryClient.invalidateQueries({ queryKey: ['watchlists'] })
      setActiveWatchlistId(newWatchlist.id)
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
        description: 'Failed to create watchlist.',
        variant: 'destructive',
      })
    },
  })

  // Delete watchlist mutation
  const deleteMutation = useMutation({
    mutationFn: (id: number) => watchlistApi.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['watchlists'] })
      if (activeWatchlistId === watchlistToModify?.id) {
        setActiveWatchlistId(watchlists?.[0]?.id ?? null)
      }
      setIsDeleteDialogOpen(false)
      setWatchlistToModify(null)
      toast({
        title: 'Watchlist deleted',
        description: 'The watchlist has been deleted.',
      })
    },
    onError: () => {
      toast({
        title: 'Error',
        description: 'Failed to delete watchlist.',
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
      setWatchlistToModify(null)
      setNewWatchlistName('')
      toast({
        title: 'Watchlist renamed',
        description: 'The watchlist has been renamed.',
      })
    },
    onError: () => {
      toast({
        title: 'Error',
        description: 'Failed to rename watchlist.',
        variant: 'destructive',
      })
    },
  })

  // Add symbol mutation
  const addSymbolMutation = useMutation({
    mutationFn: ({ watchlistId, symbol }: { watchlistId: number; symbol: string }) =>
      watchlistApi.addSymbol(watchlistId, symbol),
    onSuccess: (_, { symbol }) => {
      queryClient.invalidateQueries({ queryKey: ['watchlists'] })
      queryClient.invalidateQueries({ queryKey: ['watchlist-quotes', activeWatchlistId] })
      setIsAddStockDialogOpen(false)
      toast({
        title: 'Stock added',
        description: `${symbol} has been added to the watchlist.`,
      })
    },
    onError: (error: unknown, { symbol }) => {
      // Check for 409 Conflict error (stock already in watchlist)
      if (error && typeof error === 'object' && 'response' in error) {
        const axiosError = error as { response?: { status?: number; data?: { detail?: string } } }
        if (axiosError.response?.status === 409) {
          toast({
            title: 'Already added',
            description: `${symbol} is already in this watchlist.`,
            variant: 'default',
          })
          return
        }
      }
      toast({
        title: 'Error',
        description: 'Failed to add stock.',
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
      queryClient.invalidateQueries({ queryKey: ['watchlist-quotes', activeWatchlistId] })
      toast({
        title: 'Stock removed',
        description: 'The stock has been removed from the watchlist.',
      })
    },
    onError: () => {
      toast({
        title: 'Error',
        description: 'Failed to remove stock.',
        variant: 'destructive',
      })
    },
  })

  // Handle stock selection from search
  const handleStockSelect = useCallback(
    (result: { symbol: string }) => {
      if (activeWatchlistId) {
        addSymbolMutation.mutate({
          watchlistId: activeWatchlistId,
          symbol: result.symbol,
        })
      }
    },
    [activeWatchlistId, addSymbolMutation]
  )

  // Handle stock click to navigate
  const handleStockClick = useCallback(
    (symbol: string) => {
      navigate(`/stock/${symbol}`)
    },
    [navigate]
  )

  // Handle create watchlist
  const handleCreateWatchlist = useCallback(() => {
    if (newWatchlistName.trim()) {
      createMutation.mutate(newWatchlistName.trim())
    }
  }, [newWatchlistName, createMutation])

  // Handle rename watchlist
  const handleRenameWatchlist = useCallback(() => {
    if (watchlistToModify && newWatchlistName.trim()) {
      renameMutation.mutate({
        id: watchlistToModify.id,
        name: newWatchlistName.trim(),
      })
    }
  }, [watchlistToModify, newWatchlistName, renameMutation])

  // Handle delete watchlist
  const handleDeleteWatchlist = useCallback(() => {
    if (watchlistToModify) {
      deleteMutation.mutate(watchlistToModify.id)
    }
  }, [watchlistToModify, deleteMutation])

  // Handle remove symbol
  const handleRemoveSymbol = useCallback(
    (symbol: string) => {
      if (activeWatchlistId) {
        removeSymbolMutation.mutate({
          watchlistId: activeWatchlistId,
          symbol,
        })
      }
    },
    [activeWatchlistId, removeSymbolMutation]
  )

  // Open rename dialog
  const openRenameDialog = useCallback((watchlist: Watchlist) => {
    setWatchlistToModify(watchlist)
    setNewWatchlistName(watchlist.name)
    setIsRenameDialogOpen(true)
  }, [])

  // Open delete dialog
  const openDeleteDialog = useCallback((watchlist: Watchlist) => {
    setWatchlistToModify(watchlist)
    setIsDeleteDialogOpen(true)
  }, [])

  if (isLoadingWatchlists) {
    return (
      <div className="flex h-[400px] items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (watchlistsError) {
    return (
      <div className="flex flex-col items-center justify-center gap-4 py-16">
        <AlertCircle className="h-12 w-12 text-destructive" />
        <h2 className="text-xl font-semibold">Failed to load watchlists</h2>
        <Button onClick={() => queryClient.invalidateQueries({ queryKey: ['watchlists'] })}>
          Try again
        </Button>
      </div>
    )
  }

  const activeWatchlist = watchlists.find((w) => w.id === activeWatchlistId)

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Watchlist</h1>
          <p className="text-muted-foreground">
            Track your favorite stocks and monitor their performance
          </p>
        </div>
        <Button onClick={() => setIsCreateDialogOpen(true)}>
          <Plus className="mr-2 h-4 w-4" />
          New Watchlist
        </Button>
      </div>

      {watchlists && watchlists.length > 0 ? (
        <div className="grid gap-6 lg:grid-cols-4">
          {/* Watchlist tabs */}
          <Card className="lg:col-span-1">
            <CardHeader>
              <CardTitle className="text-base">My Watchlists</CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <ScrollArea className="h-[500px]">
                <div className="space-y-1 p-2">
                  {watchlists.map((watchlist) => (
                    <div
                      key={watchlist.id}
                      className={cn(
                        'group flex items-center justify-between rounded-lg px-3 py-2 cursor-pointer transition-colors',
                        activeWatchlistId === watchlist.id
                          ? 'bg-primary text-primary-foreground'
                          : 'hover:bg-accent'
                      )}
                      onClick={() => setActiveWatchlistId(watchlist.id)}
                    >
                      <div className="flex items-center gap-2 min-w-0">
                        <Star className="h-4 w-4 shrink-0" />
                        <span className="truncate font-medium">{watchlist.name}</span>
                        <span
                          className={cn(
                            'text-xs',
                            activeWatchlistId === watchlist.id
                              ? 'text-primary-foreground/70'
                              : 'text-muted-foreground'
                          )}
                        >
                          ({watchlist.symbols?.length ?? 0})
                        </span>
                      </div>
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button
                            variant="ghost"
                            size="icon"
                            className={cn(
                              'h-6 w-6 opacity-0 group-hover:opacity-100',
                              activeWatchlistId === watchlist.id && 'hover:bg-primary-foreground/10'
                            )}
                            onClick={(e) => e.stopPropagation()}
                          >
                            <MoreHorizontal className="h-4 w-4" />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuItem onClick={() => openRenameDialog(watchlist)}>
                            <Edit2 className="mr-2 h-4 w-4" />
                            Rename
                          </DropdownMenuItem>
                          <DropdownMenuSeparator />
                          <DropdownMenuItem
                            onClick={() => openDeleteDialog(watchlist)}
                            className="text-destructive"
                          >
                            <Trash2 className="mr-2 h-4 w-4" />
                            Delete
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </div>
                  ))}
                </div>
              </ScrollArea>
            </CardContent>
          </Card>

          {/* Active watchlist content */}
          <Card className="lg:col-span-3">
            <CardHeader>
              <div className="flex items-center justify-between">
                <div>
                  <CardTitle>{activeWatchlist?.name ?? 'Select a watchlist'}</CardTitle>
                  <CardDescription>
                    {activeWatchlist
                      ? `${activeWatchlist.symbols?.length ?? 0} stocks tracked`
                      : 'Choose a watchlist to view its stocks'}
                  </CardDescription>
                </div>
                {activeWatchlist && (
                  <Button onClick={() => setIsAddStockDialogOpen(true)}>
                    <Plus className="mr-2 h-4 w-4" />
                    Add Stock
                  </Button>
                )}
              </div>
            </CardHeader>
            <CardContent>
              {isLoadingQuotes ? (
                <div className="flex h-[400px] items-center justify-center">
                  <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                </div>
              ) : activeWatchlistWithQuotes?.symbols && activeWatchlistWithQuotes.symbols.length > 0 ? (
                <div className="rounded-lg border">
                  <table className="w-full">
                    <thead>
                      <tr className="border-b bg-muted/50">
                        <th className="px-4 py-3 text-left text-sm font-medium">Symbol</th>
                        <th className="px-4 py-3 text-left text-sm font-medium">Name</th>
                        <th className="px-4 py-3 text-right text-sm font-medium">Price</th>
                        <th className="px-4 py-3 text-right text-sm font-medium">Change</th>
                        <th className="px-4 py-3 text-right text-sm font-medium">Volume</th>
                        <th className="px-4 py-3 text-right text-sm font-medium"></th>
                      </tr>
                    </thead>
                    <tbody>
                      {activeWatchlistWithQuotes.symbols.map((symbol) => {
                        const quote = activeWatchlistWithQuotes.quotes.get(symbol)
                        return (
                          <tr
                            key={symbol}
                            className="border-b last:border-0 hover:bg-accent/50 cursor-pointer transition-colors"
                            onClick={() => handleStockClick(symbol)}
                          >
                            <td className="px-4 py-3">
                              <div className="flex items-center gap-2">
                                <span className="font-medium">{symbol}</span>
                                {quote && (
                                  <span
                                    className={cn(
                                      'flex items-center',
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
                            </td>
                            <td className="px-4 py-3 text-muted-foreground">
                              {quote?.name ?? '--'}
                            </td>
                            <td className="px-4 py-3 text-right font-medium">
                              {quote ? formatCurrency(quote.price) : '--'}
                            </td>
                            <td
                              className={cn(
                                'px-4 py-3 text-right font-medium',
                                quote && getPriceChangeColor(quote.change)
                              )}
                            >
                              {quote ? formatPercent(quote.changePercent) : '--'}
                            </td>
                            <td className="px-4 py-3 text-right text-muted-foreground">
                              {quote ? (quote.volume / 1_000_000).toFixed(2) + 'M' : '--'}
                            </td>
                            <td className="px-4 py-3 text-right">
                              <Button
                                variant="ghost"
                                size="icon"
                                className="h-8 w-8"
                                onClick={(e) => {
                                  e.stopPropagation()
                                  handleRemoveSymbol(symbol)
                                }}
                              >
                                <Trash2 className="h-4 w-4 text-muted-foreground hover:text-destructive" />
                              </Button>
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              ) : activeWatchlist ? (
                <div className="flex h-[400px] flex-col items-center justify-center gap-4">
                  <Search className="h-12 w-12 text-muted-foreground/50" />
                  <div className="text-center">
                    <p className="font-medium">No stocks in this watchlist</p>
                    <p className="text-sm text-muted-foreground">
                      Add stocks to track their performance
                    </p>
                  </div>
                  <Button onClick={() => setIsAddStockDialogOpen(true)}>
                    <Plus className="mr-2 h-4 w-4" />
                    Add your first stock
                  </Button>
                </div>
              ) : (
                <div className="flex h-[400px] items-center justify-center text-muted-foreground">
                  Select a watchlist to view its stocks
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      ) : (
        <Card>
          <CardContent className="flex h-[400px] flex-col items-center justify-center gap-4">
            <Star className="h-12 w-12 text-muted-foreground/50" />
            <div className="text-center">
              <p className="font-medium">No watchlists yet</p>
              <p className="text-sm text-muted-foreground">
                Create your first watchlist to start tracking stocks
              </p>
            </div>
            <Button onClick={() => setIsCreateDialogOpen(true)}>
              <Plus className="mr-2 h-4 w-4" />
              Create your first watchlist
            </Button>
          </CardContent>
        </Card>
      )}

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
                  if (e.key === 'Enter') handleCreateWatchlist()
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
              {createMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Create
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Add stock dialog */}
      <Dialog open={isAddStockDialogOpen} onOpenChange={setIsAddStockDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Add Stock</DialogTitle>
            <DialogDescription>
              Search for a stock to add to "{activeWatchlist?.name}"
            </DialogDescription>
          </DialogHeader>
          <div className="py-4">
            <StockSearch
              placeholder="Search by symbol or name..."
              onSelect={handleStockSelect}
              autoFocus
              showRecentSearches={false}
            />
          </div>
        </DialogContent>
      </Dialog>

      {/* Rename watchlist dialog */}
      <Dialog open={isRenameDialogOpen} onOpenChange={setIsRenameDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Rename Watchlist</DialogTitle>
            <DialogDescription>Enter a new name for this watchlist.</DialogDescription>
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
                  if (e.key === 'Enter') handleRenameWatchlist()
                }}
              />
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setIsRenameDialogOpen(false)
                setWatchlistToModify(null)
                setNewWatchlistName('')
              }}
            >
              Cancel
            </Button>
            <Button
              onClick={handleRenameWatchlist}
              disabled={!newWatchlistName.trim() || renameMutation.isPending}
            >
              {renameMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Rename
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
              Are you sure you want to delete "{watchlistToModify?.name}"? This action cannot
              be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setIsDeleteDialogOpen(false)
                setWatchlistToModify(null)
              }}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={handleDeleteWatchlist}
              disabled={deleteMutation.isPending}
            >
              {deleteMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
