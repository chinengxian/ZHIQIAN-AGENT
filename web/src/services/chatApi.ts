import { createSseParser } from './sseParser'

interface ChatHandlers {
  onMessage?: (content: string) => void
  onDone?: () => void
}

export class ChatStreamError extends Error {
  constructor(message: string, public readonly code: string) {
    super(message)
    this.name = 'ChatStreamError'
  }
}

async function errorDetails(response: Response): Promise<{ message: string; code: string }> {
  try {
    const body = await response.json() as { detail?: unknown }
    if (typeof body.detail === 'string' && body.detail.trim()) {
      return { message: body.detail, code: 'http_error' }
    }
    if (
      typeof body.detail === 'object'
      && body.detail !== null
      && 'message' in body.detail
      && typeof body.detail.message === 'string'
      && 'code' in body.detail
      && typeof body.detail.code === 'string'
    ) {
      return { message: body.detail.message, code: body.detail.code }
    }
  } catch {
    // Fall through to a stable, user-safe message.
  }
  return { message: '服务暂时不可用，请稍后重试', code: 'http_error' }
}

export async function streamChat(
  conversationId: string,
  message: string,
  handlers: ChatHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch('/api/v1/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ conversation_id: conversationId, message }),
    ...(signal ? { signal } : {}),
  })

  if (!response.ok) {
    const detail = await errorDetails(response)
    throw new ChatStreamError(detail.message, detail.code)
  }
  if (!response.body) throw new ChatStreamError('服务未返回可读取的数据流', 'empty_stream')

  let completed = false
  const parser = createSseParser((event) => {
    if (event.event === 'message') handlers.onMessage?.(event.data.content)
    if (event.event === 'done') {
      completed = true
      handlers.onDone?.()
    }
    if (event.event === 'error') {
      throw new ChatStreamError(event.data.message, event.data.code)
    }
  })
  const decoder = new TextDecoder()
  const reader = response.body.getReader()

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    parser.push(decoder.decode(value, { stream: true }))
  }
  parser.push(decoder.decode())
  parser.finish()
  if (!completed) throw new ChatStreamError('SSE 流意外中断', 'incomplete_stream')
}
