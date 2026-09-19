import { describe, expect, it } from 'vitest'

import { createSseParser } from '../../src/services/sseParser'

describe('createSseParser', () => {
  it('parses events split across arbitrary chunks', () => {
    const events: unknown[] = []
    const parser = createSseParser((event) => events.push(event))

    parser.push('event: message\r\ndata: {"content":"你')
    parser.push('好"}\r\n\r\nevent: done\ndata: {}\n\n')
    parser.finish()

    expect(events).toEqual([
      { event: 'message', data: { content: '你好' } },
      { event: 'done', data: {} },
    ])
  })

  it('throws for malformed JSON and unsupported event names', () => {
    const malformed = createSseParser(() => undefined)
    expect(() => malformed.push('event: message\ndata: nope\n\n')).toThrow('无效的 SSE 数据')

    const unsupported = createSseParser(() => undefined)
    expect(() => unsupported.push('event: mystery\ndata: {}\n\n')).toThrow('不支持的 SSE 事件')
  })

  it('rejects an unfinished frame at the end of the stream', () => {
    const parser = createSseParser(() => undefined)
    parser.push('event: message\ndata: {"content":"partial"}')
    expect(() => parser.finish()).toThrow('SSE 流意外中断')
  })
})
