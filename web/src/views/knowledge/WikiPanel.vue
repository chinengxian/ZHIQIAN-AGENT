<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { knowledgeApi } from '../../services/knowledgeApi'
import type { WikiConfig, WikiJob, WikiPage, WikiPageSummary, WikiVersion } from '../../types/knowledge'

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
const candidate = computed(() => versions.value.find((item) => item.id === selectedVersion.value) ?? null)
const pending = computed(() => versions.value.find((item) => item.review_state === 'pending_review') ?? null)
const estimate = computed(() => Math.max(0, (config.value?.token_limit ?? 0) - (config.value?.tokens_charged ?? 0) - (config.value?.tokens_reserved ?? 0)))
const paused = computed(() => jobs.value.filter((item) => item.status === 'paused_budget').length)

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

watch(() => props.knowledgeBaseId, () => { page.value = null; void load() }, { immediate: true })
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
    <div class="wiki-layout">
      <nav aria-label="Wiki 页面">
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
          <small>{{ item.page_type === 'index' ? '目录' : item.page_type === 'topic' ? '主题' : '摘要' }}</small>{{ item.title }}
        </button>
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
          <pre class="page-body">{{ page.content }}</pre><section
            v-if="page.claims.length"
            class="claims"
          >
            <h4>来源与核实状态</h4><div
              v-for="claim in page.claims"
              :key="claim.id"
              class="claim"
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
.wiki-panel{padding-top:18px;color:var(--agent-ink)}.wiki-toolbar,.setting-line,.article-actions,.button-row,.version-row{display:flex;align-items:center;justify-content:space-between;gap:12px}.wiki-toolbar p,.wiki-settings p{font-size:12px;color:var(--agent-muted);margin:5px 0}.wiki-toolbar h3,.wiki-article h3{margin:0;font-size:18px}.wiki-settings{border:1px solid #e1e1e8;border-radius:8px;padding:15px;margin:17px 0}.setting-line{justify-content:flex-start;flex-wrap:wrap;margin-bottom:10px;font-size:13px}.setting-line strong{margin-right:auto}.setting-line input{width:140px;padding:7px;border:1px solid #d5d5dd;border-radius:5px}.wiki-panel button{cursor:pointer;border:1px solid #d8d8e0;background:#fff;color:#454299;padding:7px 10px;border-radius:6px;font-size:12px}.wiki-panel button:disabled{opacity:.5;cursor:default}.wiki-error{color:#a22920!important;font-size:12px}.wiki-message{color:#227052;font-size:12px}.wiki-jobs{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0;font-size:11px;color:#666}.wiki-jobs span{background:#f2f2f7;padding:4px 7px;border-radius:4px}.wiki-layout{display:grid;grid-template-columns:180px minmax(0,1fr);gap:22px;border-top:1px solid #e3e3e8;padding-top:18px}.wiki-layout nav{display:flex;flex-direction:column;align-items:stretch;gap:5px}.wiki-layout nav h4,.wiki-article h4{margin:0 0 10px;font-size:13px}.wiki-layout nav button{text-align:left;color:#333;overflow-wrap:anywhere}.wiki-layout nav button.active{background:#eeeef8;border-color:#9995d9}.wiki-layout nav small{display:block;color:#6863a1;margin-bottom:3px}.wiki-article{min-width:0}.wiki-article label{display:block;font-size:12px;margin:10px 0}.wiki-article input,.wiki-article textarea{display:block;width:100%;margin-top:5px;border:1px solid #d5d5dd;border-radius:5px;padding:9px;font:inherit}.page-body,.compare pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.7 inherit;background:#fafafd;border:1px solid #e6e6eb;border-radius:6px;padding:14px}.claims,.history,.compare{margin-top:22px;border-top:1px solid #e7e7ec;padding-top:15px}.claim{margin:12px 0;font-size:12px}.claim p{margin:5px 0}.claim small{display:block;color:#777}.verified{color:#267356}.needs_review{color:#b05b20}.version-row{margin:5px 0}.version-row button:first-child{flex:1;text-align:left}.compare-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}.compare-grid pre{max-height:300px;overflow:auto}.button-row{justify-content:flex-end}.empty{color:#777;font-size:13px}@media(max-width:650px){.wiki-layout,.compare-grid{grid-template-columns:1fr}.wiki-layout nav{max-height:200px;overflow:auto}}
.source-link{border:0!important;padding:3px 0!important;text-decoration:underline;background:transparent!important}
</style>
