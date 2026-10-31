import { ComponentFixture, TestBed } from '@angular/core/testing'
import { provideHttpClient } from '@angular/common/http'
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing'
import { provideRouter } from '@angular/router'
import { AssistantComponent } from './assistant.component'

describe('AssistantComponent', () => {
  let fixture: ComponentFixture<AssistantComponent>
  let http: HttpTestingController
  const text = () => ((fixture.nativeElement as HTMLElement).textContent ?? '').replace(/\s+/g, ' ')

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [AssistantComponent],
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()],
    }).compileComponents()
    fixture = TestBed.createComponent(AssistantComponent)
    http = TestBed.inject(HttpTestingController)
    fixture.detectChanges()
  })

  afterEach(() => http.verify())

  it('shows the plan, the answer and the sentences no source backs', async () => {
    const asking = fixture.componentInstance.ask('Why PostgreSQL? Anything overdue?')
    const req = http.expectOne('/knowledge/assistant')
    expect(req.request.body).toEqual({ question: 'Why PostgreSQL? Anything overdue?' })
    req.flush({
      question: 'Why PostgreSQL? Anything overdue?',
      plan: [{ agent: 'watchtower', label: 'Watchtower', why: 'it asks what needs attention or follow-up' },
             { agent: 'research', label: 'Research & answer', why: 'an answer from the record, with citations' },
             { agent: 'verification', label: 'Verification', why: 'check the answer against the sources it drew on' }],
      steps: [
        { agent: 'watchtower', label: 'Watchtower', why: '', ok: true, ms: 5, result: {
          attention: [], attention_total: 0,
          overdue_commitments: [{ id: 'c1', title: 'Migrate tables', owner: 'Omar', due_date: '2026-10-01' }],
          overdue_total: 1, outcomes_due: [], outcomes_due_total: 0 } },
        { agent: 'research', label: 'Research & answer', why: '', ok: true, ms: 900, result: {
          answer: 'Billing uses PostgreSQL for transactions.', citations: [{ id: 'd1', title: 'Use PostgreSQL' }] } },
        { agent: 'verification', label: 'Verification', why: '', ok: true, ms: 2, result: {
          sentences: [{ text: 'Billing uses PostgreSQL for transactions.', backed: true },
                      { text: 'It also runs on Kubernetes.', backed: false }], backed: 1, unbacked: 1 } },
      ],
    })
    await asking
    fixture.detectChanges()
    expect(text()).toContain('it asks what needs attention or follow-up')
    expect(text()).toContain('Overdue: Migrate tables')
    expect(text()).toContain('Billing uses PostgreSQL for transactions.')
    expect(text()).toContain('1 of 2 sentences backed by a source.')
    expect(text()).toContain('no source says this It also runs on Kubernetes.')
  })

  it('reports a failed step', async () => {
    const asking = fixture.componentInstance.ask('Should we migrate billing?')
    http.expectOne('/knowledge/assistant').flush({ question: 'q', plan: [], steps: [
      { agent: 'impact', label: 'Impact', why: '', ok: false, ms: 1, error: 'impact is down' }] })
    await asking
    fixture.detectChanges()
    expect(text()).toContain('This step failed: impact is down')
  })
})
