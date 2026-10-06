<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { knowledgeApi } from '../../services/knowledgeApi'
import type { WikiConfig, WikiJob, WikiPage, WikiPageSummary, WikiPresentationSource, WikiSearchResult, WikiVersion } from '../../types/knowledge'

const props = defineProps<{ knowledgeBaseId: string }>()
const config = ref<WikiConfig | null>(null)
const pages = ref<WikiPageSummary[]>([])
const jobs = ref<WikiJob[]>([])
const page = ref<WikiPage | null>(null)
const versions = ref<WikiVersion[]>([])
const selectedVersion = ref<string | null>(null)
const limit = ref(20000)
const editing = ref(false)
const draftTitle = ref('')
const draftContent = ref('')
const busy = ref(false)
const error = ref('')
const message = ref('')
const expandedSource = ref<string | null>(null)
const searchQuery = ref('')
const searching = ref(false)
const searchResult = ref<WikiSearchResult | null>(null)
const searchError = ref('')
const highlightClaimId = ref<string | null>(null)
const expandedSectionSources = ref<Set<string>>(new Set())
let searchRequestToken = 0
let highlightTimer: number | null = null
const candidate = computed(() => versions.value.find((item) => item.id === selectedVersion.value) ?? null)
const pending = computed(() => versions.value.find((item) => item.review_state === 'pending_review') ?? null)
const estimate = computed(() => Math.max(0, (config.value?.token_limit ?? 0) - (config.value?.tokens_charged ?? 0) - (config.value?.tokens_reserved ?? 0)))
const paused = computed(() => jobs.value.filter((item) => item.status === 'paused_budget').length)
const searchActive = computed(() => searching.value || searchResult.value !== null || Boolean(searchError.value))

const matchedFieldLabels: Record<string, string> = {
  title: '标题命中',
  slug: '路径命中',
  summary: '摘要命中',
  alias: '别名命中',
  source_chunk: '语义来源命中',
}

function pageTypeLabel(pageType: WikiPageSummary['page_type']) {
  return pageType === 'index' ? '目录' : pageType === 'topic' ? '主题' : '摘要'
}

function matchedFieldLabel(field: string) {
  return matchedFieldLabels[field] ?? field
}

async function load() {
  error.value = ''
  try {
    const [nextConfig, firstPages, nextJobs] = await Promise.all([
      knowledgeApi.getWikiConfig(props.knowledgeBaseId),
      knowledgeApi.listWikiPages(props.knowledgeBaseId),
      knowledgeApi.listWikiJobs(props.knowledgeBaseId),
    ])
    const nextPages = [...firstPages]
    let lastPage = firstPages
    while (lastPage.length === 100) {
      lastPage = await knowledgeApi.listWikiPages(props.knowledgeBaseId, lastPage[99].slug)
      nextPages.push(...lastPage)
    }
    config.value = nextConfig
    pages.value = nextPages
    jobs.value = nextJobs
    limit.value = nextConfig.token_limit || 20000
    if (page.value && !nextPages.some((item) => item.id === page.value?.id)) page.value = null
  } catch { error.value = 'Wiki 加载失败，请重试' }
}

async function openPage(pageId: string) {
  error.value = ''
  try {
    const [detail, history] = await Promise.all([
      knowledgeApi.getWikiPage(props.knowledgeBaseId, pageId),
      knowledgeApi.listWikiVersions(props.knowledgeBaseId, pageId),
    ])
    page.value = detail
    versions.value = history
    selectedVersion.value = history.find((item) => item.review_state === 'pending_review')?.id ?? null
    editing.value = false
  } catch { error.value = '页面加载失败，请重试' }
}

