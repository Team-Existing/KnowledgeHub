import { Component, Input } from '@angular/core'
import { CommonModule } from '@angular/common'
import { ItemSupport } from '../../models/api'

export const SUPPORT_LABEL: Record<ItemSupport['status'], string> = {
  supported: 'Source says this',
  partly: 'Source says only part of this',
  unsupported: "Source doesn't say this",
  unverified: 'Not checked against the source',
}

/** Whether the source states what a model extracted (backend services/grounding.py), with the closest passage. */
@Component({
  selector: 'app-support-note',
  imports: [CommonModule],
  template: `
    @if (support) {
      <div class="support-note" [class.compact]="compact">
        <span class="status-badge" [ngClass]="'support-' + support.status">{{ label[support.status] }}</span>
        @if (support.reason) { <span class="muted-text"> {{ support.reason }}</span> }
        @if (support.passage && (!compact || support.status !== 'supported')) {
          <blockquote class="muted-text" style="margin:0.35rem 0 0;padding-left:0.6rem;border-left:3px solid var(--border)">
            {{ support.status === 'unsupported' ? 'Closest passage: ' : '' }}“{{ support.passage }}”</blockquote>
        }
      </div>
    }
  `
})
export class SupportNoteComponent {
  @Input() support: ItemSupport | null | undefined
  /** In the review queue a supported item needs no quote */
  @Input() compact = false
  readonly label = SUPPORT_LABEL
}
