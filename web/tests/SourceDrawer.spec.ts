import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import SourceDrawer from '../src/components/chat/SourceDrawer.vue'

describe('SourceDrawer', () => {
  it('shows PDF page metadata without exposing storage paths', () => {
    const wrapper = mount(SourceDrawer, { props: { activeId: '[1]', sources: [{ citation_id: '[1]', chunk_id: 'chunk', document_id: 'doc', document_version_id: 'version', title: '指南', filename: 'guide.pdf', page_start: 2, page_end: 3, heading_path: ['安装'], excerpt: '安全步骤', score: 0.8, rank: 1 }] } })
    expect(wrapper.text()).toContain('第 2–3 页')
    expect(wrapper.text()).toContain('安全步骤')
    expect(wrapper.text()).not.toContain('storage')
  })
})
