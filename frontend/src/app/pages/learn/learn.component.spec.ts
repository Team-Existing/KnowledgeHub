import { ComponentFixture, TestBed } from '@angular/core/testing'
import { provideHttpClient } from '@angular/common/http'
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing'
import { provideRouter } from '@angular/router'
import { LearnComponent } from './learn.component'

const MAP = {
  all_systems: [{ id: 's1', name: 'Payments' }, { id: 's2', name: 'Events' }],
  systems: [{
    system: { id: 's1', name: 'Payments', kind: 'service', owner: 'Backend team', description: null },
    depends_on: ['Events'], dependents: [], people: [{ name: 'Ana', items: 3 }],
    current: [{ id: 'd1', title: 'Use PostgreSQL for payments', type: 'decision', status: 'active', date: '2026-01-01',
                why: 'transactions', alternatives: ['MongoDB'], health: 'ok' }],
    history: [{ id: 'd0', title: 'Use MySQL for payments', type: 'decision', status: 'superseded', date: '2024-01-01', outcome: null,
                replaced_by: { id: 'd1', title: 'Use PostgreSQL for payments', kind: 'supersedes' } },
              { id: 'd1', title: 'Use PostgreSQL for payments', type: 'decision', status: 'active', date: '2026-01-01', outcome: null }],
    risks: [], lessons: [], commitments: [],
  }],
}

describe('LearnComponent', () => {
  let fixture: ComponentFixture<LearnComponent>
  let http: HttpTestingController
  const text = () => (fixture.nativeElement as HTMLElement).textContent ?? ''

  beforeEach(async () => {
    localStorage.removeItem('kh_learn_systems')
    await TestBed.configureTestingModule({
      imports: [LearnComponent],
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()],
    }).compileComponents()
    fixture = TestBed.createComponent(LearnComponent)
    http = TestBed.inject(HttpTestingController)
    fixture.detectChanges()
    http.expectOne(r => r.url === '/knowledge/learning/map').flush(MAP)
    await fixture.whenStable()
    fixture.detectChanges()
  })

  afterEach(() => { http.verify(); localStorage.removeItem('kh_learn_systems') })

  it('shows what holds now, why, and how it came to be', () => {
    expect(text()).toContain('Use PostgreSQL for payments')
    expect(text()).toContain('Why: transactions')
    expect(text()).toContain('Instead of: MongoDB')
    expect(text()).toContain('supersedes by Use PostgreSQL for payments')
    expect(text()).toContain('Depends on Events')
  })

  it('remembers the chosen systems and asks for their map', async () => {
    const toggling = fixture.componentInstance.toggle('s1')
    const req = http.expectOne(r => r.url === '/knowledge/learning/map')
    expect(req.request.params.getAll('system')).toEqual(['s1'])
    req.flush(MAP)
    await toggling
    expect(JSON.parse(localStorage.getItem('kh_learn_systems') || '[]')).toEqual(['s1'])
  })

  it('suggests questions for GraphRAG, naming the systems', async () => {
    await fixture.componentInstance.show('ask')
    fixture.detectChanges()
    expect(text()).toContain('What should I understand before changing Payments?')
  })
})
