import { TestBed } from '@angular/core/testing'
import { SupportNoteComponent } from './support-note.component'

describe('SupportNoteComponent', () => {
  const render = (support: SupportNoteComponent['support'], compact = false) => {
    const fixture = TestBed.createComponent(SupportNoteComponent)
    fixture.componentInstance.support = support
    fixture.componentInstance.compact = compact
    fixture.detectChanges()
    return (fixture.nativeElement as HTMLElement).textContent ?? ''
  }

  it('says when the source does not state the claim, with the closest passage', () => {
    const text = render({ status: 'unsupported', method: 'model', passage: 'we keep the nightly export',
                          reason: 'Nobody mentions Kubernetes.', checked_at: '' })
    expect(text).toContain("Source doesn't say this")
    expect(text).toContain('Nobody mentions Kubernetes.')
    expect(text).toContain('Closest passage: “we keep the nightly export”')
  })

  it('needs no quote for a supported item in the review queue', () => {
    const text = render({ status: 'supported', method: 'text', passage: 'billing moves', reason: '', checked_at: '' }, true)
    expect(text).toContain('Source says this')
    expect(text).not.toContain('billing moves')
  })

  it('shows nothing for an unchecked item', () => {
    expect(render(null).trim()).toBe('')
  })
})
