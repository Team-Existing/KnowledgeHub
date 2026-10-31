/**
 * Response shapes of the backend API. Keep in sync with
 * backend/app/serializers.py and the routers in backend/app/routers/.
 */

export type ReviewStatus = 'pending' | 'accepted' | 'rejected'
export type ExtractionEngine = 'regex' | 'local_llm' | 'cloud_llm' | 'structured'

/** Lifecycle of decisions, risks and commitments (null for other item types); see backend services/lineage.py */
export type DecisionStatus = 'proposed' | 'active' | 'superseded' | 'reversed' | 'deprecated' | 'invalidated' | 'rejected'
export type RiskStatus = 'open' | 'mitigated' | 'materialized' | 'closed'
export type CommitmentStatus = 'open' | 'in_progress' | 'done' | 'dropped'
export type ItemStatus = DecisionStatus | RiskStatus | CommitmentStatus

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
  health: ItemHealth
  /** status and health in one word; see backend services/health.py lifecycle() */
  lifecycle: Lifecycle
  /** action items: see backend services/tracking.py */
  commitment?: CommitmentInfo
  /** decisions */
  outcome?: OutcomeInfo
  /** model-extracted items: whether the source says this; see backend services/grounding.py */
  support?: ItemSupport | null
}

/** POST /knowledge/assistant: see backend services/orchestrator.py */
export type AssistantAgent = 'research' | 'impact' | 'learning' | 'watchtower' | 'verification'
export interface AssistantStep {
  agent: AssistantAgent
  label: string
  why: string
  ok: boolean
  ms: number
  error?: string
  /** its shape depends on the agent; the page reads the fields it shows */
  result?: any
}
export interface AssistantRun {
  question: string
  plan: { agent: AssistantAgent; label: string; why: string }[]
  steps: AssistantStep[]
}

export interface ItemSupport {
  status: 'supported' | 'partly' | 'unsupported' | 'unverified'
  /** text: the source has every word of it; model: a model compared it with the closest passages */
  method: 'text' | 'model' | 'none'
  passage: string
  reason: string
  checked_at: string
}

/** POST /knowledge/items/{id}/support/check */
export interface SupportCheck {
  support: ItemSupport & { id: string }
  item: KnowledgeItem
}

export type Lifecycle = 'active' | 'needs_review' | 'potentially_stale' | 'superseded' | 'invalidated'

export interface CommitmentInfo {
  owner: string | null
  due_date: string | null
  /** a due date in words ("end of sprint") when it isn't a date */
  due_text: string | null
  overdue: boolean
}

export type Verdict = 'achieved' | 'partly' | 'not_achieved'

export interface OutcomeInfo {
  expected: string | null
  check_by: string | null
  /** check_by has come and there's no verdict yet */
  due: boolean
  verdict: Verdict | null
  note: string | null
  recorded_at: string | null
  recorded_by: string | null
}

/** GET /knowledge/commitments */
export interface CommitmentEntry extends KnowledgeItem, CommitmentInfo {
  status: CommitmentStatus
  decision: { id: string; title: string; status: string | null } | null
}
export interface CommitmentsResponse {
  items: CommitmentEntry[]
  total: number
  counts: Record<CommitmentStatus | 'overdue', number>
}

/** A service, database, team …; see backend repositories/systems.py */
export type SystemKind = 'service' | 'database' | 'library' | 'infrastructure' | 'team' | 'product' | 'other'
export interface SystemInfo {
  id: string
  name: string
  kind: SystemKind
  description: string | null
  aliases: string[]
  owner: string | null
  /** systems this one depends on, and those that depend on it */
  depends_on: string[]
  dependents: string[]
  item_count: number
}
/** GET /knowledge/systems */
export interface SystemsResponse {
  systems: SystemInfo[]
  kinds: SystemKind[]
}
export interface SystemBrief { id: string; name: string; kind: SystemKind }
/** GET /knowledge/systems/suggestions: "from depends on to", as some knowledge says */
export interface DependencySuggestion {
  from: string
  from_name: string
  to: string
  to_name: string
  evidence: { id: string; title: string }[]
}
/** GET /knowledge/items/{id}/systems */
export interface ItemSystems {
  linked: SystemBrief[]
  /** systems the item names but isn't linked to */
  suggested: SystemBrief[]
}

