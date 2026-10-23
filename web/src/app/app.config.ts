import {
  ApplicationConfig,
  provideBrowserGlobalErrorListeners,
  provideZoneChangeDetection,
  APP_INITIALIZER,
} from '@angular/core';
import { provideHttpClient } from '@angular/common/http';
import { provideRouter, withViewTransitions } from '@angular/router';
import { provideServiceWorker } from '@angular/service-worker';
import { provideApollo } from 'apollo-angular';
import { createApolloOptions } from './core/apollo.config';

import { environment } from '../environments/environment';
import { routes } from './app.routes';
import { AuthService } from './core/auth.service';

const swProviders = environment.production
  ? [
      provideServiceWorker('ngsw-worker.js', {
        enabled: true,
        registrationStrategy: 'registerWhenStable:30000',
      }),
    ]
  : [];

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideZoneChangeDetection({ eventCoalescing: true }),
    provideHttpClient(),
    provideRouter(routes, withViewTransitions()),
    provideApollo(() => createApolloOptions()),
    {
      provide: APP_INITIALIZER,
      useFactory: (auth: AuthService) => () => auth.init(),
      deps: [AuthService],
      multi: true,
    },
    ...swProviders,
  ],
};