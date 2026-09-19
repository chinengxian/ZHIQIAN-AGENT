<script setup lang="ts">
import type { ChatMessage } from '../../types/chat'

defineProps<{ message: ChatMessage }>()
</script>

<template>
  <article
    class="message-row"
    :class="`message-row--${message.role}`"
  >
    <div class="message-bubble">
      <span class="message-content">{{ message.content }}</span>
      <span
        v-if="message.role === 'assistant' && message.status === 'streaming'"
        class="streaming-cursor"
        data-testid="streaming-cursor"
        aria-label="正在生成"
      ></span>
      <small v-if="message.status === 'stopped'">已停止</small>
      <small v-if="message.status === 'error' && !message.content">回复失败</small>
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
@keyframes blink { 50% { opacity:.22; } }
@keyframes message-in { from { opacity:0; transform:translateY(5px); } }
@media (max-width:640px) { .message-bubble { max-width:88%; } }
</style>
