import { reactive, ref } from 'vue'

import { ChatStreamError, streamChat } from '../services/chatApi'
import type { ChatMessage } from '../types/chat'

let nextMessageId = 0

function messageId(): string {
  nextMessageId += 1
  return `message-${nextMessageId}`
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

export function useChat() {
  const conversationId = crypto.randomUUID()
  const messages = ref<ChatMessage[]>([])
  const isStreaming = ref(false)
  const errorMessage = ref<string | null>(null)
  let controller: AbortController | null = null

  async function send(rawContent: string): Promise<void> {
    const content = rawContent.trim()
    if (!content || isStreaming.value) return

    errorMessage.value = null
    messages.value.push({ id: messageId(), role: 'user', content, status: 'complete' })
    const assistant = reactive<ChatMessage>({
      id: messageId(),
      role: 'assistant',
      content: '',
      status: 'streaming',
    })
    messages.value.push(assistant)
    isStreaming.value = true
    const activeController = new AbortController()
    controller = activeController

    try {
      await streamChat(conversationId, content, {
        onMessage(chunk) {
          assistant.content += chunk
        },
        onDone() {
          assistant.status = 'complete'
        },
      }, activeController.signal)
      if (assistant.status === 'streaming') assistant.status = 'complete'
    } catch (error) {
      if (isAbortError(error)) {
        assistant.status = 'stopped'
      } else {
        assistant.status = 'error'
        errorMessage.value = error instanceof ChatStreamError
          ? error.message
          : '发生了意外错误，请重试'
      }
    } finally {
      if (controller === activeController) {
        controller = null
        isStreaming.value = false
      }
    }
  }

  function stop(): void {
    controller?.abort()
  }

  function clearError(): void {
    errorMessage.value = null
  }

  return { messages, isStreaming, errorMessage, send, stop, clearError }
}
