import { useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Plus,
  Bell,
  BellOff,
  Trash2,
  Loader2,
  AlertCircle,
  TrendingUp,
  TrendingDown,
  ArrowUp,
  ArrowDown,
  RefreshCw,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { cn } from '@/lib/utils'
import { formatCurrency, formatPercent, formatRelativeTime } from '@/lib/utils'
import { alertsApi } from '@/api'
import { useToast } from '@/hooks'
import type { Alert, AlertCondition, AlertStatus, CreateAlertInput } from '@/types'

interface AlertListProps {
  className?: string
  filterSymbol?: string
}

const CONDITION_LABELS: Record<AlertCondition, string> = {
  ABOVE: 'Price above',
  BELOW: 'Price below',
  PERCENT_CHANGE_UP: 'Up by %',
  PERCENT_CHANGE_DOWN: 'Down by %',
}

const CONDITION_ICONS: Record<AlertCondition, typeof ArrowUp> = {
  ABOVE: ArrowUp,
  BELOW: ArrowDown,
  PERCENT_CHANGE_UP: TrendingUp,
  PERCENT_CHANGE_DOWN: TrendingDown,
}

export default function AlertList({ className, filterSymbol }: AlertListProps) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { toast } = useToast()

  const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false)
  const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false)
  const [alertToDelete, setAlertToDelete] = useState<Alert | null>(null)
  const [alertForm, setAlertForm] = useState<CreateAlertInput>({
    symbol: filterSymbol ?? '',
    conditionType: 'ABOVE',
    threshold: 0,
  })

  // Fetch alerts
  const {
    data: alertsData,
    isLoading,
    error,
  } = useQuery({
    queryKey: ['alerts'],
    queryFn: alertsApi.getAll,
  })

  // Ensure alerts is always an array
  const alerts = Array.isArray(alertsData) ? alertsData : []

  // Filter alerts by symbol if provided
  const filteredAlerts = filterSymbol
    ? alerts?.filter((alert) => alert.symbol === filterSymbol)
    : alerts

  // Create alert mutation
  const createMutation = useMutation({
    mutationFn: alertsApi.create,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['alerts'] })
      setIsCreateDialogOpen(false)
      setAlertForm({
        symbol: filterSymbol ?? '',
        conditionType: 'ABOVE',
        threshold: 0,
      })
      toast({
        title: 'Alert created',
        description: 'Your price alert has been created successfully.',
      })
    },
    onError: () => {
      toast({
        title: 'Error',
        description: 'Failed to create alert. Please try again.',
        variant: 'destructive',
      })
    },
  })

  // Delete alert mutation
  const deleteMutation = useMutation({
    mutationFn: alertsApi.delete,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['alerts'] })
      setIsDeleteDialogOpen(false)
      setAlertToDelete(null)
      toast({
        title: 'Alert deleted',
        description: 'The alert has been deleted successfully.',
      })
    },
    onError: () => {
      toast({
        title: 'Error',
        description: 'Failed to delete alert. Please try again.',
        variant: 'destructive',
      })
    },
  })

  // Toggle alert status mutation
  const toggleMutation = useMutation({
    mutationFn: alertsApi.toggleStatus,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['alerts'] })
      toast({
        title: 'Alert updated',
        description: 'The alert status has been updated.',
      })
    },
    onError: () => {
      toast({
        title: 'Error',
        description: 'Failed to update alert. Please try again.',
        variant: 'destructive',
      })
    },
  })

  // Reset triggered alert mutation (reactivate)
  const resetMutation = useMutation({
    mutationFn: (id: string) => alertsApi.update(id, { status: 'ACTIVE' }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['alerts'] })
      toast({
        title: 'Alert reset',
        description: 'The alert has been reactivated.',
      })
    },
    onError: () => {
      toast({
        title: 'Error',
        description: 'Failed to reset alert. Please try again.',
        variant: 'destructive',
      })
    },
  })

  const handleCreateAlert = useCallback(() => {
    if (!alertForm.symbol.trim() || alertForm.threshold <= 0) {
      toast({
        title: 'Validation error',
        description: 'Please fill in all required fields.',
        variant: 'destructive',
      })
      return
    }

    createMutation.mutate({
      ...alertForm,
      symbol: alertForm.symbol.toUpperCase(),
    })
  }, [alertForm, createMutation, toast])

  const handleDeleteAlert = useCallback((alert: Alert) => {
    setAlertToDelete(alert)
    setIsDeleteDialogOpen(true)
  }, [])

  const handleConfirmDelete = useCallback(() => {
    if (alertToDelete) {
      deleteMutation.mutate(alertToDelete.id)
    }
  }, [alertToDelete, deleteMutation])

  const handleToggleAlert = useCallback((alert: Alert) => {
    toggleMutation.mutate(alert.id)
  }, [toggleMutation])

  const handleResetAlert = useCallback((alert: Alert) => {
    resetMutation.mutate(alert.id)
  }, [resetMutation])

  const handleStockClick = useCallback((symbol: string) => {
    navigate(`/stock/${symbol}`)
  }, [navigate])

  const getStatusBadge = (status: AlertStatus) => {
    switch (status) {
      case 'ACTIVE':
        return (
          <span className="inline-flex items-center rounded-full bg-stock-up/10 px-2 py-0.5 text-xs font-medium text-stock-up">
            <Bell className="mr-1 h-3 w-3" />
            Active
          </span>
        )
      case 'TRIGGERED':
        return (
          <span className="inline-flex items-center rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
            <Bell className="mr-1 h-3 w-3" />
            Triggered
          </span>
        )
      case 'DISABLED':
        return (
          <span className="inline-flex items-center rounded-full bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground">
            <BellOff className="mr-1 h-3 w-3" />
            Disabled
          </span>
        )
    }
  }

  const formatThreshold = (alert: Alert) => {
    if (alert.conditionType === 'PERCENT_CHANGE_UP' || alert.conditionType === 'PERCENT_CHANGE_DOWN') {
      return formatPercent(alert.threshold)
    }
    return formatCurrency(alert.threshold)
  }

  // Calculate stats
  const activeCount = filteredAlerts?.filter((a) => a.status === 'ACTIVE').length ?? 0
  const triggeredCount = filteredAlerts?.filter((a) => a.status === 'TRIGGERED').length ?? 0
  const disabledCount = filteredAlerts?.filter((a) => a.status === 'DISABLED').length ?? 0

  if (isLoading) {
    return (
      <div className={cn('flex items-center justify-center p-8', className)}>
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (error) {
    return (
      <div className={cn('flex flex-col items-center justify-center gap-2 p-8', className)}>
        <AlertCircle className="h-8 w-8 text-destructive" />
        <p className="text-sm text-muted-foreground">Failed to load alerts</p>
        <Button
          variant="outline"
          size="sm"
          onClick={() => queryClient.invalidateQueries({ queryKey: ['alerts'] })}
        >
          Try again
        </Button>
      </div>
    )
  }

  return (
    <div className={cn('space-y-6', className)}>
      {/* Header with create button */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">Price Alerts</h2>
          <p className="text-sm text-muted-foreground">
            {filterSymbol
              ? `Alerts for ${filterSymbol}`
              : 'Get notified when stocks reach your target'}
          </p>
        </div>
        <Button onClick={() => setIsCreateDialogOpen(true)}>
          <Plus className="mr-2 h-4 w-4" />
          Create Alert
        </Button>
      </div>

      {/* Stats cards */}
      {!filterSymbol && (
        <div className="grid gap-4 md:grid-cols-3">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Active Alerts</CardTitle>
              <Bell className="h-4 w-4 text-stock-up" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{activeCount}</div>
              <p className="text-xs text-muted-foreground">Currently monitoring</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Triggered</CardTitle>
              <Bell className="h-4 w-4 text-primary" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{triggeredCount}</div>
              <p className="text-xs text-muted-foreground">Alerts triggered</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Disabled</CardTitle>
              <BellOff className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{disabledCount}</div>
              <p className="text-xs text-muted-foreground">Paused alerts</p>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Alerts list */}
      <Card>
        <CardHeader>
          <CardTitle>All Alerts</CardTitle>
          <CardDescription>Manage your price alerts</CardDescription>
        </CardHeader>
        <CardContent>
          {filteredAlerts && filteredAlerts.length > 0 ? (
            <ScrollArea className="h-[400px]">
              <div className="space-y-2">
                {filteredAlerts.map((alert) => {
                  const ConditionIcon = CONDITION_ICONS[alert.conditionType]
                  return (
                    <div
                      key={alert.id}
                      className="flex items-center justify-between rounded-lg border p-4 hover:bg-accent/50 transition-colors"
                    >
                      <div className="flex items-center gap-4">
                        <div
                          className="cursor-pointer"
                          onClick={() => handleStockClick(alert.symbol)}
                        >
                          <div className="flex items-center gap-2">
                            <span className="font-medium text-lg">{alert.symbol}</span>
                            {getStatusBadge(alert.status)}
                          </div>
                          <div className="flex items-center gap-2 text-sm text-muted-foreground mt-1">
                            <ConditionIcon className="h-4 w-4" />
                            <span>
                              {CONDITION_LABELS[alert.conditionType]} {formatThreshold(alert)}
                            </span>
                          </div>
                          {alert.triggeredAt && (
                            <p className="text-xs text-muted-foreground mt-1">
                              Triggered {formatRelativeTime(alert.triggeredAt)}
                            </p>
                          )}
                        </div>
                      </div>

                      <div className="flex items-center gap-2">
                        {alert.status === 'TRIGGERED' && (
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => handleResetAlert(alert)}
                            disabled={resetMutation.isPending}
                          >
                            <RefreshCw className="mr-2 h-4 w-4" />
                            Reset
                          </Button>
                        )}
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleToggleAlert(alert)}
                          disabled={toggleMutation.isPending}
                        >
                          {alert.status === 'DISABLED' ? (
                            <Bell className="h-4 w-4" />
                          ) : (
                            <BellOff className="h-4 w-4" />
                          )}
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleDeleteAlert(alert)}
                          className="text-destructive hover:text-destructive"
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    </div>
                  )
                })}
              </div>
            </ScrollArea>
          ) : (
            <div className="flex h-[300px] items-center justify-center text-muted-foreground">
              <div className="text-center">
                <Bell className="mx-auto mb-4 h-12 w-12 text-muted-foreground/50" />
                <p className="mb-4">No alerts configured</p>
                <Button variant="outline" onClick={() => setIsCreateDialogOpen(true)}>
                  <Plus className="mr-2 h-4 w-4" />
                  Create your first alert
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Create alert dialog */}
      <Dialog open={isCreateDialogOpen} onOpenChange={setIsCreateDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Create Price Alert</DialogTitle>
            <DialogDescription>
              Set up a new price alert to get notified when conditions are met.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 py-4">
            <div className="grid gap-2">
              <Label htmlFor="alert-symbol">Stock Symbol</Label>
              <Input
                id="alert-symbol"
                value={alertForm.symbol}
                onChange={(e) =>
                  setAlertForm((prev) => ({
                    ...prev,
                    symbol: e.target.value.toUpperCase(),
                  }))
                }
                placeholder="AAPL"
                disabled={!!filterSymbol}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="condition">Condition</Label>
              <Select
                value={alertForm.conditionType}
                onValueChange={(value: AlertCondition) =>
                  setAlertForm((prev) => ({ ...prev, conditionType: value }))
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="ABOVE">
                    <div className="flex items-center gap-2">
                      <ArrowUp className="h-4 w-4" />
                      Price goes above
                    </div>
                  </SelectItem>
                  <SelectItem value="BELOW">
                    <div className="flex items-center gap-2">
                      <ArrowDown className="h-4 w-4" />
                      Price goes below
                    </div>
                  </SelectItem>
                  <SelectItem value="PERCENT_CHANGE_UP">
                    <div className="flex items-center gap-2">
                      <TrendingUp className="h-4 w-4" />
                      Price increases by %
                    </div>
                  </SelectItem>
                  <SelectItem value="PERCENT_CHANGE_DOWN">
                    <div className="flex items-center gap-2">
                      <TrendingDown className="h-4 w-4" />
                      Price decreases by %
                    </div>
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="threshold">
                {alertForm.conditionType === 'PERCENT_CHANGE_UP' ||
                alertForm.conditionType === 'PERCENT_CHANGE_DOWN'
                  ? 'Percentage'
                  : 'Price'}
              </Label>
              <div className="relative">
                {alertForm.conditionType !== 'PERCENT_CHANGE_UP' &&
                  alertForm.conditionType !== 'PERCENT_CHANGE_DOWN' && (
                    <span className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground">
                      $
                    </span>
                  )}
                <Input
                  id="threshold"
                  type="number"
                  value={alertForm.threshold || ''}
                  onChange={(e) =>
                    setAlertForm((prev) => ({
                      ...prev,
                      threshold: parseFloat(e.target.value) || 0,
                    }))
                  }
                  placeholder={
                    alertForm.conditionType === 'PERCENT_CHANGE_UP' ||
                    alertForm.conditionType === 'PERCENT_CHANGE_DOWN'
                      ? '5'
                      : '150.00'
                  }
                  className={cn(
                    alertForm.conditionType !== 'PERCENT_CHANGE_UP' &&
                      alertForm.conditionType !== 'PERCENT_CHANGE_DOWN' &&
                      'pl-7'
                  )}
                  min="0"
                  step={
                    alertForm.conditionType === 'PERCENT_CHANGE_UP' ||
                    alertForm.conditionType === 'PERCENT_CHANGE_DOWN'
                      ? '0.1'
                      : '0.01'
                  }
                />
                {(alertForm.conditionType === 'PERCENT_CHANGE_UP' ||
                  alertForm.conditionType === 'PERCENT_CHANGE_DOWN') && (
                  <span className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground">
                    %
                  </span>
                )}
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setIsCreateDialogOpen(false)
                setAlertForm({
                  symbol: filterSymbol ?? '',
                  conditionType: 'ABOVE',
                  threshold: 0,
                })
              }}
            >
              Cancel
            </Button>
            <Button
              onClick={handleCreateAlert}
              disabled={createMutation.isPending}
            >
              {createMutation.isPending && (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              )}
              Create Alert
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete alert dialog */}
      <Dialog open={isDeleteDialogOpen} onOpenChange={setIsDeleteDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete Alert</DialogTitle>
            <DialogDescription>
              Are you sure you want to delete this alert for {alertToDelete?.symbol}? This
              action cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setIsDeleteDialogOpen(false)
                setAlertToDelete(null)
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
    </div>
  )
}
