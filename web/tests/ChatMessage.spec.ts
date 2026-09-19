import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ChatMessage from '../src/components/chat/ChatMessage.vue'

describe('ChatMessage', () => {
  it('renders user and assistant content as plain text', () => {
    const wrapper = mount(ChatMessage, {
      props: { message: { id: '1', role: 'assistant', content: '<b>safe</b>', status: 'complete' } },
    })

    expect(wrapper.text()).toContain('<b>safe</b>')
    expect(wrapper.find('b').exists()).toBe(false)
  })

  it('shows a streaming cursor only for an active assistant reply', () => {
    const wrapper = mount(ChatMessage, {
      props: { message: { id: '1', role: 'assistant', content: 'typing', status: 'streaming' } },
    })

    expect(wrapper.find('[data-testid="streaming-cursor"]').exists()).toBe(true)
  })
})
