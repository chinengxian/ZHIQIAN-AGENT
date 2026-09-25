<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { KnowledgeApiError, knowledgeApi } from '../../services/knowledgeApi'
import { useKnowledge } from '../../composables/useKnowledge'
import type { KnowledgeBase, KnowledgeDocument } from '../../types/knowledge'
import WikiPanel from './WikiPanel.vue'

const route = useRoute()
const router = useRouter()
const selectedId = computed(() => typeof route.params.id === 'string' ? route.params.id : null)
const { bases, documents, loading, error, refreshBases, refreshDocuments } = useKnowledge(selectedId)
const current = computed(() => bases.value.find((base) => base.id === selectedId.value))
const baseSearch = ref('')
const docSearch = ref('')
const statusFilter = ref('all')
const visibleBases = computed(() => bases.value.filter((base) => base.name.toLowerCase().includes(baseSearch.value.toLowerCase())))
const visibleDocuments = computed(() => documents.value.filter((document) =>
  (statusFilter.value === 'all' || document.status === statusFilter.value)
  && `${document.title} ${document.filename}`.toLowerCase().includes(docSearch.value.toLowerCase()),
))
const dialog = ref<'create' | 'edit' | 'deleteBase' | 'deleteDocument' | 'duplicate' | null>(null)
const targetDocument = ref<KnowledgeDocument | null>(null)
const duplicateFile = ref<File | null>(null)
const name = ref('')
const description = ref('')
const busy = ref(false)
const uploading = ref(false)
const actionError = ref('')
const feedback = ref('')
const fileInput = ref<HTMLInputElement | null>(null)
const dragging = ref(false)
const detailTab = ref<'documents' | 'wiki'>('documents')

watch(selectedId, () => { docSearch.value = ''; statusFilter.value = 'all'; detailTab.value = 'documents' })

function openBaseDialog(mode: 'create' | 'edit') {
  name.value = mode === 'edit' ? current.value?.name ?? '' : ''
  description.value = mode === 'edit' ? current.value?.description ?? '' : ''
  actionError.value = ''
  dialog.value = mode
}

async function saveBase() {
  if (!name.value.trim()) { actionError.value = '请输入知识库名称'; return }
  busy.value = true; actionError.value = ''
  try {
    const result = dialog.value === 'edit' && current.value
      ? await knowledgeApi.updateBase(current.value.id, { name: name.value.trim(), description: description.value })
      : await knowledgeApi.createBase(name.value.trim(), description.value)
    dialog.value = null
    await refreshBases()
    await router.push(`/knowledge/${result.id}`)
    feedback.value = '知识库已保存'
  } catch { actionError.value = '保存失败，请重试' }
  finally { busy.value = false }
}

async function toggleBase(base: KnowledgeBase) {
  busy.value = true; feedback.value = ''
  try { await knowledgeApi.updateBase(base.id, { enabled: !base.enabled }); await refreshBases() }
  catch { feedback.value = '状态更新失败，请重试' }
  finally { busy.value = false }
}

async function removeBase() {
  if (!current.value) return
  busy.value = true; actionError.value = ''
  try {
    await knowledgeApi.deleteBase(current.value.id, current.value.document_count > 0)
    dialog.value = null
    await router.push('/knowledge')
    await refreshBases()
    feedback.value = '删除已提交，含文档的知识库将异步清理'
  } catch { actionError.value = '删除失败，请重试' }
  finally { busy.value = false }
}

