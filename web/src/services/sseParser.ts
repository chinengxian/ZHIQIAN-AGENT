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

  if (!['message', 'done', 'error', 'status', 'sources'].includes(eventName)) {
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
  if (eventName === 'status' && isRecord(data) && (data.stage === 'retrieving' || data.stage === 'generating')) {
    return { event: 'status', data: { stage: data.stage } }
  }
  if (eventName === 'sources' && isRecord(data) && Array.isArray(data.items) && data.items.every(isSource)) {
    return { event: 'sources', data: { items: data.items } }
  }
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

function isSource(value: unknown): value is Extract<ChatEvent, { event: 'sources' }>['data']['items'][number] {
  return isRecord(value)
    && typeof value.citation_id === 'string'
    && typeof value.chunk_id === 'string'
    && typeof value.document_id === 'string'
    && typeof value.document_version_id === 'string'
    && typeof value.title === 'string'
    && typeof value.filename === 'string'
    && (value.page_start === null || typeof value.page_start === 'number')
    && (value.page_end === null || typeof value.page_end === 'number')
    && Array.isArray(value.heading_path) && value.heading_path.every((item) => typeof item === 'string')
    && typeof value.excerpt === 'string'
    && typeof value.score === 'number'
    && typeof value.rank === 'number'
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
