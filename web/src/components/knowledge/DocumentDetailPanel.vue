<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { KnowledgeApiError, knowledgeApi } from '../../services/knowledgeApi'
import type { DocumentDetail, DocumentPreviewChunk, DocumentVersion } from '../../types/knowledge'
import { previewChunkLabel } from './documentPreview'

const props = defineProps<{ knowledgeBaseId: string; documentId: string }>()
const route = useRoute()
const router = useRouter()
const detail = ref<DocumentDetail | null>(null)
const versions = ref<DocumentVersion[]>([])
const chunks = ref<DocumentPreviewChunk[]>([])
const nextCursor = ref<string | null>(null)
const loading = ref(false)
const previewLoading = ref(false)
const error = ref('')
const previewError = ref('')
const unavailableReason = ref<string | null>(null)
let requestId = 0

const requestedVersion = computed(() => typeof route.query.version === 'string' ? route.query.version : null)
const requestedChunk = computed(() => typeof route.query.chunk === 'string' ? route.query.chunk : null)
const selectedVersionId = computed(() => requestedVersion.value ?? detail.value?.active_version_id ?? null)

const stageLabel: Record<string, string> = {
  pending: '等待处理',
  processing: '处理中',
  parsing: '解析中',
  chunking: '分块中',
  embedding: '向量化中',
  indexing: '建立索引中',
  ready: '已就绪',
  failed: '处理失败',
  deleting: '删除中',
  succeeded: '已完成',
  running: '运行中',
}

const unavailableLabel: Record<string, string> = {
  processing: '正文还在处理，完成后会显示预览。',
  failed: '该版本处理失败，暂无可预览正文。',
  content_removed: '该版本正文不可用，可能已被清理或来源已失效。',
}

function formatSize(size: number | null | undefined) {
  if (!size) return '0 B'
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

function formatDate(value: string | null | undefined) {
  if (!value) return '无'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value))
}

async function loadPreview(currentRequest: number, cursor: string | null, append: boolean) {
  const versionId = selectedVersionId.value
  previewLoading.value = true
  previewError.value = ''
  try {
    const preview = await knowledgeApi.previewDocument(props.documentId, {
      versionId,
      cursor,
      limit: 10,
      anchorChunkId: append ? null : requestedChunk.value,
    })
    if (currentRequest !== requestId) return
    chunks.value = append ? [...chunks.value, ...preview.items] : preview.items
    nextCursor.value = preview.next_cursor
    unavailableReason.value = preview.preview_unavailable_reason
  } catch (cause) {
    if (currentRequest !== requestId) return
    if (cause instanceof KnowledgeApiError && ['document_chunk_not_found', 'document_version_not_found'].includes(cause.code)) {
      previewError.value = '来源已失效，无法定位到对应版本或正文块。'
      chunks.value = []
      nextCursor.value = null
      unavailableReason.value = 'content_removed'
    } else {
      previewError.value = '正文加载失败，请重试。'
    }
  } finally {
    if (currentRequest === requestId) previewLoading.value = false
  }
}

async function loadDocument() {
  const currentRequest = ++requestId
  loading.value = true
  error.value = ''
  previewError.value = ''
  chunks.value = []
  nextCursor.value = null
  try {
    const [nextDetail, versionPage] = await Promise.all([
      knowledgeApi.getDocument(props.documentId),
      knowledgeApi.listDocumentVersions(props.documentId, { limit: 50 }),
    ])
    if (currentRequest !== requestId) return
    detail.value = nextDetail
    versions.value = versionPage.items
    await loadPreview(currentRequest, null, false)
  } catch {
    if (currentRequest === requestId) error.value = '文档详情加载失败，请返回列表后重试。'
  } finally {
    if (currentRequest === requestId) loading.value = false
  }
}

async function selectVersion(versionId: string) {
  await router.replace({
    path: `/knowledge/${props.knowledgeBaseId}/documents/${props.documentId}`,
    query: { version: versionId },
  })
}

watch(
  () => [props.documentId, route.query.version, route.query.chunk],
  () => { void loadDocument() },
  { immediate: true },
)
</script>

