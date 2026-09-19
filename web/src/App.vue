<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'

import ChatComposer from './components/chat/ChatComposer.vue'
import ChatEmptyState from './components/chat/ChatEmptyState.vue'
import ChatHeader from './components/chat/ChatHeader.vue'
import ChatMessage from './components/chat/ChatMessage.vue'
import { useChat } from './composables/useChat'

const { messages, isStreaming, errorMessage, send, stop, clearError } = useChat()
const endOfConversation = ref<HTMLElement | null>(null)

watch(messages, async () => {
  await nextTick()
  endOfConversation.value?.scrollIntoView?.({ behavior: 'smooth', block: 'end' })
}, { deep: true })
</script>

<template>
  <v-app>
    <ChatHeader />
    <main class="workspace">
      <ChatEmptyState v-if="messages.length === 0" />
      <section
        v-else
        class="conversation"
        aria-label="对话消息"
        aria-live="polite"
      >
        <ChatMessage
          v-for="message in messages"
          :key="message.id"
          :message="message"
        />
        <div
          ref="endOfConversation"
          aria-hidden="true"
        ></div>
      </section>
    </main>
    <div
      v-if="errorMessage"
      class="error-banner"
      role="alert"
    >
      <span>{{ errorMessage }}</span>
      <button
        type="button"
        aria-label="关闭错误提示"
        @click="clearError"
      >
        ×
      </button>
    </div>
    <ChatComposer
      :disabled="isStreaming"
      :streaming="isStreaming"
      @send="send"
      @stop="stop"
    />
  </v-app>
</template>

<style scoped>
:global(body) { background:radial-gradient(circle at 50% -15%,#fff 0,#f7f7f9 38%,#ececf1 100%); }
.workspace { width:min(900px,calc(100% - 36px)); min-height:calc(100vh - 58px); margin:0 auto; display:flex; padding-bottom:150px; }
.conversation { width:100%; display:flex; flex-direction:column; gap:28px; padding:54px 10px 24px; }
.error-banner { position:fixed; z-index:5; right:22px; bottom:115px; display:flex; align-items:center; gap:18px; max-width:min(420px,calc(100vw - 32px)); padding:12px 14px 12px 16px; border:1px solid rgb(255 59 48 / 16%); border-radius:15px; color:#9f1c16; background:rgb(255 255 255 / 94%); box-shadow:0 12px 38px rgb(0 0 0 / 11%); backdrop-filter:blur(18px); font-size:13px; }
.error-banner button { width:26px; height:26px; border:0; border-radius:8px; color:inherit; background:rgb(255 59 48 / 8%); cursor:pointer; }
@media (max-width:640px) { .workspace { width:calc(100% - 24px); } .conversation { gap:23px; padding-top:32px; } .error-banner { right:12px; bottom:106px; } }
</style>
