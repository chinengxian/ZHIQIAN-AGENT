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

  it('emits a source selection only for registered citations', async () => {
    const wrapper = mount(ChatMessage, {
      props: { message: { id: '1', role: 'assistant', content: '参考 [1]，未注册 [2]', status: 'complete', sources: [{ citation_id: '[1]', chunk_id: 'chunk', document_id: 'doc', document_version_id: 'version', title: '指南', filename: 'guide.pdf', page_start: 2, page_end: 2, heading_path: [], excerpt: '步骤', score: 0.8, rank: 1 }] } },
    })
    await wrapper.get('[aria-label="查看来源 1"]').trigger('click')
    expect(wrapper.emitted('sourceClick')).toEqual([['[1]']])
    expect(wrapper.find('[aria-label="查看来源 2"]').exists()).toBe(false)
  })
})