async function search() {
  const query = searchQuery.value.trim()
  if (!query) {
    clearSearch()
    return
  }
  const requestToken = ++searchRequestToken
  searching.value = true
  searchError.value = ''
  try {
    const result = await knowledgeApi.searchWiki(props.knowledgeBaseId, query, {
      mode: 'hybrid',
      limit: 5,
      claimLimit: 12,
    })
    if (requestToken === searchRequestToken) searchResult.value = result
  } catch {
    if (requestToken === searchRequestToken) {
      searchResult.value = null
      searchError.value = 'Wiki 搜索失败，请重试'
    }
  } finally {
    if (requestToken === searchRequestToken) searching.value = false
  }
}

function clearSearch() {
  searchRequestToken += 1
  searchQuery.value = ''
  searchResult.value = null
  searchError.value = ''
  searching.value = false
}

async function openSearchResult(pageId: string, claimId?: string) {
  await openPage(pageId)
  if (!claimId) return
  highlightClaimId.value = claimId
  await nextTick()
  document.querySelector<HTMLElement>(`[data-claim-id="${claimId}"]`)?.scrollIntoView?.({ behavior: 'smooth', block: 'center' })
  if (highlightTimer !== null) window.clearTimeout(highlightTimer)
  highlightTimer = window.setTimeout(() => { highlightClaimId.value = null }, 2400)
}

async function saveConfig(changes: Partial<Pick<WikiConfig, 'enabled' | 'token_limit'>>) {
  busy.value = true; error.value = ''; message.value = ''
  try {
    await knowledgeApi.updateWikiConfig(props.knowledgeBaseId, changes)
    message.value = changes.enabled === true ? 'Wiki 已开启，文档将逐步生成' : changes.enabled === false ? 'Wiki 已停用，页面仍可浏览' : '额度已保存'
    await load()
  } catch { error.value = '设置未保存，请检查额度后重试' }
  finally { busy.value = false }
}

async function saveEdit() {
  if (!page.value || !draftTitle.value.trim() || !draftContent.value.trim()) return
  busy.value = true; error.value = ''
  try {
    await knowledgeApi.editWikiPage(props.knowledgeBaseId, page.value.id, page.value.current_version_id!, draftTitle.value.trim(), draftContent.value)
    await openPage(page.value.id)
    message.value = '人工版本已发布，后续自动更新需审核'
  } catch { error.value = '保存失败，页面可能已更新，请刷新后重试' }
  finally { busy.value = false }
}

async function review(decision: 'publish' | 'reject') {
  if (!page.value || !pending.value) return
  busy.value = true; error.value = ''
  try {
    await knowledgeApi.reviewWikiPage(props.knowledgeBaseId, page.value.id, pending.value.id, page.value.current_version_id!, decision)
    await openPage(page.value.id)
    message.value = decision === 'publish' ? '更新已发布' : '更新已拒绝'
  } catch { error.value = '审核失败，页面可能已更新，请刷新后重试' }
  finally { busy.value = false }
}

async function revert(version: WikiVersion) {
  if (!page.value || version.id === page.value.current_version_id) return
  busy.value = true; error.value = ''
  try {
    await knowledgeApi.revertWikiPage(props.knowledgeBaseId, page.value.id, page.value.current_version_id!, version.id)
    await openPage(page.value.id)
    message.value = '已创建并发布回滚版本'
  } catch { error.value = '回滚失败，页面可能已更新，请刷新后重试' }
  finally { busy.value = false }
}

function startEdit() {
  if (!page.value) return
  draftTitle.value = page.value.title
  draftContent.value = page.value.content
  editing.value = true
}

function toggleSectionSources(sectionId: string) {
  const next = new Set(expandedSectionSources.value)
  if (next.has(sectionId)) next.delete(sectionId)
  else next.add(sectionId)
  expandedSectionSources.value = next
}

function legacyDisplayContent(content: string) {
  return content
    .split('\n')
    .filter((line) => !/^\s*\[\^[^\]]+\]:/.test(line))
    .map((line) => line
      .replace(/^\s*#{1,6}\s*/, '')
      .replace(/^\s*[-*]\s+/, '• ')
      .replace(/\s*\[\^[^\]]+\]/g, ''))
    .join('\n')
    .trim()
}

