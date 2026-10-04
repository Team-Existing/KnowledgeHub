import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';
import { AppComponent } from './app.component';

describe('AppComponent', () => {
  function setup(token: string | null) {
    if (token) localStorage.setItem('kh_token', token); else localStorage.removeItem('kh_token');
    TestBed.configureTestingModule({
      imports: [AppComponent],
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()],
    });
    const fixture = TestBed.createComponent(AppComponent);
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  afterEach(() => localStorage.removeItem('kh_token'));

  it('should create the app', () => {
    expect(TestBed.configureTestingModule({
      imports: [AppComponent],
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()],
    }).createComponent(AppComponent).componentInstance).toBeTruthy();
  });

  it('shows no sidebar while signed out', () => {
    const el = setup(null);
    expect(el.querySelector('.sidebar')).toBeNull();
  });

  it('shows the branded sidebar once signed in', () => {
    const el = setup('test-token');
    expect(el.querySelector('.sidebar .brand-name')?.textContent).toContain('Knowledge Hubs');
    expect(el.querySelectorAll('.sidebar .nav-item').length).toBeGreaterThan(0);
  });
});
