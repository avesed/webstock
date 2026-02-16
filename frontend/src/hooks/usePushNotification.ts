import { useState, useEffect, useCallback } from 'react'
import { alertsApi } from '@/api'

/**
 * Convert a URL-safe base64 string to a Uint8Array.
 * Required for the applicationServerKey in pushManager.subscribe().
 */
function urlBase64ToUint8Array(base64String: string): Uint8Array<ArrayBuffer> {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4)
  const base64 = (base64String + padding)
    .replace(/-/g, '+')
    .replace(/_/g, '/')

  const rawData = window.atob(base64)
  const buffer = new ArrayBuffer(rawData.length)
  const outputArray = new Uint8Array(buffer)

  for (let i = 0; i < rawData.length; ++i) {
    outputArray[i] = rawData.charCodeAt(i)
  }
  return outputArray
}

interface UsePushNotificationReturn {
  isSupported: boolean
  isSubscribed: boolean
  permission: NotificationPermission | 'default'
  subscribe: () => Promise<void>
  unsubscribe: () => Promise<void>
  isLoading: boolean
}

export function usePushNotification(): UsePushNotificationReturn {
  const isSupported =
    typeof window !== 'undefined' &&
    'serviceWorker' in navigator &&
    'PushManager' in window &&
    'Notification' in window

  const [isSubscribed, setIsSubscribed] = useState(false)
  const [permission, setPermission] = useState<NotificationPermission | 'default'>('default')
  const [isLoading, setIsLoading] = useState(false)

  // Check current subscription state on mount
  useEffect(() => {
    if (!isSupported) return

    setPermission(Notification.permission)

    const checkSubscription = async () => {
      try {
        const registration = await navigator.serviceWorker.ready
        const subscription = await registration.pushManager.getSubscription()
        setIsSubscribed(subscription !== null)
      } catch (err) {
        console.warn('[Push] Failed to check subscription state:', err)
      }
    }

    void checkSubscription()
  }, [isSupported])

  const subscribe = useCallback(async () => {
    if (!isSupported) return

    setIsLoading(true)
    try {
      // Request notification permission
      const result = await Notification.requestPermission()
      setPermission(result)

      if (result !== 'granted') {
        return
      }

      // Get VAPID key from backend
      const vapidKey = await alertsApi.getVapidKey()
      const applicationServerKey = urlBase64ToUint8Array(vapidKey)

      // Subscribe to push via browser Push API
      const registration = await navigator.serviceWorker.ready
      const subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey,
      })

      // Send subscription to backend; rollback browser subscription on failure
      try {
        await alertsApi.subscribePush(subscription.toJSON())
      } catch (backendErr) {
        console.error('[Push] Backend subscription failed, rolling back browser subscription:', backendErr)
        await subscription.unsubscribe()
        throw backendErr
      }
      setIsSubscribed(true)
    } catch (err) {
      console.error('[Push] Subscribe failed:', err)
    } finally {
      setIsLoading(false)
    }
  }, [isSupported])

  const unsubscribe = useCallback(async () => {
    if (!isSupported) return

    setIsLoading(true)
    try {
      const registration = await navigator.serviceWorker.ready
      const subscription = await registration.pushManager.getSubscription()

      if (subscription) {
        const endpoint = subscription.endpoint
        await subscription.unsubscribe()
        await alertsApi.unsubscribePush(endpoint)
      }

      setIsSubscribed(false)
    } catch (err) {
      console.error('[Push] Unsubscribe failed:', err)
    } finally {
      setIsLoading(false)
    }
  }, [isSupported])

  return {
    isSupported,
    isSubscribed,
    permission,
    subscribe,
    unsubscribe,
    isLoading,
  }
}