/** POST /knowledge/impact; see backend services/impact.py */
export type ImpactRelation = 'conflicts' | 'changes' | 'consistent' | 'unrelated'
export interface ImpactItem {
  id: string
  title: string
  type: string
  status: string | null
  date: string | null
  /** why it's in the report: "affects Payments", "similar (0.62)" … */
  reasons: string[]
}
export interface ImpactDecision extends ImpactItem {
  why: string | null
  alternatives: string | string[] | null
  owner: string | null
  outcome: OutcomeInfo | null
  open_commitments: number
  replaced_by: ImpactItem | null
  relation: ImpactRelation | null
  relation_reason: string | null
}
export interface ImpactReport {
  proposal: string
  subject: ImpactItem | null
  systems: (SystemBrief & { owner: string | null; depth: number; reason: string; via: string[] })[]
  decisions: ImpactDecision[]
  risks: (ImpactItem & { severity?: string })[]
  lessons: ImpactItem[]
  commitments: (ImpactItem & { commitment?: CommitmentInfo })[]
  /** whether the model labelled how the proposal relates to each current decision */
  llm_used: boolean
  counts: { conflicts: number; changes: number; systems: number; decisions: number; risks: number; lessons: number; commitments: number }
}

/** GET /knowledge/outcomes */
export interface OutcomeEntry extends KnowledgeItem {
  outcome: OutcomeInfo
  commitments: { total: number; done: number; overdue: number }
  lessons: { id: string; title: string; status: string | null }[]
  mitigates: { id: string; title: string; status: string | null }[]
}
export interface OutcomesResponse {
  awaiting: OutcomeEntry[]
  recorded: OutcomeEntry[]
  counts: Record<Verdict | 'awaiting' | 'due', number>
}

/** Whether a current item may be out of date; see backend services/health.py */
export type HealthState = 'ok' | 'needs_review' | 'potentially_stale'
export type HealthSignalKind = 'source_changed' | 'removed_from_source' | 'dependency_changed' | 'flagged'
  | 'outcome_not_achieved' | 'contradicted' | 'possible_duplicate' | 'unsupported'

/** POST /knowledge/items/{id}/health/check */
export interface ConsistencyCheck {
  checked: number
  llm_used: boolean
  duplicates: { id: string; title: string; score: number }[]
  contradictions: { id: string; title: string; by: string; reason: string }[]
  item: KnowledgeItem
}
export interface HealthSignal {
  kind: HealthSignalKind
  at: string
  note?: string               // flagged: the reason given
  actor?: string | null       // flagged: who
  reason?: string             // dependency_changed: what happened, in words
  item_id?: string            // dependency_changed: the item that changed
  item_title?: string
  source_id?: string          // source_changed / removed_from_source
  source_title?: string
}
export interface ItemHealth {
  state: HealthState
  signals: HealthSignal[]
  review_due: string | null   // YYYY-MM-DD
  overdue: boolean
  verified_at: string | null
  verified_by: string | null
}

/** GET /knowledge/health */
export interface KnowledgeHealthResponse {
  items: KnowledgeItem[]
  total: number
  counts: { potentially_stale: number; needs_review: number; overdue: number }
}

