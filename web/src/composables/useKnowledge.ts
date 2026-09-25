import { onMounted, onUnmounted, ref, watch, type Ref } from 'vue'
import { knowledgeApi } from '../services/knowledgeApi'
import type { KnowledgeBase, KnowledgeDocument } from '../types/knowledge'

export function useKnowledge(selectedId: Ref<string | null>) {
  const bases = ref<KnowledgeBase[]>([])
  const documents = ref<KnowledgeDocument[]>([])
  const loading = ref(true)
  const error = ref('')
  let source: EventSource | null = null
  let timer: ReturnType<typeof setInterval> | null = null

  async function refreshBases() {
    try { bases.value = await knowledgeApi.listBases(); error.value = '' }
    catch { error.value = '知识库加载失败，请重试' }
    finally { loading.value = false }
  }

  async function refreshDocuments() {
    const id = selectedId.value
    if (!id) { documents.value = []; return }
    try {
      const result = await knowledgeApi.listDocuments(id)
      if (selectedId.value === id) { documents.value = result; error.value = '' }
    } catch { error.value = '文档加载失败，请重试' }
  }

  watch(selectedId, refreshDocuments, { immediate: true })
  onMounted(() => {
    void refreshBases()
    // SSE 负责即时更新；定时刷新覆盖断线和页面重新进入时的事实状态。
    source = knowledgeApi.events((progress) => {
      const document = documents.value.find((item) => item.id === progress.document_id)
      if (document) {
        document.status = progress.stage === 'ready' ? 'ready' : progress.stage === 'failed' ? 'failed' : document.status
        document.job = { id: progress.job_id, stage: progress.stage, status: progress.status, progress: progress.progress, error_code: progress.error_code, error_reference: progress.error_reference }
      }
      if (['ready', 'failed', 'deleted'].includes(progress.stage)) {
        void refreshDocuments(); void refreshBases()
      }
    }, () => { void refreshDocuments(); void refreshBases() })
    timer = setInterval(() => { void refreshDocuments(); void refreshBases() }, 10000)
  })
  onUnmounted(() => { source?.close(); if (timer) clearInterval(timer) })

  return { bases, documents, loading, error, refreshBases, refreshDocuments }
}
