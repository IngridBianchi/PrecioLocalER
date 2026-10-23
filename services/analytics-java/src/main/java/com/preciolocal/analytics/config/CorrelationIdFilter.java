package com.preciolocal.analytics.config;

import org.slf4j.MDC;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.http.server.reactive.ServerHttpRequest;
import org.springframework.web.server.ServerWebExchange;
import org.springframework.web.server.WebFilter;
import org.springframework.web.server.WebFilterChain;
import reactor.core.publisher.Mono;

import java.util.UUID;

@Order(Ordered.HIGHEST_PRECEDENCE)
public class CorrelationIdFilter implements WebFilter {

    public static final String CORRELATION_ID_HEADER = "X-Correlation-Id";
    public static final String USER_ID_HEADER = "X-User-Id";

    @Override
    public Mono<Void> filter(ServerWebExchange exchange, WebFilterChain chain) {
        ServerHttpRequest request = exchange.getRequest();
        
        // Extract or generate correlation ID
        String correlationId = request.getHeaders().getFirst(CORRELATION_ID_HEADER);
        if (correlationId == null || correlationId.isBlank()) {
            correlationId = UUID.randomUUID().toString();
        }
        
        // Extract user ID if present
        String userId = request.getHeaders().getFirst(USER_ID_HEADER);
        
        // Put in MDC for logging
        String finalCorrelationId = correlationId;
        String finalUserId = userId;
        
        return chain.filter(exchange)
                .doOnSubscribe(subscription -> {
                    MDC.put("correlation_id", finalCorrelationId);
                    if (finalUserId != null) {
                        MDC.put("user_id", finalUserId);
                    }
                })
                .doFinally(signalType -> {
                    MDC.clear();
                })
                .contextWrite(context -> context.put("correlationId", finalCorrelationId));
    }
}