export interface Artifact {
  id: string
  title: string
  /** the document text: left out of lists (GET /knowledge, search) */
  content?: string
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

export interface PlaybookStep {
  text: string
  item_id?: string          // optional link to a knowledge item (e.g. a how-to)
}

/** GET /knowledge/playbooks, POST /knowledge/playbooks */
export interface Playbook {
  id: string
  title: string
  steps: PlaybookStep[]
  category: string
}

/** GET /health (public) */
export interface HealthResponse {
  status: 'healthy' | 'degraded'
  storage: string
  database: string
  arcadedb: 'connected' | 'unreachable'
}

/** GET /knowledge */
export interface KnowledgeResponse {
  /** a page: the newest `limit` after `offset` (see totals) */
  artifacts: Artifact[]
  knowledge_items: KnowledgeItem[]
  /** between the returned nodes only */
  relationships: Relationship[]
  playbooks: Playbook[]
  totals?: KnowledgeTotals
  item_types?: string[]
}
export interface KnowledgeTotals {
  artifacts: number
  knowledge_items: number
  /** items matching the q / type filters */
  matching_items: number
  pending_review: number
}

/** GET /knowledge/items */
export interface ItemsResponse { items: KnowledgeItem[]; total: number }

/** GET /knowledge/items/{id}/context */
export interface ItemContext {
  item: KnowledgeItem
  artifact: Artifact | null
  relationships: Relationship[]
  nodes: Record<string, { title: string; kind: 'item' | 'artifact'; type: string }>
}

/** GET /knowledge/graph */
export interface GraphNode { id: string; label: string; type: string }
export interface GraphEdge { source: string; target: string; label: string }
export interface GraphResponse {
  nodes: GraphNode[]
  edges: GraphEdge[]
  layout: string
  /** the graph holds the newest nodes; total_nodes counts them all */
  total_nodes?: number
  truncated?: boolean
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
  /** an item extracted from it, whose page shows the source; absent if it has none */
  first_item_id?: string
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
  status?: ItemStatus       // e.g. "superseded" when the cited decision was replaced
  health?: HealthState      // "potentially_stale" when the cited item may be out of date
  /** matched to the answer's wording because the model wrote no [id] for it */
  inferred?: boolean
}
export interface ContextNode {
  id: string
  status?: ItemStatus | null
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

/** GET /auth/me — permissions mirror backend/app/rbac.py */
export type Role = 'admin' | 'member'
export type Permission = 'manage_users' | 'manage_models' | 'use_filesystem_connectors'
export interface Me {
  id: string
  username: string
  role: Role | string
  permissions: Permission[]
  llm_model: string | null
  /** verified by a single sign-on provider; null for password-only accounts */
  email?: string | null
}

/** GET /auth/sso/providers */
export interface SsoProvider {
  id: string
  name: string
}

/** GET /auth/sso/identities */
export interface SsoAccount {
  email: string | null
  has_password: boolean
  identities: { provider: string; name: string; email: string | null; created_at: string; last_used_at: string }[]
}

/** GET /organizations/{id}/domains */
export interface OrgDomain {
  domain: string
  added_by: string
  added_at: string
}

/** GET /admin/users */
export interface AdminUser {
  id: string
  username: string
  role: Role | string
  created_at: string
  last_login_at: string | null
  last_active_at: string | null
  deletes_on: string | null       // when the account is deleted if it stays inactive
}

/** GET /admin/retention — inactivity retention (backend/app/services/retention.py) */
export interface RetentionUser { id: string; username: string; role: string; last_active_at: string; deletes_on: string | null }
export interface RetentionGroup { id: string; name: string; members: number; last_active_at: string; deletes_on: string | null }
export interface RetentionReport {
  ran_at: string
  inactive_days: number
  users_deleted: string[]
  groups_deleted: string[]
  skipped: Array<{ user?: string; group?: string; reason: string }>
}
export interface RetentionStatus {
  enabled: boolean
  inactive_days: number
  check_hours: number
  last_run: RetentionReport | null
  due: { users: RetentionUser[]; groups: RetentionGroup[] }
  upcoming: { users: RetentionUser[]; groups: RetentionGroup[] }   // due within 30 days
}

/** GET /spaces — personal first, then the groups you're an active member of */
export interface SpaceInfo {
  id: string
  kind: 'personal' | 'organization' | 'group'
  name: string
  role: 'owner' | 'admin' | 'member' | string
  member_count?: number
  /** groups: the organization they belong to */
  org_id?: string
  org_name?: string
}

export type OrgRole = 'owner' | 'admin' | 'member'

/** GET /organizations */
export interface Organization {
  id: string
  name: string
  role: OrgRole
  member_count: number
  created_at: string
}

/** GET /organizations/{id}/members */
export interface OrgMember {
  user_id: string
  username: string
  role: OrgRole
  joined_at: string
}

/** GET /organizations/{id}/invites (the token is only in the response that creates a link) */
export interface OrgInvite {
  id: string
  role: 'admin' | 'member'
  created_by: string
  created_at: string
  expires_at: string
  max_uses: number | null
  uses: number
  usable: boolean
}

/** POST /organizations/{id}/invites */
export interface CreatedInvite {
  id: string
  token: string
  role: 'admin' | 'member'
  created_at: string
  expires_at: string
  max_uses: number | null
  uses: number
}

/** GET /organizations/join/{token} */
export interface InvitePreview {
  org_id: string
  org_name: string
  role: 'admin' | 'member'
  member_count: number
  already_member: boolean
}

/** GET /groups */
export interface Group {
  id: string
  name: string
  org_id: string | null
  role: 'admin' | 'member' | string
  status: string
  member_count: number
  created_at: string
  last_active_at: string
  deletes_on: string | null       // deleted with everything in it if nobody uses it until then
}

/** GET /groups/{id}/members (admins also see invited / declined) */
export interface GroupMember {
  user_id: string
  username: string
  role: 'admin' | 'member' | string
  status: 'active' | 'invited' | 'declined' | string
  invited_by: string | null
  since: string
}

/** GET /groups/{id}/candidates */
export interface Candidate {
  id: string
  username: string
  status: string | null      // null = never invited
}

/** GET /invitations */
export interface Invitation {
  group_id: string
  group_name: string
  invited_by: string | null
  invited_at: string
  member_count: number
}

/** POST /auth/token */
export interface TokenResponse {
  access_token: string
  token_type: string
}

/** GET /auth/setup */
export interface SetupStatus {
  /** no admin yet, and the server sets SETUP_TOKEN: the first account must supply it */
  setup_token_required: boolean
  registration_open: boolean
}

/* ── Decision lineage ─────────────────────────────────────────────────────── */

export type LinkKind = 'supersedes' | 'reverses' | 'amends' | 'realizes' | 'mitigates' | 'learned_from' | 'implements'

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
  actor?: string | null     // who did it; empty for automatic changes (e.g. a derived status)
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
  /** newest first, at most `limit`; total counts them all */
  items: KnowledgeItem[]
  edges: LineageEdge[]
  total?: number
  /** items the edges name that are outside this page */
  related?: KnowledgeItem[]
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
  disabled_reason: string | null
  needs_credentials_key: boolean      // the server has no CREDENTIALS_KEY, so secrets can't be stored
  fields: ConnectorField[]
}

export type SyncStatus = 'never' | 'queued' | 'running' | 'ok' | 'partial' | 'error' | 'interrupted'

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
  created_by?: string | null          // username of whoever added it (useful in a group)
  last_sync_at: string | null
  last_status: SyncStatus
  last_error: string | null
  last_result: SyncResult
  /** GitHub, Jira and Linear connectors: whether the Decision Copilot is on */
  copilot?: { enabled: boolean; has_secret: boolean }
}

