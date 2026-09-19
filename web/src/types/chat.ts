export type MessageStatus = 'streaming' | 'complete' | 'stopped' | 'error'

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  status: MessageStatus
}

export type ChatEvent =
  | { event: 'message'; data: { content: string } }
  | { event: 'done'; data: Record<string, never> }
  | { event: 'error'; data: { code: string; message: string } }