function summarySourceRoute(source: WikiPresentationSource) {
  return {
    path: `/knowledge/${props.knowledgeBaseId}/documents/${source.document_id}`,
    query: {
      version: source.document_version_id,
      ...(source.chunk_id ? { chunk: source.chunk_id } : {}),
    },
  }
}

watch(() => props.knowledgeBaseId, () => {
  page.value = null
  clearSearch()
  void load()
}, { immediate: true })

onBeforeUnmount(() => {
  searchRequestToken += 1
  if (highlightTimer !== null) window.clearTimeout(highlightTimer)
})
</script>

<template>
  <section
    class="wiki-panel"
    aria-label="知识库 Wiki"
  >
    <div class="wiki-toolbar">
      <div><h3>Wiki</h3><p>文档摘要、跨文档主题与目录</p></div>
      <button
        type="button"
        class="quiet"
        @click="load"
      >
        刷新
      </button>
    </div>
    <p
      v-if="error"
      class="wiki-error"
      role="alert"
    >
      {{ error }}
    </p>
    <p
      v-if="message"
      class="wiki-message"
      role="status"
    >
      {{ message }}
    </p>
    <div
      v-if="config"
      class="wiki-settings"
    >
      <div class="setting-line">
        <strong>{{ config.enabled ? '生成已开启' : '生成已关闭' }}</strong><button
          type="button"
          :disabled="busy"
          @click="saveConfig({ enabled: !config.enabled, token_limit: limit })"
        >
          {{ config.enabled ? '关闭 Wiki' : '开启 Wiki' }}
        </button>
      </div>
      <div class="setting-line">
        <label for="wiki-limit">累计 token 上限</label><input
          id="wiki-limit"
          v-model.number="limit"
          type="number"
          min="0"
          step="1000"
        /><button
          type="button"
          :disabled="busy || limit === config.token_limit"
          @click="saveConfig({ token_limit: limit })"
        >
          保存额度
        </button>
      </div>
      <p>已用 {{ config.tokens_charged.toLocaleString() }} · 预留预计 {{ config.tokens_reserved.toLocaleString() }} · 可用 {{ estimate.toLocaleString() }}</p>
      <p
        v-if="paused"
        class="wiki-error"
      >
        {{ paused }} 个任务因额度不足暂停；提高上限后自动继续。
      </p>
    </div>
    <div
      v-if="jobs.length"
      class="wiki-jobs"
    >
      <strong>最近任务</strong><span
        v-for="job in jobs.slice(0, 5)"
        :key="job.id"
      >{{ job.status }} · {{ job.stage }} · {{ job.progress }}% · 预计 {{ (job.estimated_tokens || 0).toLocaleString() }} / {{ job.actual_tokens == null ? `估算占用 ${(job.charged_tokens || 0).toLocaleString()}` : `实际 ${job.actual_tokens.toLocaleString()}` }} token</span>
    </div>
    <form
      class="wiki-search"
      aria-label="Wiki 搜索"
      @submit.prevent="search"
    >
      <input
        v-model="searchQuery"
        type="search"
        aria-label="搜索 Wiki"
        placeholder="搜索主题、摘要、结论和原文语义"
        autocomplete="off"
        @keydown.esc="clearSearch"
      /><button
        type="submit"
        :disabled="searching || !searchQuery.trim()"
      >
        {{ searching ? '搜索中…' : '搜索' }}
      </button><button
        v-if="searchActive || searchQuery"
        type="button"
        class="quiet"
        aria-label="清除 Wiki 搜索"
        @click="clearSearch"
      >
        清除
      </button>
    </form>
    <div class="wiki-layout">
      <nav aria-label="Wiki 页面">
        <template v-if="searchActive">
          <h4>搜索结果</h4><p
            v-if="searchError"
            class="wiki-error"
            role="alert"
          >
            {{ searchError }}
          </p><p
            v-else-if="searching"
            class="empty"
            role="status"
          >
            正在检索 Wiki…
          </p><template v-else-if="searchResult">
            <p
              v-if="searchResult.degraded"
              class="search-warning"
              role="status"
            >
              语义检索暂不可用，当前结果已降级
            </p><p
              v-if="!searchResult.pages.length"
              class="empty"
            >
              没有找到相关 Wiki 页面
            </p><button
              v-for="resultPage in searchResult.pages"
              :key="resultPage.id"
              type="button"
              class="search-result"
              :class="{ active: page?.id === resultPage.id }"
              :data-search-page-id="resultPage.id"
              @click="openSearchResult(resultPage.id, resultPage.matched_claims[0]?.id)"
            >
              <span class="search-result-heading"><small>{{ pageTypeLabel(resultPage.page_type) }}</small><strong>{{ resultPage.title }}</strong></span><span class="match-fields">{{ resultPage.matched_fields.map(matchedFieldLabel).join(' · ') }}</span><span
                v-for="claim in resultPage.matched_claims"
                :key="claim.id"
                class="matched-claim"
              >{{ claim.text }}</span><span
                v-if="resultPage.omitted_claim_count"
                class="omitted-claims"
              >另有 {{ resultPage.omitted_claim_count }} 条相关结论</span>
            </button>
          </template>
        </template><template v-else>
          <h4>页面</h4><p
            v-if="!pages.length"
            class="empty"
          >
            还没有页面。开启 Wiki 后会从就绪文档生成。
          </p><button
            v-for="item in pages"
            :key="item.id"
            type="button"
            :class="{ active: page?.id === item.id }"
            @click="openPage(item.id)"
          >
            <small>{{ pageTypeLabel(item.page_type) }}</small>{{ item.title }}
          </button>
        </template>
      </nav>
      <article
        v-if="page"
        class="wiki-article"
      >
        <div class="article-actions">
          <h3>{{ page.title }}</h3><button
            type="button"
            @click="startEdit"
          >
            编辑
          </button>
        </div>
        <template v-if="editing">
          <label>标题<input
            v-model="draftTitle"
            maxlength="200"
          /></label><label>正文<textarea
            v-model="draftContent"
            rows="16"
          ></textarea></label><div class="button-row">
            <button
              type="button"
              :disabled="busy"
              @click="saveEdit"
            >
              保存人工版本
            </button><button
              type="button"
              @click="editing = false"
            >
              取消
            </button>
          </div>
        </template>
        <template v-else>
          <template v-if="page.origin === 'pipeline' && page.summary_sections.length">
            <dl
              v-if="page.metadata.length"
              class="wiki-metadata"
            >
              <div
                v-for="item in page.metadata"
                :key="item.key"
              >
                <dt>{{ item.label }}</dt><dd>{{ item.value }}</dd>
              </div>
            </dl>
            <section
              v-for="section in page.summary_sections"
              :key="section.id"
              class="summary-section"
              :data-summary-section-id="section.id"
            >
              <h4>{{ section.title }}</h4><p>{{ section.text }}</p><span
                :class="section.trust_state"
              >{{ section.trust_state === 'verified' ? '已核实' : '待核实' }}</span><button
                type="button"
                class="quiet section-sources-toggle"
                @click="toggleSectionSources(section.id)"
              >
                {{ expandedSectionSources.has(section.id) ? '收起来源' : `查看 ${section.sources.length} 个来源` }}
              </button><div
                v-if="expandedSectionSources.has(section.id)"
                class="section-sources"
              >
                <div
                  v-for="source in section.sources"
                  :key="source.document_version_id + source.chunk_id"
                >
                  <RouterLink
                    class="source-link"
                    :to="summarySourceRoute(source)"
                  >
                    {{ source.document_title }} · {{ source.valid ? '查看来源' : '来源失效' }}
                  </RouterLink><small>文档版本 {{ source.document_version_id }} · 分块 {{ source.chunk_id || '无' }} · {{ source.heading_path.join(' / ') || '正文' }}<template v-if="source.page_start"> · 第 {{ source.page_start }} 页</template> · {{ source.valid ? '来源有效' : '来源失效' }}</small>
                </div>
              </div>
            </section>
          </template>
          <pre
            v-else
            class="page-body"
          >{{ legacyDisplayContent(page.content) }}</pre><section
            v-if="page.claims.length"
            class="claims"
          >
            <h4>关键事实</h4><div
              v-for="claim in page.claims"
              :key="claim.id"
              class="claim"
              :class="{ 'claim-highlight': highlightClaimId === claim.id }"
              :data-claim-id="claim.id"
            >
              <span :class="claim.trust_state">{{ claim.trust_state === 'verified' ? '已核实' : '待核实' }}</span><p>{{ claim.text }}</p><div
                v-for="source in claim.sources"
                :key="source.document_version_id + source.chunk_id"
              >
                <button
                  type="button"
                  class="source-link"
                  @click="expandedSource = expandedSource === claim.id + source.document_version_id ? null : claim.id + source.document_version_id"
                >
                  {{ source.document_title }} · {{ source.valid ? '查看来源' : '来源失效' }}
                </button><small v-if="expandedSource === claim.id + source.document_version_id">文档版本 {{ source.document_version_id }} · 分块 {{ source.chunk_id || '无' }} · {{ source.heading_path.join(' / ') || '正文' }}<template v-if="source.page_start"> · 第 {{ source.page_start }} 页</template> · {{ source.valid ? '来源有效' : '来源失效' }}</small>
              </div>
            </div>
          </section>
        </template>
        <section class="history">
          <h4>版本历史</h4><div
            v-for="version in versions"
            :key="version.id"
            class="version-row"
          >
            <button
              type="button"
              @click="selectedVersion = version.id"
            >
              v{{ version.version_no }} · {{ version.origin }} · {{ version.review_state }}
            </button><button
              type="button"
              :disabled="busy || version.id === page.current_version_id || version.review_state === 'pending_review'"
              @click="revert(version)"
            >
              回滚到此版
            </button>
          </div>
        </section>
        <section
          v-if="candidate"
          class="compare"
        >
          <h4>{{ candidate.review_state === 'pending_review' ? '待审核更新' : '版本对比' }}</h4><div class="compare-grid">
            <div><strong>当前发布版</strong><pre>{{ page.content }}</pre></div><div><strong>v{{ candidate.version_no }}</strong><pre>{{ candidate.content }}</pre></div>
          </div><div
            v-if="candidate.review_state === 'pending_review'"
            class="button-row"
          >
            <button
              type="button"
              :disabled="busy"
              @click="review('publish')"
            >
              发布更新
            </button><button
              type="button"
              :disabled="busy"
              @click="review('reject')"
            >
              拒绝更新
            </button>
          </div>
        </section>
      </article>
      <p
        v-else
        class="empty"
      >
        选择左侧页面查看内容、来源和历史版本。
      </p>
    </div>
  </section>
