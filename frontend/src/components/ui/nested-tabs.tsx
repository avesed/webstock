import * as React from 'react'
import * as TabsPrimitive from '@radix-ui/react-tabs'

import { cn } from '@/lib/utils'

/**
 * Primary Tabs - Main navigation level with larger, more prominent styling.
 * Used for top-level tab navigation (e.g., Traditional | AI).
 */
const PrimaryTabsList = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.List>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.List>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.List
    ref={ref}
    className={cn(
      'flex h-12 w-full items-center gap-1 overflow-x-auto border-b bg-transparent px-1',
      // Mobile scrolling support
      'scrollbar-none snap-x snap-mandatory',
      className
    )}
    {...props}
  />
))
PrimaryTabsList.displayName = 'PrimaryTabsList'

const PrimaryTabsTrigger = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.Trigger>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Trigger>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.Trigger
    ref={ref}
    className={cn(
      'inline-flex h-10 items-center justify-center whitespace-nowrap px-4 text-base font-semibold',
      'text-muted-foreground transition-colors',
      'border-b-2 border-transparent -mb-px',
      'hover:text-foreground',
      'data-[state=active]:border-primary data-[state=active]:text-foreground',
      'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2',
      'disabled:pointer-events-none disabled:opacity-50',
      'snap-start',
      className
    )}
    {...props}
  />
))
PrimaryTabsTrigger.displayName = 'PrimaryTabsTrigger'

/**
 * Secondary Tabs - Sub-navigation level with lighter, more subtle styling.
 * Used for nested tab navigation within a primary tab (e.g., Financials | News).
 */
const SecondaryTabsList = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.List>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.List>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.List
    ref={ref}
    className={cn(
      'flex h-9 w-full items-center gap-4 overflow-x-auto border-b bg-transparent',
      // Mobile scrolling support
      'scrollbar-none',
      className
    )}
    {...props}
  />
))
SecondaryTabsList.displayName = 'SecondaryTabsList'

const SecondaryTabsTrigger = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.Trigger>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Trigger>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.Trigger
    ref={ref}
    className={cn(
      'inline-flex h-8 items-center justify-center whitespace-nowrap text-sm font-medium',
      'text-muted-foreground transition-colors',
      'border-b border-transparent -mb-px',
      'hover:text-foreground',
      'data-[state=active]:border-primary data-[state=active]:text-foreground',
      'focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring',
      'disabled:pointer-events-none disabled:opacity-50',
      className
    )}
    {...props}
  />
))
SecondaryTabsTrigger.displayName = 'SecondaryTabsTrigger'

// Re-export base components for convenience
export { Tabs, TabsContent } from './tabs'

export { PrimaryTabsList, PrimaryTabsTrigger, SecondaryTabsList, SecondaryTabsTrigger }
