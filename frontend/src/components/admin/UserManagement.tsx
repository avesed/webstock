import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import {
  Search,
  MoreHorizontal,
  UserCheck,
  UserX,
  Shield,
  ShieldOff,
  Key,
  Loader2,
  ChevronLeft,
  ChevronRight,
  CheckCircle2,
  XCircle,
  Clock,
  UserPlus,
  AlertTriangle,
} from 'lucide-react'

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Label } from '@/components/ui/label'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Separator } from '@/components/ui/separator'
import { useToast } from '@/hooks'
import { adminApi } from '@/api/admin'
import { cn } from '@/lib/utils'
import type { AdminUser, UserFilters, UserRole, SystemConfig } from '@/types'

interface ToggleSwitchProps {
  checked: boolean
  onCheckedChange: (checked: boolean) => void
  disabled?: boolean
}

function ToggleSwitch({ checked, onCheckedChange, disabled }: ToggleSwitchProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onCheckedChange(!checked)}
      className={cn(
        'relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2',
        'disabled:cursor-not-allowed disabled:opacity-50',
        checked ? 'bg-primary' : 'bg-input'
      )}
    >
      <span
        className={cn(
          'pointer-events-none inline-block h-5 w-5 transform rounded-full bg-background shadow-lg ring-0 transition-transform',
          checked ? 'translate-x-5' : 'translate-x-0'
        )}
      />
    </button>
  )
}

