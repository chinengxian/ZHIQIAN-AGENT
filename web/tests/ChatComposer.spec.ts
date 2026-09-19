import { mount } from '@vue/test-utils'

import ChatComposer from '../src/components/chat/ChatComposer.vue'

describe('ChatComposer', () => {
  it('does not send blank content', async () => {
    const wrapper = mount(ChatComposer, { props: { disabled: false, streaming: false } })
    await wrapper.get('textarea').setValue('   ')
    await wrapper.get('textarea').trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('send')).toBeUndefined()
  })

  it('sends on Enter and keeps Shift+Enter for a newline', async () => {
    const wrapper = mount(ChatComposer, { props: { disabled: false, streaming: false } })
    await wrapper.get('textarea').setValue('hello')
    await wrapper.get('textarea').trigger('keydown', { key: 'Enter', shiftKey: true })
    expect(wrapper.emitted('send')).toBeUndefined()
    await wrapper.get('textarea').trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('send')?.[0]).toEqual(['hello'])
  })

  it('offers a stop action while streaming', () => {
    const wrapper = mount(ChatComposer, { props: { disabled: true, streaming: true } })
    expect(wrapper.find('[aria-label="停止生成"]').exists()).toBe(true)
  })
})