async function uploadFiles(files: FileList | File[]) {
  const accepted = ['.pdf', '.docx', '.md', '.txt']
  const list = Array.from(files)
  if (!current.value || !list.length) return
  uploading.value = true; actionError.value = ''; feedback.value = ''
  let completed = 0
  for (const file of list) {
    if (!accepted.some((ext) => file.name.toLowerCase().endsWith(ext)) || file.size > 50 * 1024 * 1024) {
      actionError.value = `${file.name}：仅支持 PDF、DOCX、MD、TXT，且每个文件不超过 50 MB`
      continue
    }
    try { await knowledgeApi.upload(current.value.id, file); completed++ }
    catch (cause) {
      if (cause instanceof KnowledgeApiError && cause.code === 'duplicate_document') {
        duplicateFile.value = file; dialog.value = 'duplicate'
      } else if (cause instanceof KnowledgeApiError && cause.code === 'invalid_file_signature') {
        actionError.value = `${file.name}：文件内容与格式不符`
      } else if (cause instanceof KnowledgeApiError && cause.code === 'invalid_text_encoding') {
        actionError.value = `${file.name}：文本需要使用 UTF-8 编码`
      } else { actionError.value = `${file.name}：上传失败，请重试` }
    }
  }
  if (completed) feedback.value = `${completed} 个文件已提交处理`
  await refreshDocuments(); await refreshBases()
  uploading.value = false
  if (fileInput.value) fileInput.value.value = ''
}

async function uploadDuplicate() {
  if (!current.value || !duplicateFile.value) return
  busy.value = true; actionError.value = ''
  try {
    await knowledgeApi.upload(current.value.id, duplicateFile.value, true)
    dialog.value = null; duplicateFile.value = null
    feedback.value = '新版本已提交处理'
    await refreshDocuments()
  } catch { actionError.value = '提交新版本失败，请重试' }
  finally { busy.value = false }
}

async function documentAction(document: KnowledgeDocument, action: 'retry' | 'reindex') {
  busy.value = true; feedback.value = ''
  try {
    if (action === 'retry') await knowledgeApi.retry(document.id)
    else await knowledgeApi.reindex(document.id)
    feedback.value = action === 'retry' ? '重试已提交' : '重建已提交，旧版本在成功前继续可用'
    await refreshDocuments()
  } catch { feedback.value = '操作失败，请重试' }
  finally { busy.value = false }
}

async function removeDocument() {
  if (!targetDocument.value) return
  busy.value = true; actionError.value = ''
  try {
    await knowledgeApi.deleteDocument(targetDocument.value.id)
    dialog.value = null; feedback.value = '删除已提交'
    await refreshDocuments(); await refreshBases()
  } catch { actionError.value = '删除失败，请重试' }
  finally { busy.value = false }
}

function onDrop(event: DragEvent) {
  dragging.value = false
  if (event.dataTransfer?.files) void uploadFiles(event.dataTransfer.files)
}

const stageLabel: Record<string, string> = {
  pending: '等待处理', processing: '处理中', parsing: '解析中', chunking: '分块中', embedding: '向量化中',
  indexing: '建立索引中', ready: '已就绪', failed: '处理失败', deleting: '删除中',
}
</script>

