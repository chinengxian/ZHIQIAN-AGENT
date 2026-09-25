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

  it('parses knowledge status and public sources', () => {
    const events: unknown[] = []
    const parser = createSseParser((event) => events.push(event))
    parser.push('event: status\ndata: {"stage":"retrieving"}\n\n')
    parser.push('event: sources\ndata: {"items":[{"citation_id":"[1]","chunk_id":"chunk","document_id":"doc","document_version_id":"version","title":"指南","filename":"guide.pdf","page_start":2,"page_end":3,"heading_path":["安装"],"excerpt":"步骤","score":0.8,"rank":1}]}\n\n')
    parser.finish()
    expect(events).toMatchObject([
      { event: 'status', data: { stage: 'retrieving' } },
      { event: 'sources', data: { items: [{ citation_id: '[1]', title: '指南', page_start: 2 }] } },
    ])
  })
})
