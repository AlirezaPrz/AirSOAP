import { createContext, useContext } from 'react'

export interface ToastMessage {
  /** One short sentence saying what just happened. */
  text: string
  tone?: 'success' | 'info'
}

export const ToastContext = createContext<(message: ToastMessage) => void>(() => {})

/** Confirms an action that finished without needing a dialog. Never put secrets or patient data in a toast. */
export const useToast = () => useContext(ToastContext)
