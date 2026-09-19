import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, vi } from 'vitest'

import App from '../src/App.vue'

describe('App', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('renders the approved single-page shell without model settings', () => {
    render(App)

    expect(screen.getByText('今天想聊些什么？')).toBeTruthy()
    expect(screen.queryByText(/API Key/i)).toBeNull()
    expect(screen.getByLabelText('消息输入')).toBeTruthy()
  })

  it('renders a streamed conversation and restores the composer', async () => {
    const encoder = new TextEncoder()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode('event: message\ndata: {"content":"你好"}\n\n'))
        controller.enqueue(encoder.encode('event: done\ndata: {}\n\n'))
        controller.close()
      },
    }), { status: 200 })))
    render(App)

    await fireEvent.update(screen.getByLabelText('消息输入'), '介绍一下你自己')
    await fireEvent.click(screen.getByLabelText('发送消息'))

    expect(await screen.findByText('介绍一下你自己')).toBeTruthy()
    expect(await screen.findByText('你好')).toBeTruthy()
    await waitFor(() => {
      expect(screen.getByLabelText('消息输入').hasAttribute('disabled')).toBe(false)
    })
  })
})
