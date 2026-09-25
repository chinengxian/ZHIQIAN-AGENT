<script setup lang="ts">
import { computed } from 'vue'

import type { ChatSource } from '../../types/chat'

const props = defineProps<{ sources: ChatSource[]; activeId: string | null }>()
const emit = defineEmits<{ close: []; select: [citationId: string] }>()
const active = computed(() => props.sources.find((source) => source.citation_id === props.activeId) ?? props.sources[0])

function location(source: ChatSource): string {
  const path = source.heading_path.join(' / ')
  if (source.filename.toLowerCase().endsWith('.pdf') && source.page_start !== null) {
    return source.page_end !== null && source.page_end !== source.page_start
      ? `第 ${source.page_start}–${source.page_end} 页`
      : `第 ${source.page_start} 页`
  }
  return path || source.title
}
</script>

<template>
  <div
    class="source-overlay"
    @click.self="emit('close')"
  >
    <aside
      class="source-drawer"
      role="dialog"
      aria-modal="true"
      aria-label="引用来源"
    >
      <header>
        <h2>引用来源</h2>
        <button
          type="button"
          aria-label="关闭来源"
          @click="emit('close')"
        >
          <span
            class="mdi mdi-close"
            aria-hidden="true"
          ></span>
        </button>
      </header>
      <div
        v-if="active"
        class="source-detail"
      >
        <div class="source-index">
          {{ active.citation_id }}
        </div>
        <h3>{{ active.title }}</h3>
        <p class="source-location">
          {{ location(active) }}
        </p>
        <p class="source-excerpt">
          {{ active.excerpt }}
        </p>
      </div>
      <div
        v-if="sources.length > 1"
        class="source-list"
      >
        <h3>本次回答的来源</h3>
        <button
          v-for="source in sources"
          :key="source.citation_id"
          type="button"
          @click="emit('select', source.citation_id)"
        >
          {{ source.citation_id }} {{ source.title }} · {{ location(source) }}
        </button>
      </div>
    </aside>
  </div>
</template>

<style scoped>
.source-overlay { position:fixed; inset:0; z-index:20; background:#0005; }
.source-drawer { position:absolute; inset:0 0 0 auto; width:min(440px,100vw); overflow-y:auto; padding:22px; background:#fff; box-shadow:-12px 0 35px #0002; }
header { display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid #e9e9ec; padding-bottom:15px; }
h2 { margin:0; font-size:17px; }
header button { width:32px; height:32px; border:0; background:transparent; cursor:pointer; font-size:20px; }
.source-detail { padding-top:23px; }
.source-index { color:#4d4ba8; font-weight:700; }
h3 { margin:9px 0; font-size:16px; overflow-wrap:anywhere; }
.source-location { color:#656570; font-size:13px; }
.source-excerpt { white-space:pre-wrap; overflow-wrap:anywhere; line-height:1.6; font-size:14px; }
.source-list { border-top:1px solid #e9e9ec; padding-top:15px; }
.source-list h3 { font-size:13px; }
.source-list button { display:block; width:100%; padding:8px 0; border:0; background:transparent; text-align:left; font-size:13px; }
</style>