</template>

<style scoped>
.wiki-panel{padding-top:18px;color:var(--agent-ink);min-width:0}.wiki-toolbar,.setting-line,.article-actions,.button-row,.version-row{display:flex;align-items:center;justify-content:space-between;gap:12px}.wiki-toolbar p,.wiki-settings p{font-size:12px;color:var(--agent-muted);margin:5px 0}.wiki-toolbar h3,.wiki-article h3{margin:0;font-size:18px}.wiki-settings{border:1px solid #e1e1e8;border-radius:8px;padding:15px;margin:17px 0}.setting-line{justify-content:flex-start;flex-wrap:wrap;margin-bottom:10px;font-size:13px}.setting-line strong{margin-right:auto}.setting-line input{width:140px;padding:7px;border:1px solid #d5d5dd;border-radius:5px}.wiki-panel button{cursor:pointer;border:1px solid #d8d8e0;background:#fff;color:#454299;padding:7px 10px;border-radius:6px;font-size:12px}.wiki-panel button:disabled{opacity:.5;cursor:default}.wiki-error{color:#a22920!important;font-size:12px}.wiki-message{color:#227052;font-size:12px}.wiki-jobs{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0;font-size:11px;color:#666}.wiki-jobs span{background:#f2f2f7;padding:4px 7px;border-radius:4px}.wiki-search{display:flex;align-items:center;gap:8px;margin:14px 0;max-width:720px}.wiki-search input{min-width:0;flex:1;border:1px solid #cfcfda;border-radius:7px;padding:9px 11px;font:inherit}.wiki-layout{display:grid;grid-template-columns:minmax(210px,280px) minmax(0,1fr);gap:22px;border-top:1px solid #e3e3e8;padding-top:18px}.wiki-layout nav{display:flex;min-width:0;flex-direction:column;align-items:stretch;gap:5px}.wiki-layout nav h4,.wiki-article h4{margin:0 0 10px;font-size:13px}.wiki-layout nav button{text-align:left;color:#333;overflow-wrap:anywhere}.wiki-layout nav button.active{background:#eeeef8;border-color:#9995d9}.wiki-layout nav small{display:block;color:#6863a1;margin-bottom:3px}.search-warning{margin:0 0 7px;padding:7px 9px;border-radius:6px;background:#fff4dc;color:#815200;font-size:12px}.search-result{display:flex;flex-direction:column;align-items:stretch;gap:5px}.search-result-heading{display:flex;align-items:baseline;gap:7px}.search-result-heading small{display:inline;margin:0}.match-fields{color:#655fa4;font-size:11px}.matched-claim,.omitted-claims{color:#555;font-size:12px;line-height:1.45}.omitted-claims{color:#777}.wiki-article{min-width:0}.wiki-article label{display:block;font-size:12px;margin:10px 0}.wiki-article input,.wiki-article textarea{display:block;width:100%;margin-top:5px;border:1px solid #d5d5dd;border-radius:5px;padding:9px;font:inherit}.page-body,.compare pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.7 inherit;background:#fafafd;border:1px solid #e6e6eb;border-radius:6px;padding:14px}.claims,.history,.compare{margin-top:22px;border-top:1px solid #e7e7ec;padding-top:15px}.claim{margin:12px 0;padding:3px 5px;border-radius:5px;font-size:12px;transition:background-color .2s ease}.claim-highlight{background:#fff1b8}.claim p{margin:5px 0}.claim small{display:block;color:#777}.verified{color:#267356}.needs_review{color:#b05b20}.version-row{margin:5px 0}.version-row button:first-child{flex:1;text-align:left}.compare-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}.compare-grid pre{max-height:300px;overflow:auto}.button-row{justify-content:flex-end}.empty{color:#777;font-size:13px}@media(max-width:650px){.wiki-search{flex-wrap:wrap}.wiki-search input{flex-basis:100%}.wiki-layout,.compare-grid{grid-template-columns:1fr}.wiki-layout nav{max-height:320px;overflow:auto}}
.wiki-metadata{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 16px}.wiki-metadata div{display:flex;gap:5px;padding:6px 8px;background:#f6f6fb;border:1px solid #e1e1eb;border-radius:5px;font-size:12px}.wiki-metadata dt{color:#666}.wiki-metadata dd{margin:0;color:#333}.summary-section{margin:16px 0;padding:14px;border:1px solid #e7e7ec;border-radius:7px;background:#fff}.summary-section h4{margin-bottom:6px}.summary-section p{margin:0 0 9px;white-space:pre-wrap;line-height:1.7}.section-sources-toggle{margin-left:10px}.section-sources{margin-top:9px;font-size:12px}.section-sources small{display:block;color:#777}@media(max-width:650px){.wiki-metadata{display:grid;grid-template-columns:1fr}.wiki-metadata div{overflow-wrap:anywhere}.section-sources-toggle{margin-left:6px}}
.source-link{border:0!important;padding:3px 0!important;text-decoration:underline;background:transparent!important}
</style>
