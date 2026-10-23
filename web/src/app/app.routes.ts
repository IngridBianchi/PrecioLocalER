import { Routes } from '@angular/router';
import { SearchComponent } from './features/search/search';
import { ProductDetailComponent } from './features/product-detail/product-detail';
import { DashboardComponent } from './features/dashboard/dashboard';
import { ComparatorComponent } from './features/comparator/comparator';
import { AlertsComponent } from './features/alerts/alerts';
import { PredictionsComponent } from './features/predictions/predictions';

export const routes: Routes = [
  { path: '', component: SearchComponent },
  { path: 'dashboard', component: DashboardComponent },
  { path: 'comparador', component: ComparatorComponent },
  { path: 'alertas', component: AlertsComponent },
  { path: 'predicciones/:id', component: PredictionsComponent },
  {
    path: 'producto/:id',
    loadComponent: () =>
      import('./features/product-detail/product-detail').then(
        (m) => m.ProductDetailComponent
      )
  },
  { path: '**', redirectTo: '' }
];