import { HealthSignal, HealthState } from '../models/api'

export const HEALTH_LABEL: Record<HealthState, string> = {
  ok: 'Up to date',
  needs_review: 'Needs review',
  potentially_stale: 'May be out of date',
}

/** One signal in words: why the item may no longer hold. */
export function describeSignal(s: HealthSignal): string {
  switch (s.kind) {
    case 'source_changed': return `Its source “${s.source_title ?? 'the source'}” changed after it was accepted`
    case 'removed_from_source': return `Its source “${s.source_title ?? 'the source'}” changed and no longer contains it`
    case 'dependency_changed': return `${s.reason ?? 'An item it builds on changed'}${s.item_title ? `: “${s.item_title}”` : ''}`
    case 'flagged': return `Flagged${s.actor ? ` by ${s.actor}` : ''}: “${s.note ?? ''}”`
    case 'contradicted': return `Newer knowledge contradicts it${s.item_title ? `: “${s.item_title}”` : ''}${s.reason ? ` (${s.reason})` : ''}`
    case 'possible_duplicate': return `It may duplicate “${s.item_title ?? 'another item'}”`
    case 'outcome_not_achieved': return `Its outcome was recorded as not achieved${s.note ? `: “${s.note}”` : ''}`
    default: return s.kind
  }
}
