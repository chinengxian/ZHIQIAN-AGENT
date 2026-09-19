<script setup lang="ts">
import { computed, ref } from 'vue'

const props = defineProps<{ disabled: boolean; streaming: boolean }>()
const emit = defineEmits<{ send: [value: string]; stop: [] }>()
const value = ref('')
const canSend = computed(() => !props.disabled && value.value.trim().length > 0)

function send(): void {
  const content = value.value.trim()
  if (!content || props.disabled) return
  emit('send', content)
  value.value = ''
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key !== 'Enter' || event.shiftKey) return
  event.preventDefault()
  send()
}
</script>

<template>
  <footer class="composer-wrap">
    <div class="composer">
      <textarea
        v-model="value"
        aria-label="消息输入"
        placeholder="给 Agent 发送消息…"
        rows="1"
        :disabled="disabled"
        @keydown="onKeydown"
      ></textarea>
      <button
        v-if="streaming"
        type="button"
        aria-label="停止生成"
        class="action-button"
        @click="emit('stop')"
      >
        ■
      </button>
      <button
        v-else
        type="button"
        aria-label="发送消息"
        class="action-button"
        :disabled="!canSend"
        @click="send"
      >
        ↑
      </button>
    </div>
    <p>Enter 发送 · Shift + Enter 换行</p>
  </footer>
</template>

<style scoped>
.composer-wrap { position:fixed; inset:auto 0 0; z-index:3; padding:20px 18px 18px; background:linear-gradient(180deg,rgb(245 245 247 / 0%),rgb(245 245 247 / 92%) 28%,#f5f5f7 100%); }
.composer { width:min(880px,100%); min-height:62px; margin:auto; display:flex; align-items:flex-end; gap:10px; padding:8px 10px 8px 18px; border:1px solid rgb(0 0 0 / 9%); border-radius:24px; background:rgb(255 255 255 / 94%); box-shadow:0 18px 60px rgb(0 0 0 / 13%); }
textarea { flex:1; min-height:38px; max-height:120px; align-self:center; resize:none; border:0; outline:0; padding:9px 0; color:var(--agent-ink); background:transparent; line-height:1.45; }
.action-button { width:42px; height:42px; flex:0 0 auto; border:0; border-radius:15px; color:#fff; background:linear-gradient(145deg,#7567ff,#574be7); box-shadow:0 8px 18px rgb(99 91 255 / 28%); cursor:pointer; }
.action-button:disabled { opacity:.34; cursor:default; box-shadow:none; }
.action-button:focus-visible, textarea:focus-visible { outline:3px solid rgb(99 91 255 / 28%); outline-offset:2px; }
p { margin:7px auto 0; color:#6e6e73; text-align:center; font-size:11px; }
@media (max-width:640px) { .composer-wrap { padding:14px 10px max(12px,env(safe-area-inset-bottom)); } .composer { border-radius:20px; } }
</style>
