import { ApolloClientOptions, InMemoryCache, split } from '@apollo/client/core';
import { HttpLink } from '@apollo/client/link/http';
import { GraphQLWsLink } from '@apollo/client/link/subscriptions';
import { getMainDefinition } from '@apollo/client/utilities';
import { createClient } from 'graphql-ws';
import { environment } from '../../environments/environment';

export function createApolloOptions(): ApolloClientOptions<unknown> {
  const graphqlHttpUrl = environment.graphqlBaseUrl
    ? `${environment.graphqlBaseUrl}/graphql`
    : '/graphql';
  const graphqlWsUrl = environment.graphqlWsUrl
    ? `${environment.graphqlWsUrl}/ws`
    : '/ws';

  const httpLink = new HttpLink({
    uri: graphqlHttpUrl,
  });

  const wsLink = new GraphQLWsLink(
    createClient({
      url: graphqlWsUrl,
      connectionParams: () => {
        const token = localStorage.getItem('supabase_token');
        return token ? { Authorization: `Bearer ${token}` } : {};
      },
      retryAttempts: 5,
      shouldRetry: () => true,
    })
  );

  const link = split(
    ({ query }) => {
      const definition = getMainDefinition(query);
      return definition.kind === 'OperationDefinition' && definition.operation === 'subscription';
    },
    wsLink,
    httpLink
  );

  return {
    link,
    cache: new InMemoryCache({
      typePolicies: {
        Query: {
          fields: {
            searchProducts: {
              keyArgs: ['q', 'chain'],
              merge(existing, incoming) {
                return incoming;
              },
            },
            priceHistory: {
              keyArgs: ['productId', 'span', 'chain'],
              merge(existing, incoming) {
                return incoming;
              },
            },
          },
        },
      },
    }),
    defaultOptions: {
      watchQuery: {
        fetchPolicy: 'cache-and-network',
      },
    },
  };
}