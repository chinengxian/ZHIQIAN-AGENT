export type MessageStatus = 'streaming' | 'complete' | 'stopped' | 'error'

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  status: MessageStatus
  retrievalStatus?: 'retrieving' | 'generating'
  sources?: ChatSource[]
}

export type KnowledgeScope =
  | { mode: 'all_enabled' }
  | { mode: 'selected'; knowledge_base_ids: string[] }

export interface ChatSource {
  citation_id: string
  chunk_id: string
  document_id: string
  document_version_id: string
  title: string
  filename: string
  page_start: number | null
  page_end: number | null
  heading_path: string[]
  excerpt: string
  score: number
  rank: number
}

export type ChatEvent =
  | { event: 'message'; data: { content: string } }
  | { event: 'done'; data: Record<string, never> }
  | { event: 'error'; data: { code: string; message: string } }
  | { event: 'status'; data: { stage: 'retrieving' | 'generating' } }
  | { event: 'sources'; data: { items: ChatSource[] } }
