import { ComponentFixture, TestBed } from '@angular/core/testing'
import { provideHttpClient } from '@angular/common/http'
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing'
import { ActivatedRoute, convertToParamMap, provideRouter } from '@angular/router'
import { ImpactComponent } from './impact.component'

const REPORT = {
  proposal: 'Move the payment service to MongoDB', subject: null, llm_used: true,
  systems: [
    { id: 's1', name: 'Payments', kind: 'service', owner: null, depth: 0, reason: 'named in the proposal', via: [] },
    { id: 's2', name: 'Reporting', kind: 'service', owner: null, depth: 1, reason: 'depends on Payments', via: ['Payments'] },
  ],
  decisions: [
    { id: 'd1', title: 'Use PostgreSQL for payments', type: 'decision', status: 'active', date: null, reasons: ['mentions Payments'],
      why: 'transactional consistency', alternatives: ['MongoDB'], owner: 'Ana', outcome: null, open_commitments: 1,
      replaced_by: null, relation: 'conflicts', relation_reason: 'MongoDB drops the consistency payments need' },
    { id: 'd0', title: 'Use MySQL for payments', type: 'decision', status: 'superseded', date: null, reasons: ['mentions Payments'],
      why: null, alternatives: null, owner: null, outcome: null, open_commitments: 0,
      replaced_by: { id: 'd1', title: 'Use PostgreSQL for payments' }, relation: null, relation_reason: null },
  ],
  risks: [], lessons: [], commitments: [],
  counts: { conflicts: 1, changes: 0, systems: 2, decisions: 2, risks: 0, lessons: 0, commitments: 0 },
}

describe('ImpactComponent', () => {
  let fixture: ComponentFixture<ImpactComponent>
  let http: HttpTestingController
  const text = () => (fixture.nativeElement as HTMLElement).textContent ?? ''

  async function create(query: Record<string, string>) {
    await TestBed.configureTestingModule({
      imports: [ImpactComponent],
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting(),
                  { provide: ActivatedRoute, useValue: { snapshot: { queryParamMap: convertToParamMap(query) } } }],
    }).compileComponents()
    fixture = TestBed.createComponent(ImpactComponent)
    http = TestBed.inject(HttpTestingController)
  }

  afterEach(() => http.verify())

  it('starts from a decision and shows conflicts, reasoning, history and reach', async () => {
    await create({ decision: 'd9' })
    fixture.detectChanges()
    http.expectOne('/knowledge/systems').flush({ systems: [], kinds: [] })
    await new Promise(resolve => setTimeout(resolve))
    const request = http.expectOne('/knowledge/impact')
    expect(request.request.body).toEqual({ proposal: '', system_ids: [], decision_id: 'd9' })
    request.flush(REPORT)
    await fixture.whenStable()
    fixture.detectChanges()
    expect(text()).toContain('Potential conflict with 1 current decision')
    expect(text()).toContain('Conflicts')
    expect(text()).toContain('Why: transactional consistency')
    expect(text()).toContain('Alternatives considered: MongoDB')
    expect(text()).toContain('replaced by Use PostgreSQL for payments')
    expect(text()).toContain('depends on Payments')
  })

  it('waits for a description when there is nothing to start from', async () => {
    await create({})
    fixture.detectChanges()
    http.expectOne('/knowledge/systems').flush({ systems: [], kinds: [] })
    await fixture.whenStable()
    expect(fixture.componentInstance.canRun).toBeFalse()
    fixture.componentInstance.proposal = 'Move payments'
    expect(fixture.componentInstance.canRun).toBeTrue()
  })
})