<template>
  <main class="knowledge-page">
    <header class="page-header">
      <div><h1>知识库</h1><p>管理文档与处理状态</p></div>
      <button
        class="primary-button"
        type="button"
        @click="openBaseDialog('create')"
      >
        <span
          class="mdi mdi-plus"
          aria-hidden="true"
        ></span>新建知识库
      </button>
    </header>
    <p
      v-if="feedback"
      class="notice"
      role="status"
    >
      {{ feedback }}
    </p>
    <p
      v-if="error"
      class="notice error"
      role="alert"
    >
      {{ error }} <button
        type="button"
        @click="refreshBases(); refreshDocuments()"
      >
        重试
      </button>
    </p>
    <div class="knowledge-grid">
      <section
        class="base-list"
        aria-label="知识库列表"
      >
        <label class="search-field"><span
          class="mdi mdi-magnify"
          aria-hidden="true"
        ></span><input
          v-model="baseSearch"
          type="search"
          placeholder="搜索知识库"
          aria-label="搜索知识库"
        /></label>
        <p
          v-if="loading"
          class="empty"
        >
          正在加载...
        </p>
        <p
          v-else-if="!bases.length"
          class="empty"
        >
          还没有知识库
        </p>
        <p
          v-else-if="!visibleBases.length"
          class="empty"
        >
          没有匹配的知识库
        </p>
        <RouterLink
          v-for="base in visibleBases"
          :key="base.id"
          :to="`/knowledge/${base.id}`"
          class="base-row"
          :class="{ selected: selectedId === base.id }"
        >
          <span class="base-title">{{ base.name }}</span>
          <span class="base-meta">{{ base.document_count }} 个文档<span v-if="base.processing_count"> · {{ base.processing_count }} 个处理中</span></span>
          <span
            class="base-status"
            :class="{ disabled: !base.enabled }"
          >{{ base.enabled ? '已启用' : '已停用' }}</span>
        </RouterLink>
      </section>
      <section
        class="detail"
        aria-label="知识库详情"
      >
        <template v-if="current">
          <div class="detail-header">
            <div><h2>{{ current.name }}</h2><p>{{ current.description || '暂无描述' }}</p></div>
            <div class="detail-actions">
              <button
                type="button"
                title="编辑知识库"
                aria-label="编辑知识库"
                @click="openBaseDialog('edit')"
              >
                <span
                  class="mdi mdi-pencil-outline"
                  aria-hidden="true"
                ></span>
              </button>
              <button
                type="button"
                :disabled="busy"
                :title="current.enabled ? '停用知识库' : '启用知识库'"
                :aria-label="current.enabled ? '停用知识库' : '启用知识库'"
                @click="toggleBase(current)"
              >
                <span
                  class="mdi"
                  :class="current.enabled ? 'mdi-pause-circle-outline' : 'mdi-play-circle-outline'"
                  aria-hidden="true"
                ></span>
              </button>
              <button
                type="button"
                title="删除知识库"
                aria-label="删除知识库"
                @click="dialog = 'deleteBase'"
              >
                <span
                  class="mdi mdi-delete-outline"
                  aria-hidden="true"
                ></span>
              </button>
            </div>
          </div>
          <div
            class="detail-tabs"
            role="tablist"
            aria-label="知识库内容"
          >
            <button
              type="button"
              role="tab"
              :aria-selected="detailTab === 'documents'"
              @click="detailTab = 'documents'"
            >
              文档
            </button>
            <button
              type="button"
              role="tab"
              :aria-selected="detailTab === 'wiki'"
              @click="detailTab = 'wiki'"
            >
              Wiki
            </button>
          </div>
          <WikiPanel
            v-if="detailTab === 'wiki'"
            :knowledge-base-id="current.id"
          />
          <template v-else>
            <div class="document-toolbar">
              <h3>文档 <span>{{ current.document_count }}</span></h3>
              <div class="toolbar-fields">
                <label class="search-field"><span
                  class="mdi mdi-magnify"
                  aria-hidden="true"
                ></span><input
                  v-model="docSearch"
                  type="search"
                  placeholder="搜索文档"
                  aria-label="搜索文档"
                /></label>
                <label class="filter-label">状态 <select
                  v-model="statusFilter"
                  aria-label="筛选状态"
                ><option value="all">全部</option><option value="pending">等待处理</option><option value="processing">处理中</option><option value="ready">已就绪</option><option value="failed">失败</option><option value="deleting">删除中</option></select></label>
              </div>
            </div>
            <div
              class="upload-zone"
              :class="{ dragging }"
              @dragover.prevent="dragging = true"
              @dragleave.prevent="dragging = false"
              @drop.prevent="onDrop"
            >
              <span
                class="mdi mdi-cloud-upload-outline"
                aria-hidden="true"
              ></span>
              <span>拖入文件，或</span>
              <button
                type="button"
                :disabled="uploading"
                @click="fileInput?.click()"
              >
                {{ uploading ? '上传中...' : '选择文件' }}
              </button>
              <small>PDF、DOCX、MD、TXT · 单个不超过 50 MB</small>
              <input
                ref="fileInput"
                type="file"
                multiple
                accept=".pdf,.docx,.md,.txt"
                aria-label="选择上传文件"
                class="file-input"
                @change="($event) => uploadFiles(($event.target as HTMLInputElement).files ?? [])"
              />
            </div>
            <p
              v-if="actionError"
              class="notice error"
              role="alert"
            >
              {{ actionError }}
            </p>
            <p
              v-if="!documents.length"
              class="empty detail-empty"
            >
              还没有文档，上传文件后可在这里查看处理进度。
            </p>
            <p
              v-else-if="!visibleDocuments.length"
              class="empty detail-empty"
            >
              没有匹配的文档
            </p>
            <div
              v-else
              class="document-list"
            >
              <article
                v-for="document in visibleDocuments"
                :key="document.id"
                class="document-row"
              >
                <span
                  class="mdi mdi-file-document-outline file-icon"
                  aria-hidden="true"
                ></span>
                <div class="document-info">
                  <strong>{{ document.title }}</strong><small>{{ document.filename }}</small>
                  <div
                    v-if="document.job && ['pending', 'processing'].includes(document.status)"
                    class="progress-wrap"
                  >
                    <span>{{ stageLabel[document.job.stage] || document.job.stage }} · {{ document.job.progress }}%</span>
                    <progress
                      :value="document.job.progress"
                      max="100"
                      :aria-label="`${document.title}处理进度`"
                    ></progress>
                  </div>
                  <small
                    v-if="document.status === 'failed'"
                    class="failure"
                  >{{ document.job?.error_code || '处理失败' }}<span v-if="document.job?.error_reference"> · 参考号 {{ document.job.error_reference }}</span></small>
                </div>
                <span
                  class="document-status"
                  :class="document.status"
                >{{ stageLabel[document.status] || document.status }}</span>
                <div class="document-actions">
                  <button
                    v-if="document.status === 'failed'"
                    type="button"
                    :disabled="busy"
                    title="重试"
                    :aria-label="`重试 ${document.title}`"
                    @click="documentAction(document, 'retry')"
                  >
                    <span
                      class="mdi mdi-refresh"
                      aria-hidden="true"
                    ></span>
                  </button>
                  <button
                    v-if="document.active_version_id && document.status === 'ready'"
                    type="button"
                    :disabled="busy"
                    title="重建索引"
                    :aria-label="`重建 ${document.title}`"
                    @click="documentAction(document, 'reindex')"
                  >
                    <span
                      class="mdi mdi-database-refresh-outline"
                      aria-hidden="true"
                    ></span>
                  </button>
                  <button
                    type="button"
                    title="删除文档"
                    :aria-label="`删除 ${document.title}`"
                    @click="targetDocument = document; dialog = 'deleteDocument'"
                  >
                    <span
                      class="mdi mdi-delete-outline"
                      aria-hidden="true"
                    ></span>
                  </button>
                </div>
              </article>
            </div>
          </template>
        </template>
        <div
          v-else
          class="empty detail-empty"
        >
          从左侧选择知识库查看文档。
        </div>
      </section>
    </div>
    <div
      v-if="dialog"
      class="dialog-backdrop"
      @click.self="dialog = null"
    >
      <section
        class="dialog"
        role="dialog"
        aria-modal="true"
        :aria-label="dialog === 'create' ? '新建知识库' : dialog === 'edit' ? '编辑知识库' : '确认操作'"
      >
        <template v-if="dialog === 'create' || dialog === 'edit'">
          <h2>{{ dialog === 'create' ? '新建知识库' : '编辑知识库' }}</h2>
          <label>名称<input
            v-model="name"
            maxlength="200"
            autofocus
          /></label>
          <label>描述<textarea
            v-model="description"
            rows="3"
            maxlength="4000"
          ></textarea></label>
        </template>
        <template v-else-if="dialog === 'deleteBase'">
          <h2>删除知识库</h2><p>确定删除“{{ current?.name }}”吗？<span v-if="current?.document_count">其中 {{ current.document_count }} 个文档将异步清理，此操作无法撤销。</span></p>
        </template>
        <template v-else-if="dialog === 'deleteDocument'">
          <h2>删除文档</h2><p>确定删除“{{ targetDocument?.title }}”吗？删除将异步完成。</p>
        </template>
        <template v-else>
          <h2>文件已存在</h2><p>“{{ duplicateFile?.name }}”已有相同内容。要作为新版本继续上传吗？</p>
        </template>
        <p
          v-if="actionError"
          class="failure"
          role="alert"
        >
          {{ actionError }}
        </p>
        <div class="dialog-actions">
          <button
            type="button"
            @click="dialog = null"
          >
            取消
          </button><button
            class="primary-button"
            type="button"
            :disabled="busy"
            @click="dialog === 'create' || dialog === 'edit' ? saveBase() : dialog === 'deleteBase' ? removeBase() : dialog === 'deleteDocument' ? removeDocument() : uploadDuplicate()"
          >
            确认
          </button>
        </div>
      </section>
    </div>
  </main>
