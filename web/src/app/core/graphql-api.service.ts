import { Injectable, inject } from '@angular/core';
import { Apollo, gql } from 'apollo-angular';
import { Observable } from 'rxjs';
import { map } from 'rxjs/operators';
import { environment } from '../../environments/environment';

export const SEARCH_PRODUCTS = gql`
  query SearchProducts($q: String!, $chain: String, $limit: Int, $offset: Int) {
    searchProducts(q: $q, chain: $chain, limit: $limit, offset: $offset) {
      products {
        id
        name
        normalizedName
        brand
        category
        categorySlug
        sizeQuantity
        sizeUnit
        ean
        chain
        chainName
        price
        promoType
        capturedAt
      }
      total
      page
      pageSize
    }
  }
`;

export const GET_PRODUCT = gql`
  query Product($id: ID!) {
    product(id: $id) {
      id
      name
      normalizedName
      brand {
        id
        name
        slug
      }
      category {
        id
        name
        slug
      }
      sizeQuantity
      sizeUnit
      ean
      prices {
        id
        chain
        chainId
        price
        promoType
        capturedAt
        scrapedAt
        hash
      }
    }
  }
`;

export const GET_CHAINS = gql`
  query Chains {
    chains {
      id
      name
      slug
      active
    }
  }
`;

export const GET_PRICE_HISTORY = gql`
  query PriceHistory($productId: ID!, $span: String, $chain: String) {
    priceHistory(productId: $productId, span: $span, chain: $chain) {
      id
      productId
      chain
      chainId
      skuId
      storeId
      price
      promoType
      capturedAt
      scrapedAt
      hash
    }
  }
`;

export const GET_FORECAST = gql`
  query Forecast($productId: ID!, $chain: String, $horizon: Int) {
    forecast(productId: $productId, chain: $chain, horizon: $horizon) {
      productId
      chain
      requestedModel
      model
      degradedReason
      nPoints
      mape
      rmse
      mae
      points {
        date
        predictedPrice
        confLow
        confHigh
      }
    }
  }
`;

export const GET_FORECAST_COMPARISON = gql`
  query ForecastComparison($productId: ID!, $chain: String!) {
    forecastComparison(productId: $productId, chain: $chain) {
      model
      language
      mape
      rmse
      mae
      numPoints
      trainedAt
    }
  }
`;

export const GET_ANOMALIES = gql`
  query Anomalies($productId: ID!, $chain: String, $days: Int) {
    anomalies(productId: $productId, chain: $chain, days: $days) {
      productId
      chain
      nPoints
      nAnomalies
      detector
      anomalies {
        id
        productId
        chain
        pricePointId
        price
        expectedPrice
        score
        detectedAt
      }
    }
  }
`;

export const GET_COMPARE = gql`
  query Compare($productIds: [ID!]!) {
    compare(productIds: $productIds) {
      product {
        id
        name
        normalizedName
        brand {
          id
          name
          slug
        }
        category {
          id
          name
          slug
        }
        sizeQuantity
        sizeUnit
        ean
      }
      prices {
        chain {
          name
          slug
        }
        sku {
          id
          externalId
        }
        price
        promoType
        capturedAt
      }
    }
  }
`;

export const INFLATION_REPORT = gql`
  query InflationReport($span: String) {
    inflationReport(span: $span) {
      period
      span
      categories {
        category {
          id
          name
          slug
        }
        inflation
        productCount
        avgPriceChange
      }
      overallInflation
      generatedAt
    }
  }
`;

export const TOP_MOVERS = gql`
  query TopMovers($span: String, $top: Int) {
    topMovers(span: $span, top: $top) {
      period
      span
      gainers {
        product {
          id
          name
        }
        chain {
          name
          slug
        }
        priceChange
        priceChangePct
        currentPrice
        previousPrice
      }
      losers {
        product {
          id
          name
        }
        chain {
          name
          slug
        }
        priceChange
        priceChangePct
        currentPrice
        previousPrice
      }
      generatedAt
    }
  }
`;

export const CREATE_ALERT = gql`
  mutation CreateAlert($input: CreateAlertInput!) {
    createAlert(input: $input) {
      id
      userId
      product {
        id
        name
      }
      direction
      targetPrice
      active
      createdAt
    }
  }
`;

export const TOGGLE_ALERT = gql`
  mutation ToggleAlert($id: ID!, $active: Boolean!) {
    toggleAlert(id: $id, active: $active) {
      id
      active
    }
  }
`;

export const DELETE_ALERT = gql`
  mutation DeleteAlert($id: ID!) {
    deleteAlert(id: $id) {
      success
      message
    }
  }
`;

export const MY_ALERTS = gql`
  query MyAlerts {
    myAlerts {
      id
      userId
      product {
        id
        name
        normalizedName
        brand {
          name
        }
        sizeQuantity
        sizeUnit
      }
      direction
      targetPrice
      active
      lastTriggeredAt
      createdAt
    }
  }
`;

export const ALERT_EVENTS = gql`
  query AlertEvents($limit: Int) {
    alertEvents(limit: $limit) {
      id
      alertId
      pricePointId
      price
      triggeredAt
      alert {
        id
        product {
          id
          name
        }
        direction
        targetPrice
      }
    }
  }
`;

