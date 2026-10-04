import { ApplicationConfig, provideZoneChangeDetection } from '@angular/core'
import { provideRouter } from '@angular/router'
import { provideHttpClient, withInterceptors } from '@angular/common/http'
import { routes } from './app.routes'
import { authInterceptor } from './services/auth.interceptor'

export const appConfig: ApplicationConfig = {
  providers: [
    // Angular 21 defaults to zoneless change detection; the app relies on zone.js
    provideZoneChangeDetection(),
    provideRouter(routes),
    provideHttpClient(withInterceptors([authInterceptor])),
  ]
}
