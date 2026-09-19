import type { ChatEvent } from '../types/chat'

export interface SseParser {
  push(chunk: string): void
  finish(): void
}

type EventHandler = (event: ChatEvent) => void

function decodeFrame(frame: string): ChatEvent {
  let eventName = ''
  const dataLines: string[] = []

  for (const line of frame.split(/\r?\n/)) {
    if (line.startsWith('event:')) eventName = line.slice(6).trim()
    if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart())
  }

  if (!['message', 'done', 'error'].includes(eventName)) {
    throw new Error(`不支持的 SSE 事件：${eventName || '(空)'}`)
  }

  let data: unknown
  try {
    data = JSON.parse(dataLines.join('\n'))
  } catch {
    throw new Error('无效的 SSE 数据')
  }

  if (eventName === 'message' && isRecord(data) && typeof data.content === 'string') {
    return { event: 'message', data: { content: data.content } }
  }
  if (eventName === 'done' && isRecord(data)) return { event: 'done', data: {} }
  if (
    eventName === 'error'
    && isRecord(data)
    && typeof data.code === 'string'
    && typeof data.message === 'string'
  ) {
    return { event: 'error', data: { code: data.code, message: data.message } }
  }
  throw new Error('无效的 SSE 数据')
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function createSseParser(onEvent: EventHandler): SseParser {
  let buffer = ''

  function drain(): void {
    let boundary = /\r?\n\r?\n/.exec(buffer)
    while (boundary) {
      const frame = buffer.slice(0, boundary.index)
      buffer = buffer.slice(boundary.index + boundary[0].length)
      if (frame.trim()) onEvent(decodeFrame(frame))
      boundary = /\r?\n\r?\n/.exec(buffer)
    }
  }

  return {
    push(chunk) {
      buffer += chunk
      drain()
    },
    finish() {
      drain()
      if (buffer.trim()) throw new Error('SSE 流意外中断')
    },
  }
}
