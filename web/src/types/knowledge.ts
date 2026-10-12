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
  document_version_id?: string | null
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

export interface DocumentVersion {
  id: string
  version_no: number
  file_size: number
  status: string
  parser_name: string | null
  created_at: string
  ready_at: string | null
  error_code: string | null
  error_reference: string | null
}

export interface DocumentDetail extends KnowledgeDocument {
  created_at: string
  updated_at: string
  active_version: DocumentVersion | null
  latest_job: IngestionJob | null
}

export interface DocumentVersionPage {
  items: DocumentVersion[]
  next_cursor: string | null
}

export interface DocumentPreviewChunk {
  chunk_id: string
  ordinal: number
  display_ordinal: number
  content: string
  heading_path: string[]
  page_start: number | null
  page_end: number | null
  anchor_chunk_id: string | null
  focus_start: number | null
  focus_end: number | null
}

export interface DocumentPreview {
  document_id: string
  version_id: string | null
  items: DocumentPreviewChunk[]
  next_cursor: string | null
  preview_unavailable_reason: 'processing' | 'failed' | 'content_removed' | null
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
  index_state: 'not_required' | 'pending' | 'indexing' | 'ready' | 'failed'
  index_error_code: string | null
}

export interface WikiClaim {
  id: string
  claim_key: string
  text: string
  position: number
  claim_type: 'summary_fact' | 'topic_fact' | 'source_excerpt' | 'manual'
  trust_state: 'verified' | 'needs_review'
  reason: string | null
  sources: WikiPresentationSource[]
}

export interface WikiPresentationSource {
  document_id: string
  document_version_id: string
  chunk_id: string | null
  document_title: string
  heading_path: string[]
  page_start: number | null
  page_end: number | null
  valid: boolean
}

export interface WikiSummarySection {
  id: string
  title: string
  text: string
  position: number
  trust_state: 'verified' | 'needs_review'
  reason: string | null
  sources: WikiPresentationSource[]
}

export interface WikiMetadataItem {
  key: string
  label: string
  value: string
}

export interface WikiPage extends WikiPageSummary {
  version_no: number
  content: string
  summary: string
  origin: 'pipeline' | 'user' | 'revert'
  summary_sections: WikiSummarySection[]
  metadata: WikiMetadataItem[]
  claims: WikiClaim[]
  claims_next_cursor: string | null
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

export interface WikiSearchSource {
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

export interface WikiSearchClaim {
  id: string
  text: string
  position: number
  claim_type: 'summary_fact' | 'topic_fact' | 'source_excerpt' | 'manual'
  score: number
  sources: WikiSearchSource[]
  also_in_pages: WikiPageSummary[]
}

export interface WikiSearchPage {
  id: string
  knowledge_base_id: string
  slug: string
  title: string
  page_type: 'summary' | 'topic' | 'index'
  score: number
  matched_fields: string[]
  matched_claims: WikiSearchClaim[]
  omitted_claim_count: number
}

export interface WikiSearchResult {
  pages: WikiSearchPage[]
  no_answer: boolean
  no_answer_reason: string | null
  degraded: boolean
  degradation_reason: string | null
  truncation_reason: string | null
  next_cursor: string | null
  collection_version: string | null
}