export const ON_PRICE_UPDATE = gql`
  subscription OnPriceUpdate($productId: ID!) {
    onPriceUpdate(productId: $productId) {
      id
      productId
      chain
      chainId
      price
      promoType
      capturedAt
    }
  }
`;

export const ON_ALERT_TRIGGERED = gql`
  subscription OnAlertTriggered {
    onAlertTriggered {
      id
      alertId
      pricePointId
      price
      triggeredAt
      alert {
        id
        product {
          id
          name
        }
        direction
        targetPrice
      }
    }
  }
`;

@Injectable({ providedIn: 'root' })
export class GraphqlApiService {
  private readonly apollo = inject(Apollo);

  searchProducts(q: string, chain?: string, limit = 20, offset = 0): Observable<any> {
    return this.apollo.watchQuery({
      query: SEARCH_PRODUCTS,
      variables: { q, chain, limit, offset },
      fetchPolicy: 'cache-and-network',
    }).valueChanges.pipe(map((result: any) => result.data.searchProducts));
  }

  getProduct(id: string): Observable<any> {
    return this.apollo.watchQuery({
      query: GET_PRODUCT,
      variables: { id },
      fetchPolicy: 'cache-first',
    }).valueChanges.pipe(map((result: any) => result.data.product));
  }

  getChains(): Observable<any[]> {
    return this.apollo.watchQuery({
      query: GET_CHAINS,
      fetchPolicy: 'cache-first',
    }).valueChanges.pipe(map((result: any) => result.data.chains));
  }

  getPriceHistory(productId: string, span = '30d', chain?: string): Observable<any[]> {
    return this.apollo.watchQuery({
      query: GET_PRICE_HISTORY,
      variables: { productId, span, chain },
      fetchPolicy: 'cache-and-network',
    }).valueChanges.pipe(map((result: any) => result.data.priceHistory));
  }

  getForecast(productId: string, chain?: string, horizon = 7): Observable<any> {
    return this.apollo.watchQuery({
      query: GET_FORECAST,
      variables: { productId, chain, horizon },
      fetchPolicy: 'cache-and-network',
    }).valueChanges.pipe(map((result: any) => result.data.forecast));
  }

  getForecastComparison(productId: string, chain: string): Observable<any[]> {
    return this.apollo.watchQuery({
      query: GET_FORECAST_COMPARISON,
      variables: { productId, chain },
      fetchPolicy: 'cache-and-network',
    }).valueChanges.pipe(map((result: any) => result.data.forecastComparison));
  }

  getAnomalies(productId: string, chain?: string, days = 30): Observable<any> {
    return this.apollo.watchQuery({
      query: GET_ANOMALIES,
      variables: { productId, chain, days },
      fetchPolicy: 'cache-and-network',
    }).valueChanges.pipe(map((result: any) => result.data.anomalies));
  }

  compare(productIds: string[]): Observable<any[]> {
    return this.apollo.watchQuery({
      query: GET_COMPARE,
      variables: { productIds },
      fetchPolicy: 'cache-and-network',
    }).valueChanges.pipe(map((result: any) => result.data.compare));
  }

  getInflationReport(span = '30d'): Observable<any> {
    return this.apollo.watchQuery({
      query: INFLATION_REPORT,
      variables: { span },
      fetchPolicy: 'cache-and-network',
    }).valueChanges.pipe(map((result: any) => result.data.inflationReport));
  }

  getTopMovers(span = '30d', top = 20): Observable<any> {
    return this.apollo.watchQuery({
      query: TOP_MOVERS,
      variables: { span, top },
      fetchPolicy: 'cache-and-network',
    }).valueChanges.pipe(map((result: any) => result.data.topMovers));
  }

  createAlert(input: { productId: string; direction: 'BELOW' | 'ABOVE'; targetPrice: string }): Observable<any> {
    return this.apollo.mutate({
      mutation: CREATE_ALERT,
      variables: { input },
    }).pipe(map((result: any) => result.data.createAlert));
  }

  toggleAlert(id: string, active: boolean): Observable<any> {
    return this.apollo.mutate({
      mutation: TOGGLE_ALERT,
      variables: { id, active },
    }).pipe(map((result: any) => result.data.toggleAlert));
  }

  deleteAlert(id: string): Observable<any> {
    return this.apollo.mutate({
      mutation: DELETE_ALERT,
      variables: { id },
    }).pipe(map((result: any) => result.data.deleteAlert));
  }

  getMyAlerts(): Observable<any[]> {
    return this.apollo.watchQuery({
      query: MY_ALERTS,
      fetchPolicy: 'cache-and-network',
    }).valueChanges.pipe(map((result: any) => result.data.myAlerts));
  }

  getAlertEvents(limit = 50): Observable<any[]> {
    return this.apollo.watchQuery({
      query: ALERT_EVENTS,
      variables: { limit },
      fetchPolicy: 'cache-and-network',
    }).valueChanges.pipe(map((result: any) => result.data.alertEvents));
  }

  onPriceUpdate(productId: string): Observable<any> {
    return this.apollo.subscribe({
      query: ON_PRICE_UPDATE,
      variables: { productId },
    }).pipe(map((result: any) => result.data.onPriceUpdate));
  }

  onAlertTriggered(): Observable<any> {
    return this.apollo.subscribe({
      query: ON_ALERT_TRIGGERED,
    }).pipe(map((result: any) => result.data.onAlertTriggered));
  }
}