export function UserManagement() {
  const { t } = useTranslation('admin')
  const { t: tCommon } = useTranslation('common')
  const queryClient = useQueryClient()
  const { toast } = useToast()

  // State
  const [page, setPage] = useState(1)
  const [pageSize] = useState(10)
  const [filters, setFilters] = useState<UserFilters>({
    search: '',
    role: 'all',
    status: 'all',
  })
  const [searchInput, setSearchInput] = useState('')
  const [confirmDialog, setConfirmDialog] = useState<{
    open: boolean
    type: 'status' | 'role' | 'password' | 'api' | 'approve' | 'reject' | null
    user: AdminUser | null
    newValue?: boolean | string
  }>({ open: false, type: null, user: null })
  const [temporaryPassword, setTemporaryPassword] = useState<string | null>(null)
  const [rejectReason, setRejectReason] = useState('')
  const [addUserDialog, setAddUserDialog] = useState(false)
  const [newUserEmail, setNewUserEmail] = useState('')
  const [newUserPassword, setNewUserPassword] = useState('')
  const [newUserRole, setNewUserRole] = useState<UserRole>('user')

  // Query for system config (approval toggle)
  const { data: config } = useQuery({
    queryKey: ['admin-system-config'],
    queryFn: adminApi.getSystemConfig,
  })

  // Query for users
  const { data, isLoading, error } = useQuery({
    queryKey: ['admin-users', page, pageSize, filters],
    queryFn: () => adminApi.getUsers(page, pageSize, filters),
  })

  // Mutations
  const statusMutation = useMutation({
    mutationFn: ({ userId, isActive }: { userId: number; isActive: boolean }) =>
      adminApi.updateUserStatus(userId, isActive),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin-users'] })
      toast({ title: t('users.statusUpdated') })
      setConfirmDialog({ open: false, type: null, user: null })
    },
    onError: () => {
      toast({ title: tCommon('status.error'), variant: 'destructive' })
    },
  })

  const roleMutation = useMutation({
    mutationFn: ({ userId, role }: { userId: number; role: UserRole }) =>
      adminApi.updateUserRole(userId, role),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin-users'] })
      toast({ title: t('users.roleUpdated') })
      setConfirmDialog({ open: false, type: null, user: null })
    },
    onError: () => {
      toast({ title: tCommon('status.error'), variant: 'destructive' })
    },
  })

  const passwordMutation = useMutation({
    mutationFn: (userId: number) => adminApi.resetUserPassword(userId),
    onSuccess: (data) => {
      setTemporaryPassword(data.temporaryPassword)
      toast({ title: t('users.passwordReset') })
    },
    onError: () => {
      toast({ title: tCommon('status.error'), variant: 'destructive' })
    },
  })

  const approveMutation = useMutation({
    mutationFn: (userId: number) => adminApi.approveUser(userId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin-users'] })
      toast({ title: t('users.userApproved') })
      setConfirmDialog({ open: false, type: null, user: null })
    },
    onError: () => {
      toast({ title: tCommon('status.error'), variant: 'destructive' })
    },
  })

  const rejectMutation = useMutation({
    mutationFn: ({ userId, reason }: { userId: number; reason?: string }) =>
      adminApi.rejectUser(userId, reason),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin-users'] })
      toast({ title: t('users.userRejected') })
      setConfirmDialog({ open: false, type: null, user: null })
      setRejectReason('')
    },
    onError: () => {
      toast({ title: tCommon('status.error'), variant: 'destructive' })
    },
  })

  const configMutation = useMutation({
    mutationFn: (newConfig: Partial<SystemConfig>) => adminApi.updateSystemConfig(newConfig),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin-system-config'] })
      toast({ title: t('settings.saved') })
    },
    onError: () => {
      toast({ title: tCommon('status.error'), variant: 'destructive' })
    },
  })

  const createUserMutation = useMutation({
    mutationFn: (data: { email: string; password: string; role: UserRole }) =>
      adminApi.createUser(data.email, data.password, data.role),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin-users'] })
      toast({ title: t('users.userCreated') })
      setAddUserDialog(false)
      setNewUserEmail('')
      setNewUserPassword('')
      setNewUserRole('user')
    },
    onError: () => {
      toast({ title: tCommon('status.error'), variant: 'destructive' })
    },
  })

  // Handlers
  const handleSearch = () => {
    setFilters((prev) => ({ ...prev, search: searchInput }))
    setPage(1)
  }

  const handleFilterChange = (key: keyof UserFilters, value: string) => {
    setFilters((prev) => ({ ...prev, [key]: value }))
    setPage(1)
  }

  const handleConfirmAction = () => {
    if (!confirmDialog.user) return

    switch (confirmDialog.type) {
      case 'status':
        statusMutation.mutate({
          userId: confirmDialog.user.id,
          isActive: confirmDialog.newValue as boolean,
        })
        break
      case 'role':
        roleMutation.mutate({
          userId: confirmDialog.user.id,
          role: confirmDialog.newValue as UserRole,
        })
        break
      case 'password':
        passwordMutation.mutate(confirmDialog.user.id)
        break
      case 'approve':
        approveMutation.mutate(confirmDialog.user.id)
        break
      case 'reject':
        rejectMutation.mutate({
          userId: confirmDialog.user.id,
          ...(rejectReason ? { reason: rejectReason } : {}),
        })
        break
    }
  }

  const formatDate = (dateString: string | null) => {
    if (!dateString) return '-'
    return new Date(dateString).toLocaleDateString()
  }

  const handleApprovalToggle = (checked: boolean) => {
    if (!config) return
    configMutation.mutate({
      features: {
        ...config.features,
        requireRegistrationApproval: checked,
      },
    })
  }

  const handleCreateUser = () => {
    if (!newUserEmail || !newUserPassword) return
    createUserMutation.mutate({
      email: newUserEmail,
      password: newUserPassword,
      role: newUserRole,
    })
  }

  if (error) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center h-64">
          <p className="text-destructive">{tCommon('status.error')}</p>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-4">
        <div>
          <CardTitle>{t('users.title')}</CardTitle>
          <CardDescription>{t('users.description')}</CardDescription>
        </div>
        <Button onClick={() => setAddUserDialog(true)}>
          <UserPlus className="mr-2 h-4 w-4" />
          {t('users.addUser')}
        </Button>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Approval Toggle */}
        <div className="flex items-center justify-between rounded-lg border p-4">
          <div className="space-y-0.5">
            <Label className="text-base">{t('settings.requireApproval')}</Label>
            <p className="text-sm text-muted-foreground">{t('settings.requireApprovalDescription')}</p>
            {config?.features.requireRegistrationApproval && !config.features.requireRegistrationApproval && (
              <div className="flex items-center gap-2 mt-2 text-amber-600 dark:text-amber-400 text-xs">
                <AlertTriangle className="h-3 w-3" />
                <span>{t('settings.disableApprovalWarning')}</span>
              </div>
            )}
          </div>
          <ToggleSwitch
            checked={config?.features.requireRegistrationApproval ?? false}
            onCheckedChange={handleApprovalToggle}
            disabled={configMutation.isPending}
          />
        </div>

        <Separator />

        {/* Filters */}
        <div className="flex flex-col gap-4 sm:flex-row">
          <div className="flex flex-1 gap-2">
            <Input
              placeholder={t('users.searchPlaceholder')}
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
              className="max-w-sm"
            />
            <Button variant="outline" size="icon" onClick={handleSearch}>
              <Search className="h-4 w-4" />
            </Button>
          </div>

          <div className="flex gap-2">
            <Select value={filters.role} onValueChange={(v) => handleFilterChange('role', v)}>
              <SelectTrigger className="w-32">
                <SelectValue placeholder={t('users.filterRole')} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">{t('users.allRoles')}</SelectItem>
                <SelectItem value="user">{t('users.roleUser')}</SelectItem>
                <SelectItem value="admin">{t('users.roleAdmin')}</SelectItem>
              </SelectContent>
            </Select>

            <Select value={filters.status} onValueChange={(v) => handleFilterChange('status', v as 'all' | 'active' | 'inactive' | 'pending')}>
              <SelectTrigger className="w-36">
                <SelectValue placeholder={t('users.filterStatus')} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">{t('users.allStatus')}</SelectItem>
                <SelectItem value="active">{tCommon('status.active')}</SelectItem>
                <SelectItem value="inactive">{tCommon('status.inactive')}</SelectItem>
                <SelectItem value="pending">{t('users.statusPending')}</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>

        {/* Table */}
        {isLoading ? (
          <div className="flex items-center justify-center h-64">
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <div className="rounded-md border">
            <table className="w-full">
              <thead>
                <tr className="border-b bg-muted/50">
                  <th className="h-10 px-4 text-left font-medium">{t('users.email')}</th>
                  <th className="h-10 px-4 text-left font-medium">{t('users.role')}</th>
                  <th className="h-10 px-4 text-left font-medium">{t('users.status')}</th>
                  <th className="h-10 px-4 text-left font-medium">{t('users.createdAt')}</th>
                  <th className="h-10 px-4 text-left font-medium">{t('users.lastLogin')}</th>
                  <th className="h-10 px-4 text-right font-medium">{tCommon('actions.edit')}</th>
                </tr>
              </thead>
              <tbody>
                {data?.users.map((user) => (
                  <tr key={user.id} className="border-b">
                    <td className="px-4 py-3">{user.email}</td>
                    <td className="px-4 py-3">
                      <span
                        className={cn(
                          'inline-flex items-center rounded-full px-2 py-1 text-xs font-medium',
                          user.role === 'admin'
                            ? 'bg-purple-100 text-purple-700 dark:bg-purple-900 dark:text-purple-300'
                            : 'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300'
                        )}
                      >
                        {user.role === 'admin' ? t('users.roleAdmin') : t('users.roleUser')}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <span
                        className={cn(
                          'inline-flex items-center gap-1 rounded-full px-2 py-1 text-xs font-medium',
                          user.accountStatus === 'pending_approval'
                            ? 'bg-amber-100 text-amber-700 dark:bg-amber-900 dark:text-amber-300'
                            : user.isActive
                            ? 'bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300'
                            : 'bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300'
                        )}
                        aria-label={
                          user.accountStatus === 'pending_approval'
                            ? t('users.statusPending')
                            : user.isActive
                            ? tCommon('status.active')
                            : tCommon('status.inactive')
                        }
                      >
                        {user.accountStatus === 'pending_approval' && (
                          <Clock className="h-3 w-3" />
                        )}
                        {user.accountStatus === 'pending_approval'
                          ? t('users.statusPending')
                          : user.isActive
                          ? tCommon('status.active')
                          : tCommon('status.inactive')}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-muted-foreground">{formatDate(user.createdAt)}</td>
                    <td className="px-4 py-3 text-muted-foreground">{formatDate(user.lastLoginAt)}</td>
                    <td className="px-4 py-3 text-right">
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button variant="ghost" size="icon">
                            <MoreHorizontal className="h-4 w-4" />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuLabel>{tCommon('actions.edit')}</DropdownMenuLabel>
                          <DropdownMenuSeparator />

                          {/* Approve/Reject options for pending users */}
                          {user.accountStatus === 'pending_approval' && (
                            <>
                              <DropdownMenuItem
                                onClick={() =>
                                  setConfirmDialog({
                                    open: true,
                                    type: 'approve',
                                    user,
                                  })
                                }
                              >
                                <CheckCircle2 className="mr-2 h-4 w-4 text-green-600" />
                                {t('users.approve')}
                              </DropdownMenuItem>
                              <DropdownMenuItem
                                onClick={() =>
                                  setConfirmDialog({
                                    open: true,
                                    type: 'reject',
                                    user,
                                  })
                                }
                              >
                                <XCircle className="mr-2 h-4 w-4 text-red-600" />
                                {t('users.reject')}
                              </DropdownMenuItem>
                              <DropdownMenuSeparator />
                            </>
                          )}

                          {/* Status toggle - only for non-pending users */}
                          {user.accountStatus !== 'pending_approval' && (
                            <DropdownMenuItem
                              onClick={() =>
                                setConfirmDialog({
                                  open: true,
                                  type: 'status',
                                  user,
                                  newValue: !user.isActive,
                                })
                              }
                            >
                              {user.isActive ? (
                                <>
                                  <UserX className="mr-2 h-4 w-4" />
                                  {t('users.disable')}
                                </>
                              ) : (
                                <>
                                  <UserCheck className="mr-2 h-4 w-4" />
                                  {t('users.enable')}
                                </>
                              )}
                            </DropdownMenuItem>
                          )}

                          <DropdownMenuItem
                            onClick={() =>
                              setConfirmDialog({
                                open: true,
                                type: 'role',
                                user,
                                newValue: user.role === 'admin' ? 'user' : 'admin',
                              })
                            }
                          >
                            {user.role === 'admin' ? (
                              <>
                                <ShieldOff className="mr-2 h-4 w-4" />
                                {t('users.demote')}
                              </>
                            ) : (
                              <>
                                <Shield className="mr-2 h-4 w-4" />
                                {t('users.promote')}
                              </>
                            )}
                          </DropdownMenuItem>
                          <DropdownMenuSeparator />
                          <DropdownMenuItem
                            onClick={() =>
                              setConfirmDialog({
                                open: true,
                                type: 'password',
                                user,
                              })
                            }
                          >
                            <Key className="mr-2 h-4 w-4" />
                            {t('users.resetPassword')}
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </td>
                  </tr>
                ))}
                {(!data?.users || data.users.length === 0) && (
                  <tr>
                    <td colSpan={6} className="px-4 py-8 text-center text-muted-foreground">
                      {tCommon('empty.description')}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}

        {/* Pagination */}
        {data && data.totalPages > 1 && (
          <div className="flex items-center justify-between">
            <p className="text-sm text-muted-foreground">
              {tCommon('pagination.showing')} {(page - 1) * pageSize + 1} {tCommon('pagination.to')}{' '}
              {Math.min(page * pageSize, data.total)} {tCommon('pagination.of')} {data.total}{' '}
              {tCommon('pagination.entries')}
            </p>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="icon"
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page === 1}
              >
                <ChevronLeft className="h-4 w-4" />
              </Button>
              <span className="text-sm">
                {tCommon('pagination.page')} {page} {tCommon('pagination.of')} {data.totalPages}
              </span>
              <Button
                variant="outline"
                size="icon"
                onClick={() => setPage((p) => Math.min(data.totalPages, p + 1))}
                disabled={page === data.totalPages}
              >
                <ChevronRight className="h-4 w-4" />
              </Button>
            </div>
          </div>
        )}

        {/* Confirm Dialog */}
        <Dialog open={confirmDialog.open} onOpenChange={(open) => {
          if (!open) {
            setConfirmDialog({ open: false, type: null, user: null })
            setRejectReason('')
          }
        }}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>
                {confirmDialog.type === 'status' && t('users.confirmStatusTitle')}
                {confirmDialog.type === 'role' && t('users.confirmRoleTitle')}
                {confirmDialog.type === 'password' && t('users.confirmPasswordTitle')}
                {confirmDialog.type === 'approve' && t('users.confirmApproveTitle')}
                {confirmDialog.type === 'reject' && t('users.confirmRejectTitle')}
              </DialogTitle>
              <DialogDescription>
                {confirmDialog.type === 'status' &&
                  t('users.confirmStatusMessage', {
                    email: confirmDialog.user?.email,
                    action: confirmDialog.newValue ? t('users.enable') : t('users.disable'),
                  })}
                {confirmDialog.type === 'role' &&
                  t('users.confirmRoleMessage', {
                    email: confirmDialog.user?.email,
                    role: confirmDialog.newValue === 'admin' ? t('users.roleAdmin') : t('users.roleUser'),
                  })}
                {confirmDialog.type === 'password' &&
                  t('users.confirmPasswordMessage', { email: confirmDialog.user?.email })}
                {confirmDialog.type === 'approve' &&
                  t('users.confirmApproveMessage', { email: confirmDialog.user?.email })}
                {confirmDialog.type === 'reject' &&
                  t('users.confirmRejectMessage', { email: confirmDialog.user?.email })}
              </DialogDescription>
            </DialogHeader>

            {/* Reject reason input */}
            {confirmDialog.type === 'reject' && (
              <div className="space-y-2">
                <Label htmlFor="reject-reason">{t('users.rejectReason')}</Label>
                <Textarea
                  id="reject-reason"
                  value={rejectReason}
                  onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setRejectReason(e.target.value)}
                  placeholder={t('users.rejectReason')}
                  rows={3}
                />
              </div>
            )}

            <DialogFooter>
              <Button variant="outline" onClick={() => {
                setConfirmDialog({ open: false, type: null, user: null })
                setRejectReason('')
              }}>
                {tCommon('actions.cancel')}
              </Button>
              <Button
                onClick={handleConfirmAction}
                disabled={
                  statusMutation.isPending ||
                  roleMutation.isPending ||
                  passwordMutation.isPending ||
                  approveMutation.isPending ||
                  rejectMutation.isPending
                }
                variant={confirmDialog.type === 'reject' ? 'destructive' : 'default'}
              >
                {(statusMutation.isPending ||
                  roleMutation.isPending ||
                  passwordMutation.isPending ||
                  approveMutation.isPending ||
                  rejectMutation.isPending) && (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                )}
                {confirmDialog.type === 'approve' ? t('users.approve') :
                 confirmDialog.type === 'reject' ? t('users.reject') :
                 tCommon('actions.confirm')}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* Temporary Password Dialog */}
        <Dialog open={!!temporaryPassword} onOpenChange={() => setTemporaryPassword(null)}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>{t('users.temporaryPasswordTitle')}</DialogTitle>
              <DialogDescription>{t('users.temporaryPasswordMessage')}</DialogDescription>
            </DialogHeader>
            <div className="rounded-lg bg-muted p-4 font-mono text-lg text-center">{temporaryPassword}</div>
            <DialogFooter>
              <Button onClick={() => setTemporaryPassword(null)}>{tCommon('actions.close')}</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* Add User Dialog */}
        <Dialog open={addUserDialog} onOpenChange={(open) => {
          if (!open) {
            setAddUserDialog(false)
            setNewUserEmail('')
            setNewUserPassword('')
            setNewUserRole('user')
          }
        }}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>{t('users.addUser')}</DialogTitle>
              <DialogDescription>{t('users.addUserDescription')}</DialogDescription>
            </DialogHeader>
            <div className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="new-user-email">{t('users.email')}</Label>
                <Input
                  id="new-user-email"
                  type="email"
                  value={newUserEmail}
                  onChange={(e) => setNewUserEmail(e.target.value)}
                  placeholder="user@example.com"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="new-user-password">{t('users.password')}</Label>
                <Input
                  id="new-user-password"
                  type="password"
                  value={newUserPassword}
                  onChange={(e) => setNewUserPassword(e.target.value)}
                  placeholder="********"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="new-user-role">{t('users.role')}</Label>
                <Select value={newUserRole} onValueChange={(v) => setNewUserRole(v as UserRole)}>
                  <SelectTrigger id="new-user-role">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="user">{t('users.roleUser')}</SelectItem>
                    <SelectItem value="admin">{t('users.roleAdmin')}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => {
                setAddUserDialog(false)
                setNewUserEmail('')
                setNewUserPassword('')
                setNewUserRole('user')
              }}>
                {tCommon('actions.cancel')}
              </Button>
              <Button
                onClick={handleCreateUser}
                disabled={!newUserEmail || !newUserPassword || createUserMutation.isPending}
              >
                {createUserMutation.isPending && (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                )}
                {t('users.addUser')}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </CardContent>
    </Card>
  )
}