/** GET/PUT /connectors/{id}/copilot; a created secret only in the response that creates it (GitHub, Jira) */
export interface CopilotSettings {
  enabled: boolean
  has_secret: boolean
  webhook_url: string
  webhook_secret?: string
}

/** POST /knowledge/copilot/preview */
export interface CopilotPreview {
  /** ref: 42, ARCH-12, ENG-42; skip: why a webhook would leave it alone (a draft, done …) */
  target: { ref: string; title: string; url: string; noun: string; skip: string | null }
  files: number
  report: ImpactReport
  /** whether the record has something to say: a webhook run would comment */
  relevant: boolean
  /** the comment, in the platform's markup (Markdown; Jira wiki markup) */
  comment: string
}

/** GET /knowledge/slack: a Slack app's /decisions command (its signing secret never comes back) */
export interface SlackIntegration {
  id: string
  name: string
  request_url: string
  command: string
  created_at: string
  created_by_name: string | null
  last_used_at: string | null
}

/** GET /knowledge/digests: weekly digests of what needs attention (the webhook URL never comes back) */
export interface DigestInfo {
  id: string
  name: string
  /** 0 = Monday */
  weekday: number
  created_at: string
  created_by_name: string | null
  last_sent_at: string | null
  last_status: string | null
}
export interface DigestList { digests: DigestInfo[]; hour_utc: number; weekdays: string[] }
/** GET /knowledge/digests/preview */
export interface DigestPreview {
  sections: { title: string; count: number; entries: { id: string; title: string; note: string }[] }[]
  total: number
}

/** GET /knowledge/api-keys: read-only keys for developer portals (the key itself only on creation) */
export interface ApiKeyInfo {
  id: string
  name: string
  hint: string
  created_by_name: string | null
  created_at: string
  last_used_at: string | null
}
export interface CreatedApiKey extends ApiKeyInfo { key: string }

/** Institutional learning (backend services/learning.py) */
export interface LearnItem { id: string; title: string; type: string; status: string | null; date: string | null; why?: string | null; who?: string | null }
export interface SystemMap {
  system: { id: string; name: string; kind: string; owner: string | null; description: string | null }
  depends_on: string[]
  dependents: string[]
  current: (LearnItem & { health: string; alternatives: string[] | null })[]
  history: (LearnItem & { outcome: OutcomeInfo | null; replaced_by?: { id: string; title: string; kind: string } })[]
  risks: (LearnItem & { severity?: string })[]
  lessons: LearnItem[]
  commitments: (LearnItem & { commitment: CommitmentInfo })[]
  people: { name: string; items: number }[]
}
/** GET /knowledge/learning/map */
export interface KnowledgeMap { systems: SystemMap[]; all_systems: { id: string; name: string }[] }
/** GET /knowledge/learning/abandoned */
export interface Abandoned {
  decisions: (LearnItem & { how: string; note?: string | null; replaced_by: { id: string; title: string; why: string | null } | null })[]
  rejected_alternatives: { option: string; instead: { id: string; title: string }; why: string | null }[]
  dropped_commitments: LearnItem[]
}
/** GET /knowledge/learning/mistakes */
export interface Mistakes { groups: { title: string; count: number; systems: string[]; items: LearnItem[]; latest: string }[] }
/** GET /knowledge/learning/controversial */
export interface Controversial { decisions: (LearnItem & { reasons: string[]; score: number })[] }

/** GET /knowledge/copilot/runs */
export interface CopilotRun {
  id: string
  connector_id: string
  kind: 'github' | 'jira' | 'linear'
  ref: string
  title: string | null
  url: string | null
  source: 'manual' | 'webhook'
  status: 'posted' | 'updated' | 'skipped' | 'failed'
  reason: string | null
  comment_url: string | null
  counts: Partial<ImpactReport['counts']>
  created_at: string
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
