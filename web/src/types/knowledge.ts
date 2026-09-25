export interface KnowledgeBase {
  id: string
  name: string
  description: string
  enabled: boolean
  document_count: number
  processing_count: number
}

export interface IngestionJob {
  id: string
  stage: string
  status: string
  progress: number
  error_code: string | null
  error_reference: string | null
}

export interface KnowledgeDocument {
  id: string
  knowledge_base_id: string
  title: string
  filename: string
  mime_type: string
  status: string
  active_version_id: string | null
  job: IngestionJob | null
}

export interface DocumentProgress {
  type: 'document_progress'
  document_id: string
  job_id: string
  stage: string
  status: string
  progress: number
  error_code: string | null
  error_reference: string | null
}

export interface WikiConfig {
  knowledge_base_id: string
  enabled: boolean
  token_limit: number
  tokens_reserved: number
  tokens_charged: number
}

export interface WikiPageSummary {
  id: string
  knowledge_base_id: string
  slug: string
  page_type: 'summary' | 'topic' | 'index'
  title: string
  current_version_id: string | null
}

export interface WikiClaim {
  id: string
  claim_key: string
  text: string
  trust_state: 'verified' | 'needs_review'
  reason: string | null
  sources: Array<{
    document_id: string
    document_version_id: string
    chunk_id: string | null
    document_title: string
    heading_path: string[]
    page_start: number | null
    page_end: number | null
    valid: boolean
  }>
}

export interface WikiPage extends WikiPageSummary {
  version_no: number
  content: string
  summary: string
  origin: 'pipeline' | 'user' | 'revert'
  claims: WikiClaim[]
}

export interface WikiVersion {
  id: string
  page_id: string
  version_no: number
  title: string
  content: string
  summary: string
  origin: 'pipeline' | 'user' | 'revert'
  review_state: 'published' | 'pending_review' | 'rejected' | 'superseded'
  base_published_version_id: string | null
  created_at: string
}

export interface WikiJob {
  id: string
  job_type: string
  status: string
  stage: string
  progress: number
  error_code: string | null
  estimated_tokens: number
  actual_tokens: number | null
  charged_tokens: number
  created_at: string
}
