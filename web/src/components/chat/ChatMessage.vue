<script setup lang="ts">
import { computed } from 'vue'
import type { ChatMessage } from '../../types/chat'

const props = defineProps<{ message: ChatMessage }>()
const emit = defineEmits<{ sourceClick: [citationId: string] }>()
const segments = computed(() => props.message.content.split(/(\[\d{1,3}\])/g).filter(Boolean).map((text) => ({
  text,
  citationId: /^\[\d{1,3}\]$/.test(text) ? text : null,
})))
const sourceIds = computed(() => new Set(props.message.sources?.map((source) => source.citation_id) ?? []))
</script>

<template>
  <article
    class="message-row"
    :class="`message-row--${message.role}`"
  >
    <div class="message-bubble">
      <span class="message-content"><template
        v-for="(segment, index) in segments"
        :key="index"
      ><button
        v-if="segment.citationId && sourceIds.has(segment.citationId)"
        type="button"
        class="citation"
        :aria-label="`查看来源 ${segment.citationId.slice(1, -1)}`"
        @click="emit('sourceClick', segment.citationId)"
      >{{ segment.text }}</button><span v-else>{{ segment.text }}</span></template></span>
      <small v-if="message.role === 'assistant' && message.status === 'streaming' && !message.content && message.retrievalStatus === 'retrieving'">正在检索知识库…</small>
      <span
        v-if="message.role === 'assistant' && message.status === 'streaming'"
        class="streaming-cursor"
        data-testid="streaming-cursor"
        aria-label="正在生成"
      ></span>
      <small v-if="message.status === 'stopped'">已停止</small>
      <small v-if="message.status === 'error' && !message.content">回复失败</small>
      <button
        v-if="message.role === 'assistant' && message.sources?.length"
        type="button"
        class="source-action"
        @click="emit('sourceClick', message.sources[0]!.citation_id)"
      >
        <span
          class="mdi mdi-text-box-search-outline"
          aria-hidden="true"
        ></span> {{ message.sources.length }} 个来源
      </button>
    </div>
  </article>
</template>

<style scoped>
.message-row { display:flex; width:100%; animation:message-in .24s ease-out both; }
.message-row--user { justify-content:flex-end; }
.message-bubble { max-width:min(78%,680px); color:var(--agent-ink); font-size:15px; line-height:1.62; }
.message-row--user .message-bubble { padding:11px 16px; border-radius:19px 19px 5px 19px; background:#fff; box-shadow:0 8px 28px rgb(0 0 0 / 7%); }
.message-row--assistant .message-bubble { max-width:760px; padding:7px 2px; }
.message-content { white-space:pre-wrap; overflow-wrap:anywhere; }
.streaming-cursor { display:inline-block; width:7px; height:17px; margin-left:3px; vertical-align:-3px; border-radius:3px; background:var(--agent-purple); animation:blink 1s ease-in-out infinite; }
small { display:block; margin-top:5px; color:var(--agent-muted); font-size:11px; }
.citation { display:inline; padding:0 2px; border:0; border-radius:3px; background:#e9e9f8; color:#3e3a9a; font:inherit; font-size:12px; cursor:pointer; }
.source-action { display:flex; align-items:center; gap:5px; margin-top:9px; padding:0; border:0; background:transparent; color:#4d4ba8; font-size:12px; cursor:pointer; }
@keyframes blink { 50% { opacity:.22; } }
@keyframes message-in { from { opacity:0; transform:translateY(5px); } }
@media (max-width:640px) { .message-bubble { max-width:88%; } }
</style>
