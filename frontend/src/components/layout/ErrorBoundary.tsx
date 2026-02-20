import { Component, type ErrorInfo, type ReactNode } from 'react'
import { AlertCircle } from 'lucide-react'
import i18n from '@/i18n'
import { Button } from '@/components/ui/button'

interface ErrorBoundaryProps {
  children: ReactNode
}

interface ErrorBoundaryState {
  hasError: boolean
  error: Error | null
  isChunkError: boolean
}

function isChunkLoadError(error: Error): boolean {
  return (
    error.name === 'ChunkLoadError' ||
    error.message.includes('Failed to fetch dynamically imported module') ||
    error.message.includes('Importing a module script failed') ||
    error.message.includes('Unable to preload CSS')
  )
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props)
    this.state = {
      hasError: false,
      error: null,
      isChunkError: false,
    }
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return {
      hasError: true,
      error,
      isChunkError: isChunkLoadError(error),
    }
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    console.error('[ErrorBoundary] Caught error:', error, errorInfo)
  }

  private handleRetry = (): void => {
    this.setState({ hasError: false, error: null, isChunkError: false })
  }

  private handleReload = (): void => {
    window.location.reload()
  }

  override render(): ReactNode {
    if (!this.state.hasError) {
      return this.props.children
    }

    const t = i18n.t.bind(i18n)

    if (this.state.isChunkError) {
      return (
        <div role="alert" className="flex h-full items-center justify-center p-4">
          <div className="flex max-w-md flex-col items-center gap-4 text-center">
            <AlertCircle aria-hidden="true" className="h-12 w-12 text-destructive" />
            <h2 className="text-lg font-semibold">
              {t('common:errors.chunkLoadError')}
            </h2>
            <p className="text-sm text-muted-foreground">
              {t('common:errors.chunkLoadErrorDescription')}
            </p>
            <Button onClick={this.handleReload}>
              {t('common:errors.reloadPage')}
            </Button>
          </div>
        </div>
      )
    }

    return (
      <div role="alert" className="flex h-full items-center justify-center p-4">
        <div className="flex max-w-md flex-col items-center gap-4 text-center">
          <AlertCircle aria-hidden="true" className="h-12 w-12 text-destructive" />
          <h2 className="text-lg font-semibold">
            {t('common:errors.renderError')}
          </h2>
          <p className="text-sm text-muted-foreground">
            {t('common:errors.renderErrorDescription')}
          </p>
          <div className="flex gap-3">
            <Button variant="outline" onClick={this.handleRetry}>
              {t('common:actions.retry')}
            </Button>
            <Button onClick={this.handleReload}>
              {t('common:errors.reloadPage')}
            </Button>
          </div>
          {import.meta.env.DEV && this.state.error && (
            <details className="mt-4 w-full rounded-md border p-4 text-left">
              <summary className="cursor-pointer text-sm font-medium text-muted-foreground">
                Error Details
              </summary>
              <pre className="mt-2 overflow-auto whitespace-pre-wrap text-xs text-destructive">
                {this.state.error.name}: {this.state.error.message}
              </pre>
            </details>
          )}
        </div>
      </div>
    )
  }
}
