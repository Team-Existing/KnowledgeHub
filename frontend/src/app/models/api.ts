/**
 * Response shapes of the backend API. Keep in sync with
 * backend/app/serializers.py and the routers in backend/app/routers/.
 */

export type ReviewStatus = 'pending' | 'accepted' | 'rejected'
export type ExtractionEngine = 'regex' | 'local_llm' | 'cloud_llm' | 'structured'

/** Lifecycle of decisions and risks (null for other item types); see backend services/lineage.py */
export type DecisionStatus = 'proposed' | 'active' | 'superseded' | 'reversed' | 'deprecated' | 'rejected'
export type RiskStatus = 'open' | 'mitigated' | 'materialized' | 'closed'
export type ItemStatus = DecisionStatus | RiskStatus

/** Extracted item details; see backend item_schema.normalize_item_details. */
export interface ItemDetails {
  what?: string
  why?: string
  who?: string
  due?: string
  severity?: 'low' | 'medium' | 'high'
  steps?: string[]
  evidence?: string
  confidence?: number
  extractor?: 'regex' | 'llm' | 'okf'
  [key: string]: unknown
}

export interface KnowledgeItem {
  id: string
  artifact_id: string | null
  title: string
  type: string
  author: string
  date: string
  tags: string[]
  details: ItemDetails
  extraction_engine: ExtractionEngine
  review_status: ReviewStatus
  review_note: string
  status: ItemStatus | null
  declared_status: ItemStatus | null
}

export interface Artifact {
  id: string
  title: string
  content: string
  source: string
  source_type: string
  author: string
  tags: string[]
  extraction_engine: ExtractionEngine
  created_at: string
  metadata: Record<string, unknown>
}

export interface Relationship {
  from: string
  to: string
  type: string
}

export interface Playbook {
  id: string
  title: string
  steps: Record<string, unknown>[]
  category: string
}

/** GET /knowledge */
export interface KnowledgeResponse {
  artifacts: Artifact[]
  knowledge_items: KnowledgeItem[]
  relationships: Relationship[]
  playbooks: Playbook[]
}

/** GET /knowledge/graph */
export interface GraphNode { id: string; label: string; type: string }
export interface GraphEdge { source: string; target: string; label: string }
export interface GraphResponse {
  nodes: GraphNode[]
  edges: GraphEdge[]
  layout: string
}

/** GET /knowledge/links */
export interface CrossLink {
  item_id_a: string
  item_id_b: string
  score: number
}

/** POST /knowledge/link */
export interface CrossLinkResult {
  links_created: number
  links: CrossLink[]
}

/** POST /knowledge/artifacts[/upload|/url|/transcript] */
export interface IngestResult {
  artifact: Omit<Artifact, 'extraction_engine' | 'metadata'>
  items: Array<Omit<KnowledgeItem, 'extraction_engine' | 'review_status' | 'review_note'>>
  relationships: Relationship[]
  extracted_count: number
  summary?: string              // transcript only
  llm_error?: string | null     // transcript, file and URL ingestion
}

/** GET /knowledge/search */
export interface SearchArtifact extends Artifact {
  snippet?: string              // present when the artifact matched in its content
}
export interface SearchResult {
  query: string
  filters: { type: string | null; source_type: string | null; tag: string | null }
  knowledge_items: KnowledgeItem[]
  artifacts: SearchArtifact[]
  total: number
  item_total: number
  artifact_total: number
}

/** POST /knowledge/graphrag/query */
export interface Citation {
  id: string
  title: string
  type: string
}
export interface ContextNode {
  id: string
  title?: string
  label?: string
  kind?: string
  type?: string
  score?: number
  rrf_score?: number
  retrieved_by?: string
}
export interface GraphRagResponse {
  answer: string
  citations: Citation[]
  context_nodes: ContextNode[]
  summaries: Array<{ artifact_id: string; title?: string; summary: string }>
  route: string
  sub_queries: string[]
  hyde_doc?: string
  retrieval_mode: string
  latency_ms: number
  timings_ms?: Record<string, number>
}

/** POST /knowledge/reembed */
export interface ReembedResult {
  provider: string
  dimensions: number
  items_reembedded: number
  summaries_reembedded: number
}

/** POST /auth/token */
export interface TokenResponse {
  access_token: string
  token_type: string
}

/* ── Decision lineage ─────────────────────────────────────────────────────── */

export type LinkKind = 'supersedes' | 'reverses' | 'amends' | 'realizes' | 'mitigates' | 'learned_from'

/** Edge newer -> older: `from` <kind> `to` */
export interface LineageEdge {
  from: string
  to: string
  kind: LinkKind
  note: string
  origin: string
  created_at: string
}

export interface ItemBrief {
  id: string
  title: string
  type: string
  date: string
  status: ItemStatus | null
  artifact_id: string | null
  review_status: ReviewStatus
}

export interface LineageLink {
  kind: LinkKind
  note: string
  origin: string
  created_at: string
  item: ItemBrief
}

export interface ItemEvent {
  kind: 'reviewed' | 'edited' | 'linked' | 'unlinked' | 'status_changed' | 'status_declared' | string
  at: string
  detail: Record<string, any>
}

/** GET /knowledge/items/{id}/lineage */
export interface LineageResponse {
  item: KnowledgeItem
  status: ItemStatus | null
  declared_status: ItemStatus | null
  declarable_statuses: ItemStatus[]
  links: { incoming: LineageLink[]; outgoing: LineageLink[] }
  chain: ItemBrief[]
  chain_edges: LineageEdge[]
  events: ItemEvent[]
}

/** GET /knowledge/items/{id}/lineage/suggestions */
export interface LinkSuggestion {
  kind: LinkKind
  from: string
  to: string
  score: number
  item: ItemBrief
}

/** GET /knowledge/lineage/kinds */
export interface LinkKindsResponse {
  kinds: Array<{ kind: LinkKind; from_types: string[] | null; to_types: string[] | null }>
  statuses: Record<string, ItemStatus[]>
}

/** GET /knowledge/register */
export interface RegisterResponse {
  items: KnowledgeItem[]
  edges: LineageEdge[]
}

/* ── Connectors ───────────────────────────────────────────────────────────── */

export interface ConnectorField {
  name: string
  label: string
  type: 'text' | 'password' | 'number' | 'list'
  required: boolean
  secret: boolean
  default: unknown
  help: string
}

/** GET /connectors/kinds */
export interface ConnectorKind {
  kind: string
  label: string
  filesystem: boolean
  allowed: boolean
  fields: ConnectorField[]
}

export type SyncStatus = 'never' | 'running' | 'ok' | 'partial' | 'error' | 'interrupted'

export interface SyncResult {
  fetched?: number
  created?: number
  updated?: number
  unchanged?: number
  failed?: number
  items_extracted?: number
  links_created?: number
  errors?: Array<{ external_id: string; error: string }>
}

/** GET /connectors */
export interface Connector {
  id: string
  kind: string
  label: string
  name: string
  config: Record<string, unknown>     // secrets come back as "********"
  created_at: string
  last_sync_at: string | null
  last_status: SyncStatus
  last_error: string | null
  last_result: SyncResult
}

/** GET /connectors/{id}/documents */
export interface SyncedDocument {
  external_id: string
  artifact_id: string
  content_hash: string
  title: string
  url: string
  synced_at: string
}
