<script setup lang="ts">
import { computed, ref } from 'vue'

import { knowledgeApi } from '../../services/knowledgeApi'
import type { KnowledgeScope } from '../../types/chat'
import type { KnowledgeBase } from '../../types/knowledge'

const props = defineProps<{ scope: KnowledgeScope; disabled: boolean }>()
const emit = defineEmits<{ change: [scope: KnowledgeScope] }>()
const open = ref(false)
const loading = ref(false)
const error = ref(false)
const bases = ref<KnowledgeBase[]>([])
const enabled = computed(() => bases.value.filter((base) => base.enabled))
const selected = computed(() => props.scope.mode === 'selected' ? props.scope.knowledge_base_ids : [])
const label = computed(() => props.scope.mode === 'all_enabled' ? '全部知识库' : `已选 ${selected.value.length} 个知识库`)

async function toggle(): Promise<void> {
  open.value = !open.value
  if (!open.value) return
  loading.value = true
  error.value = false
  try {
    bases.value = await knowledgeApi.listBases()
    if (props.scope.mode === 'selected') {
      const valid = props.scope.knowledge_base_ids.filter((id) => enabled.value.some((base) => base.id === id))
      if (valid.length !== props.scope.knowledge_base_ids.length) emit('change', valid.length ? { mode: 'selected', knowledge_base_ids: valid } : { mode: 'all_enabled' })
    }
  } catch {
    error.value = true
  } finally {
    loading.value = false
  }
}

function choose(id: string): void {
  const next = selected.value.includes(id) ? selected.value.filter((item) => item !== id) : [...selected.value, id]
  emit('change', next.length ? { mode: 'selected', knowledge_base_ids: next } : { mode: 'all_enabled' })
}
</script>

<template>
  <div class="scope-picker">
    <button
      type="button"
      class="scope-trigger"
      :disabled="disabled"
      :aria-expanded="open"
      aria-label="选择知识范围"
      @click="toggle"
    >
      <span
        class="mdi mdi-bookshelf"
        aria-hidden="true"
      ></span>
      <span>{{ label }}</span>
      <span
        class="mdi mdi-chevron-down"
        aria-hidden="true"
      ></span>
    </button>
    <div
      v-if="open"
      class="scope-menu"
      role="group"
      aria-label="知识范围"
      @keydown.esc="open = false"
    >
      <div
        v-if="loading"
        class="scope-note"
      >
        正在加载知识库…
      </div>
      <div
        v-else-if="error"
        class="scope-note"
        role="alert"
      >
        知识库列表不可用
      </div>
      <template v-else>
        <label class="scope-option">
          <input
            type="radio"
            name="knowledge-scope"
            :checked="scope.mode === 'all_enabled'"
            @change="emit('change', { mode: 'all_enabled' })"
          />
          全部启用知识库
        </label>
        <div
          v-if="enabled.length === 0"
          class="scope-note"
        >
          暂无启用的知识库
        </div>
        <label
          v-for="base in enabled"
          :key="base.id"
          class="scope-option"
        >
          <input
            type="checkbox"
            :checked="selected.includes(base.id)"
            @change="choose(base.id)"
          />
          <span>{{ base.name }}</span>
        </label>
      </template>
      <button
        type="button"
        class="scope-done"
        @click="open = false"
      >
        完成
      </button>
    </div>
  </div>
</template>

<style scoped>
.scope-picker { position:relative; }
.scope-trigger { display:flex; align-items:center; gap:6px; min-height:34px; padding:0 8px; border:1px solid #dedee4; border-radius:6px; background:#fff; color:#34343a; cursor:pointer; font-size:12px; white-space:nowrap; }
.scope-trigger:disabled { opacity:.55; cursor:not-allowed; }
.scope-trigger .mdi { font-size:16px; }
.scope-menu { position:absolute; top:calc(100% + 6px); right:0; z-index:6; width:min(290px,calc(100vw - 32px)); max-height:300px; overflow-y:auto; padding:7px; border:1px solid #dedee4; border-radius:7px; background:#fff; box-shadow:0 10px 30px #0002; }
.scope-option { display:flex; align-items:center; gap:10px; min-height:38px; padding:5px 8px; border-radius:5px; color:#303039; font-size:13px; cursor:pointer; }
.scope-option:hover { background:#f3f3f7; }
.scope-option input { accent-color:#4d4ba8; }
.scope-option span { overflow-wrap:anywhere; }
.scope-note { padding:9px 8px; color:#6b6b75; font-size:12px; }
.scope-done { display:block; width:100%; margin-top:6px; padding:7px; border:0; border-radius:5px; background:#ececf7; color:#37339a; cursor:pointer; font-size:12px; }
</style>
