import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ChatStreamError, streamChat } from '../../src/services/chatApi'
import { useChat } from '../../src/composables/useChat'

vi.mock('../../src/services/chatApi', async (importOriginal) => {
  const original = await importOriginal<typeof import('../../src/services/chatApi')>()
  return { ...original, streamChat: vi.fn() }
})

const streamChatMock = vi.mocked(streamChat)

describe('useChat', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    streamChatMock.mockReset()
  })

  it('adds the user immediately and appends assistant chunks', async () => {
    let release: (() => void) | undefined
    streamChatMock.mockImplementation(async (_conversationId, _message, handlers) => {
      handlers.onMessage?.('你')
      await new Promise<void>((resolve) => { release = resolve })
      handlers.onMessage?.('好')
      handlers.onDone?.()
    })
    const chat = useChat()

    const pending = chat.send('  hello  ')
    expect(chat.messages.value.map(({ role, content, status }) => ({ role, content, status }))).toEqual([
      { role: 'user', content: 'hello', status: 'complete' },
      { role: 'assistant', content: '你', status: 'streaming' },
    ])
    release?.()
    await pending

    expect(chat.messages.value[1]).toMatchObject({ content: '你好', status: 'complete' })
    expect(chat.isStreaming.value).toBe(false)
  })

  it('reuses one conversation id while sending only the current message', async () => {
    vi.spyOn(globalThis.crypto, 'randomUUID').mockReturnValue(
      '82de55a8-6065-4eeb-84af-079ea2e2b2c5',
    )
    streamChatMock.mockResolvedValue()
    const chat = useChat()

    await chat.send('one')
    await chat.send('two')

    expect(streamChatMock).toHaveBeenNthCalledWith(
      1,
      '82de55a8-6065-4eeb-84af-079ea2e2b2c5',
      'one',
      expect.any(Object),
      expect.any(AbortSignal),
    )
    expect(streamChatMock).toHaveBeenNthCalledWith(
      2,
      '82de55a8-6065-4eeb-84af-079ea2e2b2c5',
      'two',
      expect.any(Object),
      expect.any(AbortSignal),
    )
  })

  it('creates a new conversation id for a new page session', async () => {
    vi.spyOn(globalThis.crypto, 'randomUUID')
      .mockReturnValueOnce('82de55a8-6065-4eeb-84af-079ea2e2b2c5')
      .mockReturnValueOnce('67724b7e-a88c-4a9e-9272-7c4168fc7e25')
    streamChatMock.mockResolvedValue()

    await useChat().send('first session')
    await useChat().send('second session')

    expect(streamChatMock.mock.calls[0]?.[0]).toBe('82de55a8-6065-4eeb-84af-079ea2e2b2c5')
    expect(streamChatMock.mock.calls[1]?.[0]).toBe('67724b7e-a88c-4a9e-9272-7c4168fc7e25')
  })

  it('ignores a second send while generation is active', async () => {
    let release: (() => void) | undefined
    streamChatMock.mockImplementation(() => new Promise<void>((resolve) => { release = resolve }))
    const chat = useChat()

    const first = chat.send('one')
    await chat.send('two')
    expect(streamChatMock).toHaveBeenCalledTimes(1)
    expect(chat.messages.value[0]?.content).toBe('one')
    release?.()
    await first
  })

  it('aborts generation and keeps partial content', async () => {
    streamChatMock.mockImplementation(async (_conversationId, _message, handlers, signal) => {
      handlers.onMessage?.('partial')
      await new Promise<void>((_resolve, reject) => {
        signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
      })
    })
    const chat = useChat()

    const pending = chat.send('stop me')
    chat.stop()
    await pending

    expect(chat.messages.value[1]).toMatchObject({ content: 'partial', status: 'stopped' })
    expect(chat.isStreaming.value).toBe(false)
    expect(chat.errorMessage.value).toBeNull()
  })

  it('shows safe errors and restores the ready state', async () => {
    streamChatMock.mockRejectedValue(new ChatStreamError('模型暂时不可用', 'upstream_error'))
    const chat = useChat()

    await chat.send('hello')

    expect(chat.errorMessage.value).toBe('模型暂时不可用')
    expect(chat.messages.value[1]?.status).toBe('error')
    expect(chat.isStreaming.value).toBe(false)
  })
})
