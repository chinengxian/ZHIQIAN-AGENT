import { afterEach, describe, expect, it, vi } from 'vitest'

import { ChatStreamError, streamChat } from '../../src/services/chatApi'

function streamResponse(chunks: string[]): Response {
  const encoder = new TextEncoder()
  return new Response(
    new ReadableStream({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
        controller.close()
      },
    }),
    { status: 200, headers: { 'content-type': 'text/event-stream' } },
  )
}

describe('streamChat', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('posts the conversation id and current message and forwards SSE events', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      streamResponse([
        'event: message\ndata: {"content":"Hi"}\n\n',
        'event: done\ndata: {}\n\n',
      ]),
    )
    vi.stubGlobal('fetch', fetchMock)
    const onMessage = vi.fn()
    const onDone = vi.fn()

    await streamChat('82de55a8-6065-4eeb-84af-079ea2e2b2c5', 'Hello', { onMessage, onDone })

    expect(fetchMock).toHaveBeenCalledWith('/api/v1/chat/stream', expect.objectContaining({
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        conversation_id: '82de55a8-6065-4eeb-84af-079ea2e2b2c5',
        message: 'Hello',
      }),
    }))
    expect(onMessage).toHaveBeenCalledWith('Hi')
    expect(onDone).toHaveBeenCalledOnce()
  })

  it('surfaces safe server errors from non-success responses', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ detail: '模型暂时不可用' }),
      { status: 502, headers: { 'content-type': 'application/json' } },
    )))

    await expect(streamChat('conversation-id', 'Hello', {})).rejects.toMatchObject({
      name: 'ChatStreamError',
      message: '模型暂时不可用',
      code: 'http_error',
    })
  })

  it('reads FastAPI structured error details', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ detail: { code: 'upstream_unavailable', message: 'Model request failed' } }),
      { status: 502, headers: { 'content-type': 'application/json' } },
    )))

    await expect(streamChat('conversation-id', 'Hello', {})).rejects.toMatchObject({
      message: 'Model request failed',
      code: 'upstream_unavailable',
    })
  })

  it('surfaces an error event and never reports completion', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(streamResponse([
      'event: message\ndata: {"content":"partial"}\n\n',
      'event: error\ndata: {"code":"upstream_error","message":"生成失败，请重试"}\n\n',
    ])))
    const onDone = vi.fn()

    await expect(streamChat('conversation-id', 'Hello', { onDone })).rejects.toEqual(
      new ChatStreamError('生成失败，请重试', 'upstream_error'),
    )
    expect(onDone).not.toHaveBeenCalled()
  })
})