<template>
  <section class="document-detail-panel">
    <RouterLink
      class="back-link"
      :to="`/knowledge/${knowledgeBaseId}`"
    >
      <span
        class="mdi mdi-arrow-left"
        aria-hidden="true"
      ></span>返回文档列表
    </RouterLink>
    <p
      v-if="loading"
      class="detail-empty"
    >
      正在加载文档详情...
    </p>
    <p
      v-else-if="error"
      class="detail-error"
      role="alert"
    >
      {{ error }}
    </p>
    <template v-else-if="detail">
      <header class="document-heading">
        <div>
          <h3>{{ detail.title }}</h3>
          <p>{{ detail.filename }}</p>
        </div>
        <span :class="['status-pill', detail.status]">{{ stageLabel[detail.status] || detail.status }}</span>
      </header>
      <dl class="meta-grid">
        <div><dt>格式</dt><dd>{{ detail.mime_type }}</dd></div>
        <div><dt>活动版本</dt><dd>{{ detail.active_version?.version_no ? `v${detail.active_version.version_no}` : '无' }}</dd></div>
        <div><dt>大小</dt><dd>{{ formatSize(detail.active_version?.file_size) }}</dd></div>
        <div><dt>上传时间</dt><dd>{{ formatDate(detail.created_at) }}</dd></div>
        <div><dt>就绪时间</dt><dd>{{ formatDate(detail.active_version?.ready_at) }}</dd></div>
        <div><dt>解析器</dt><dd>{{ detail.active_version?.parser_name || '未记录' }}</dd></div>
      </dl>
      <div
        v-if="detail.latest_job"
        class="job-line"
      >
        <span>{{ stageLabel[detail.latest_job.stage] || detail.latest_job.stage }} · {{ stageLabel[detail.latest_job.status] || detail.latest_job.status }} · {{ detail.latest_job.progress }}%</span>
        <span v-if="detail.latest_job.error_code">{{ detail.latest_job.error_code }}<template v-if="detail.latest_job.error_reference"> · {{ detail.latest_job.error_reference }}</template></span>
      </div>
      <div class="version-row">
        <label>
          版本
          <select
            :value="selectedVersionId || ''"
            aria-label="选择文档版本"
            @change="selectVersion(($event.target as HTMLSelectElement).value)"
          >
            <option
              v-for="version in versions"
              :key="version.id"
              :value="version.id"
            >
              v{{ version.version_no }} · {{ stageLabel[version.status] || version.status }} · {{ formatSize(version.file_size) }}
            </option>
          </select>
        </label>
      </div>
      <section class="preview-section">
        <div class="preview-title">
          <h4>正文预览</h4>
          <span v-if="requestedChunk">已定位来源 {{ requestedChunk.slice(0, 8) }}</span>
        </div>
        <p
          v-if="previewError"
          class="detail-error"
          role="alert"
        >
          {{ previewError }}
        </p>
        <p
          v-if="unavailableReason && !chunks.length"
          class="detail-empty"
        >
          {{ unavailableLabel[unavailableReason] || '正文不可用。' }}
        </p>
        <article
          v-for="chunk in chunks"
          :key="chunk.chunk_id"
          class="chunk-card"
          :class="{ focused: chunk.anchor_chunk_id }"
        >
          <div class="chunk-meta">
            <span>{{ chunk.heading_path.join(' / ') || '正文' }}</span>
            <span>{{ previewChunkLabel(chunk) }}</span>
          </div>
          <pre>{{ chunk.content }}</pre>
        </article>
        <button
          v-if="nextCursor"
          type="button"
          class="load-more"
          :disabled="previewLoading"
          @click="loadPreview(requestId, nextCursor, true)"
        >
          {{ previewLoading ? '加载中...' : '加载更多正文' }}
        </button>
      </section>
    </template>
  </section>
</template>

<style scoped>
.document-detail-panel { padding-top:18px; }
.back-link { display:inline-flex; align-items:center; gap:5px; margin-bottom:15px; color:#4d4ab2; font-size:13px; text-decoration:none; }
.document-heading { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; padding-bottom:16px; border-bottom:1px solid #e5e5eb; }
.document-heading h3 { margin:0; font-size:18px; overflow-wrap:anywhere; }
.document-heading p { margin:5px 0 0; color:var(--agent-muted); font-size:12px; overflow-wrap:anywhere; }
.status-pill { flex:0 0 auto; border-radius:999px; padding:4px 9px; background:#eeeeF4; color:#565662; font-size:11px; }
.status-pill.ready { background:#e7f4ed; color:#217154; }
.status-pill.failed { background:#fff0ed; color:#a22920; }
.meta-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; margin:16px 0; }
.meta-grid div { min-width:0; border:1px solid #e5e5eb; border-radius:6px; padding:10px; background:#fff; }
.meta-grid dt { color:var(--agent-muted); font-size:11px; }
.meta-grid dd { margin:5px 0 0; font-size:13px; overflow-wrap:anywhere; }
.job-line { display:flex; flex-wrap:wrap; gap:8px 15px; margin-bottom:14px; color:#62626c; font-size:12px; }
.version-row { margin:14px 0; }
.version-row label { display:inline-flex; align-items:center; gap:8px; color:#555; font-size:12px; }
.version-row select { max-width:100%; padding:7px; border:1px solid #d7d7df; border-radius:6px; background:#fff; font:inherit; }
.preview-section { border-top:1px solid #e7e7ec; padding-top:16px; }
.preview-title { display:flex; align-items:center; justify-content:space-between; gap:10px; margin-bottom:10px; }
.preview-title h4 { margin:0; font-size:14px; }
.preview-title span { color:#5d5aaf; font-size:11px; }
.chunk-card { border:1px solid #e4e4eb; border-radius:6px; padding:12px; margin:10px 0; background:#fff; }
.chunk-card.focused { border-color:#7772d0; box-shadow:0 0 0 2px #ecebff; }
.chunk-meta { display:flex; justify-content:space-between; gap:10px; margin-bottom:8px; color:#666; font-size:11px; }
.chunk-meta span:first-child { overflow-wrap:anywhere; }
.chunk-card pre { margin:0; white-space:pre-wrap; overflow-wrap:anywhere; color:#25252c; font:13px/1.7 inherit; }
.load-more { margin-top:10px; border:1px solid #d8d8e0; background:#fff; color:#454299; padding:8px 12px; border-radius:6px; font-size:12px; }
.detail-empty, .detail-error { color:#777; font-size:13px; padding:22px 0; }
.detail-error { color:#a22920; }
@media (max-width:700px) {
  .meta-grid { grid-template-columns:1fr; }
  .document-heading, .preview-title { align-items:flex-start; flex-direction:column; }
}
</style>