</template>

<style scoped>
.knowledge-page { max-width:1440px; min-height:100vh; margin:auto; padding:40px 40px 70px; color:var(--agent-ink); }
.page-header, .detail-header, .document-toolbar, .toolbar-fields, .detail-actions, .document-actions, .dialog-actions { display:flex; align-items:center; justify-content:space-between; gap:12px; }
.page-header { margin-bottom:28px; }
h1,h2,h3,p { margin:0; } h1 { font-size:27px; } h2 { font-size:21px; } h3 { font-size:16px; }
.page-header p, .detail-header p { color:var(--agent-muted); font-size:13px; margin-top:5px; }
button { cursor:pointer; } button:disabled { opacity:.5; cursor:default; }
.primary-button { display:inline-flex; align-items:center; gap:6px; border:0; border-radius:7px; background:#5552bf; color:#fff; padding:10px 15px; font-size:13px; font-weight:600; white-space:nowrap; }
.primary-button .mdi { font-size:18px; }
.knowledge-grid { display:grid; grid-template-columns:minmax(230px,280px) minmax(0,1fr); border-top:1px solid #e2e2e7; min-height:650px; }
.base-list { padding:20px 12px 0 0; border-right:1px solid #e2e2e7; }
.search-field { display:flex; align-items:center; gap:6px; min-width:0; border:1px solid #dfdfe4; border-radius:6px; background:#fff; padding:0 10px; color:#777; }
.search-field input { width:100%; min-width:0; height:36px; border:0; outline:none; background:transparent; font-size:13px; }
.base-list .search-field { margin:0 8px 15px; }
.base-row { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:2px 8px; padding:13px 12px; color:inherit; text-decoration:none; border-radius:6px; }
.base-row:hover, .base-row.selected { background:#eeeef6; }
.base-title { grid-column:1/-1; font-size:14px; font-weight:600; overflow-wrap:anywhere; }
.base-meta, .base-status { font-size:12px; color:var(--agent-muted); }
.base-status { color:#247054; }.base-status.disabled { color:#888; }
.detail { padding:23px 0 0 28px; min-width:0; }.detail-header { align-items:flex-start; padding-bottom:22px; border-bottom:1px solid #e5e5e9; }
.detail-header p { overflow-wrap:anywhere; }.detail-actions button, .document-actions button { border:0; background:transparent; color:#5c5c66; width:34px; height:34px; border-radius:6px; font-size:19px; }
.detail-tabs { display:flex; gap:18px; border-bottom:1px solid #e4e4eb; margin-top:14px; }.detail-tabs button { border:0; background:none; color:#666; padding:12px 5px; font-size:13px; }.detail-tabs button[aria-selected="true"] { color:#5552bf; border-bottom:2px solid #5552bf; font-weight:600; }
.detail-actions button:hover, .document-actions button:hover { background:#e9e9ee; }
.document-toolbar { margin:22px 0 15px; flex-wrap:wrap; }.document-toolbar h3 span { color:var(--agent-muted); font-weight:400; }
.toolbar-fields { flex-wrap:wrap; }.toolbar-fields .search-field { width:170px; }.filter-label { display:flex; align-items:center; gap:6px; color:#666; font-size:12px; }.filter-label select { padding:7px; border:1px solid #dfdfe4; border-radius:6px; background:#fff; font:inherit; }
.upload-zone { display:flex; align-items:center; justify-content:center; flex-wrap:wrap; gap:6px; min-height:78px; padding:14px; border:1px dashed #b9b9c5; border-radius:6px; background:#fafafd; font-size:13px; color:#62626c; }
.upload-zone.dragging { border-color:#5552bf; background:#f0f0ff; }.upload-zone .mdi { font-size:21px; color:#5552bf; }.upload-zone button { border:0; background:none; color:#4340ae; font-weight:600; }.upload-zone small { width:100%; text-align:center; font-size:11px; }.file-input { position:absolute; width:1px; height:1px; opacity:0; }
.document-list { margin-top:15px; }.document-row { display:flex; align-items:center; gap:12px; min-width:0; padding:14px 4px; border-bottom:1px solid #e9e9ed; }.file-icon { font-size:23px; color:#6665a7; }.document-info { min-width:0; flex:1; }.document-info strong, .document-info small { display:block; overflow-wrap:anywhere; }.document-info strong { font-size:13px; }.document-info small { font-size:11px; color:var(--agent-muted); margin-top:4px; }.document-status { font-size:11px; white-space:nowrap; color:#60606a; }.document-status.ready { color:#217154; }.document-status.failed, .failure { color:#a22920 !important; }.document-actions { gap:0; }
.progress-wrap { margin-top:7px; color:#5552bf; font-size:11px; }.progress-wrap progress { display:block; width:100%; height:4px; margin-top:4px; accent-color:#5552bf; }.notice { padding:10px 12px; background:#eef6f0; color:#226344; margin-bottom:15px; font-size:13px; }.notice.error { background:#fff1ef; color:#a22920; }.notice button { border:0; background:transparent; color:inherit; text-decoration:underline; }.empty { color:#777; font-size:13px; padding:22px 12px; }.detail-empty { text-align:center; padding:70px 15px; }
.dialog-backdrop { position:fixed; inset:0; z-index:20; display:grid; place-items:center; background:#0006; padding:15px; }.dialog { width:min(420px,100%); padding:24px; background:#fff; border-radius:8px; box-shadow:0 20px 65px #0003; }.dialog h2 { font-size:18px; margin-bottom:18px; }.dialog p { font-size:13px; line-height:1.6; }.dialog label { display:block; font-size:12px; color:#555; margin:12px 0; }.dialog input,.dialog textarea { display:block; width:100%; margin-top:6px; padding:9px; border:1px solid #cfcfd6; border-radius:5px; font:inherit; color:#222; }.dialog-actions { justify-content:flex-end; margin-top:23px; }.dialog-actions button:not(.primary-button) { padding:8px 14px; border:1px solid #d7d7dc; background:#fff; border-radius:6px; }
@media (max-width:850px) { .knowledge-page { padding:64px 20px 50px; }.knowledge-grid { grid-template-columns:1fr; }.base-list { border-right:0; border-bottom:1px solid #e2e2e7; padding:15px 0; }.detail { padding:22px 0; }.base-row { display:flex; align-items:center; }.base-title { flex:1; } }
@media (max-width:520px) { .page-header { align-items:flex-end; }.page-header h1 { font-size:22px; }.page-header p { display:none; }.document-row { flex-wrap:wrap; }.document-actions { margin-left:auto; }.toolbar-fields { width:100%; }.toolbar-fields .search-field { flex:1; } }
</style>
