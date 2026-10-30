import { ComponentFixture, TestBed } from '@angular/core/testing'
import { provideHttpClient } from '@angular/common/http'
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing'
import { provideRouter } from '@angular/router'
import { LoginComponent } from './login.component'
import { ModelCatalog, ModelService } from '../../services/model.service'

function catalog(installed: string[], reachable = true, hosted: string[] = [], ollamaEnabled = true): ModelCatalog {
  const ids = ollamaEnabled ? ['llama3.1:8b', 'mistral:7b', 'gpt-oss:20b'] : []
  return {
    ollamaEnabled,
    ollamaReachable: ollamaEnabled && reachable,
    anyInstalled: (reachable && installed.length > 0) || hosted.length > 0,
    ramGb: 16,
    recommended: 'llama3.1:8b',
    models: [
      ...ids.map(id => ({
        id, name: id, size: '4 GB', ramRequired: '8 GB+', minRamGb: 8, description: '',
        provider: 'ollama' as const, providerName: 'Ollama', local: true, installed: installed.includes(id), active: false,
      })),
      ...hosted.map(name => ({
        id: `api/${name}`, name, size: '', ramRequired: '', minRamGb: 0, description: '',
        provider: 'api' as const, providerName: 'Test Cloud', local: false, installed: true, active: false,
      })),
    ],
  }
}

describe('LoginComponent model gate', () => {
  let fixture: ComponentFixture<LoginComponent>
  let http: HttpTestingController
  const text = () => (fixture.nativeElement as HTMLElement).textContent ?? ''

  async function load(c: ModelCatalog, setupTokenRequired = false) {
    fixture.detectChanges()                                     // ngOnInit → GET /models/catalog, /auth/setup
    http.expectOne('/models/catalog').flush(c)
    http.expectOne('/auth/setup').flush({ setup_token_required: setupTokenRequired, registration_open: true })
    await fixture.whenStable()
    fixture.detectChanges()
  }

  beforeEach(async () => {
    localStorage.removeItem('kh_token')
    await TestBed.configureTestingModule({
      imports: [LoginComponent],
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()],
    }).compileComponents()
    fixture = TestBed.createComponent(LoginComponent)
    http = TestBed.inject(HttpTestingController)
  })

  afterEach(() => http.verify())

  it('goes straight to sign-in when a model is already installed', async () => {
    await load(catalog(['mistral:7b']))
    expect(fixture.componentInstance.stage).toBe('ready')
    expect(text()).toContain('Sign in')
    expect(text()).not.toContain('Download a local AI model')
  })

  it('shows the download screen when none of the three is installed', async () => {
    await load(catalog([]))
    expect(fixture.componentInstance.stage).toBe('needs-model')
    expect(text()).toContain('Download a local AI model')
    expect((fixture.nativeElement as HTMLElement).querySelectorAll('input[type=radio]').length).toBe(3)
    expect(text()).not.toContain('Sign in to your workspace')
  })

  it('moves on to sign-in once a model is downloaded', async () => {
    await load(catalog([]))
    const models = TestBed.inject(ModelService)
    spyOn(models, 'installModel').and.resolveTo()
    await (fixture.debugElement.query(el => el.name === 'app-model-setup').componentInstance).download()
    fixture.detectChanges()
    expect(models.installModel).toHaveBeenCalledWith('llama3.1:8b', true)   // the recommended one, before login
    expect(fixture.componentInstance.stage).toBe('ready')
    expect(text()).toContain('is installed. Sign in to start.')
  })

  it('asks the first account on a new server for the setup token', async () => {
    await load(catalog(['mistral:7b']), true)
    const c = fixture.componentInstance
    expect(c.mode).toBe('register')
    expect(text()).toContain('SETUP_TOKEN')
    c.username = 'ana'; c.password = 'secret123'; c.setupToken = 'from-the-server'
    const done = c.submit()
    const register = http.expectOne('/auth/register')
    expect(register.request.body).toEqual({ username: 'ana', password: 'secret123', setup_token: 'from-the-server' })
    register.flush({ user_id: '1', role: 'admin' }, { status: 201, statusText: 'Created' })
    await new Promise(resolve => setTimeout(resolve))
    http.expectOne('/auth/token').flush({ access_token: 'x.y.z', token_type: 'bearer' })
    await new Promise(resolve => setTimeout(resolve))
    http.expectOne('/auth/me').flush({ id: '1', username: 'ana', role: 'admin', permissions: [], llm_model: null })
    await done
    localStorage.removeItem('kh_token')
  })

  it('signs in with a hosted model while Ollama is down, and says where text goes', async () => {
    await load(catalog([], false, ['small-model']))
    expect(fixture.componentInstance.stage).toBe('ready')
    expect(text()).toContain('hosted models send the text they work on to Test Cloud')
    expect(text()).not.toContain('your knowledge never leaves this server')
  })

  it('says only local models are in use when there are no hosted ones', async () => {
    await load(catalog(['mistral:7b']))
    expect(text()).toContain('your knowledge never leaves this server')
  })

  it('asks for an administrator when the server has no model at all', async () => {
    await load(catalog([], false, [], false))
    expect(fixture.componentInstance.stage).toBe('needs-model')
    expect(text()).toContain('No AI model is configured')
    expect(text()).not.toContain("Ollama isn't running")
  })

  it('explains when Ollama is not running', async () => {
    await load(catalog([], false))
    expect(text()).toContain("Ollama isn't running")
  })

  it('re-checks models when the server refuses sign-in for lack of one', async () => {
    await load(catalog(['mistral:7b']))
    const c = fixture.componentInstance
    c.username = 'ana'; c.password = 'secret123'
    const done = c.submit()
    http.expectOne('/auth/token').flush(
      { detail: 'No local model is installed. Download one of the supported models to sign in.' },
      { status: 412, statusText: 'Precondition Failed' })
    await new Promise(resolve => setTimeout(resolve))         // let the error reach the component
    http.expectOne('/models/catalog').flush(catalog([]))
    await done
    fixture.detectChanges()
    expect(c.stage).toBe('needs-model')
    expect(text()).toContain('Download a local AI model')
  })
})
