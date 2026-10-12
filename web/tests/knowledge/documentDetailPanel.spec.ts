import { flushPromises, mount } from '@vue/test-utils'
import { readFileSync } from 'node:fs'
import { beforeEach, expect, it, vi } from 'vitest'
import DocumentDetailPanel from '../../src/components/knowledge/DocumentDetailPanel.vue'

const api = vi.hoisted(() => ({
  getDocument: vi.fn(),
  listDocumentVersions: vi.fn(),
  previewDocument: vi.fn(),
}))

const routerState = vi.hoisted(() => ({
  route: { query: { version: 'version-1', chunk: 'child-chunk-1' } as Record<string, string> },
  replace: vi.fn(),
}))

vi.mock('../../src/services/knowledgeApi', () => ({ knowledgeApi: api, KnowledgeApiError: class KnowledgeApiError extends Error { constructor(message: string, public code: string) { super(message) } } }))
vi.mock('vue-router', () => ({
  RouterLink: { props: ['to'], template: '<a><slot /></a>' },
  useRoute: () => routerState.route,
  useRouter: () => ({ replace: routerState.replace }),
}))

beforeEach(() => {
  vi.clearAllMocks()
  routerState.route.query = { version: 'version-1', chunk: 'child-chunk-1' }
  api.getDocument.mockResolvedValue({
    id: 'doc-1',
    knowledge_base_id: 'base',
    title: '身份规范',
    filename: 'identity.md',
    mime_type: 'text/markdown',
    status: 'ready',
    active_version_id: 'version-1',
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z',
    active_version: { id: 'version-1', version_no: 1, file_size: 128, status: 'ready', parser_name: 'markdown', created_at: '2026-10-01T00:00:00Z', ready_at: '2026-10-01T00:00:00Z', error_code: null, error_reference: null },
    latest_job: null,
  })
  api.listDocumentVersions.mockResolvedValue({
    items: [{ id: 'version-1', version_no: 1, file_size: 128, status: 'ready', parser_name: 'markdown', created_at: '2026-10-01T00:00:00Z', ready_at: '2026-10-01T00:00:00Z', error_code: null, error_reference: null }],
    next_cursor: null,
  })
  api.previewDocument.mockResolvedValue({
    items: [{
      chunk_id: 'parent-chunk-1',
      ordinal: 0,
      display_ordinal: 1,
      content: '<img src=x onerror=alert(1)>认证要求',
      heading_path: ['身份认证'],
      page_start: null,
      page_end: null,
      anchor_chunk_id: 'child-chunk-1',
      focus_start: 0,
      focus_end: 28,
    }],
    next_cursor: null,
    preview_unavailable_reason: null,
  })
})

it('用子块定位父块并以文本节点安全高亮命中片段', async () => {
  const wrapper = mount(DocumentDetailPanel, { props: { knowledgeBaseId: 'base', documentId: 'doc-1' } })
  await flushPromises()

  expect(api.previewDocument).toHaveBeenCalledWith('doc-1', expect.objectContaining({ versionId: 'version-1', anchorChunkId: 'child-chunk-1' }))
  expect(wrapper.find('.chunk-card.focused').exists()).toBe(true)
  expect(wrapper.find('mark').text()).toContain('<img src=x')
  expect(wrapper.find('img').exists()).toBe(false)
})

it('正文高亮实现不使用 HTML 注入 API', () => {
  const source = readFileSync('src/components/knowledge/DocumentDetailPanel.vue', 'utf8')

  expect(source).not.toMatch(/v-html|innerHTML/)
})
