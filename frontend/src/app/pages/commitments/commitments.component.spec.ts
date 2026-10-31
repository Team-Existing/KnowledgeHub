import { ComponentFixture, TestBed } from '@angular/core/testing'
import { provideHttpClient } from '@angular/common/http'
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing'
import { provideRouter } from '@angular/router'
import { CommitmentsComponent } from './commitments.component'

const COMMITMENT = {
  id: 'action_1', title: 'Migrate billing tables', type: 'action-item', status: 'in_progress', owner: 'Omar',
  due_date: '2026-01-01', due_text: null, overdue: true, health: { state: 'ok', signals: [] },
  decision: { id: 'decision_1', title: 'Move billing to PostgreSQL', status: 'active' },
}

describe('CommitmentsComponent', () => {
  let fixture: ComponentFixture<CommitmentsComponent>
  let http: HttpTestingController
  const text = () => (fixture.nativeElement as HTMLElement).textContent ?? ''

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [CommitmentsComponent],
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()],
    }).compileComponents()
    fixture = TestBed.createComponent(CommitmentsComponent)
    http = TestBed.inject(HttpTestingController)
  })

  afterEach(() => http.verify())

  it('lists commitments with owner, overdue badge and the decision they carry out', async () => {
    fixture.detectChanges()
    http.expectOne(r => r.url === '/knowledge/commitments' && r.params.get('show') === 'open').flush({
      items: [COMMITMENT], total: 1, counts: { open: 1, in_progress: 1, done: 2, dropped: 1, overdue: 1 },
    })
    http.expectOne('/knowledge/outcomes').flush({
      awaiting: [], recorded: [], counts: { achieved: 0, partly: 0, not_achieved: 0, awaiting: 0, due: 0 },
    })
    await fixture.whenStable()
    fixture.detectChanges()
    expect(text()).toContain('Migrate billing tables')
    expect(text()).toContain('Overdue')
    expect(text()).toContain('carries out Move billing to PostgreSQL')
    // open counts in-progress too; finished counts done and dropped
    expect(text()).toContain('Open (2)')
    expect(text()).toContain('Finished (3)')
    expect(text()).toContain('All (5)')
    // regression: the menu showed its first option ("Open") whatever the status was
    const status = (fixture.nativeElement as HTMLElement).querySelector('select[aria-label^="Status of"]') as HTMLSelectElement
    expect(status.value).toBe('in_progress')
  })
})
