package com.preciolocal.analytics.config;

import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import io.github.resilience4j.circuitbreaker.CircuitBreakerConfig;
import io.github.resilience4j.circuitbreaker.CircuitBreakerRegistry;
import io.github.resilience4j.ratelimiter.RateLimiter;
import io.github.resilience4j.ratelimiter.RateLimiterConfig;
import io.github.resilience4j.ratelimiter.RateLimiterRegistry;
import io.github.resilience4j.retry.Retry;
import io.github.resilience4j.retry.RetryConfig;
import io.github.resilience4j.retry.RetryRegistry;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.time.Duration;

@Configuration
public class ResilienceConfig {

    @Bean
    public CircuitBreakerRegistry circuitBreakerRegistry() {
        CircuitBreakerConfig config = CircuitBreakerConfig.custom()
                .slidingWindowSize(10)
                .minimumNumberOfCalls(5)
                .permittedNumberOfCallsInHalfOpenState(3)
                .automaticTransitionFromOpenToHalfOpenEnabled(true)
                .waitDurationInOpenState(Duration.ofSeconds(30))
                .failureRateThreshold(50)
                .build();
        return CircuitBreakerRegistry.of(config);
    }

    @Bean
    public CircuitBreaker alertEngineCircuitBreaker(CircuitBreakerRegistry registry) {
        return registry.circuitBreaker("alertEngine");
    }

    @Bean
    public RateLimiterRegistry rateLimiterRegistry() {
        RateLimiterConfig config = RateLimiterConfig.custom()
                .limitForPeriod(60)
                .limitRefreshPeriod(Duration.ofSeconds(60))
                .timeoutDuration(Duration.ofSeconds(5))
                .build();
        return RateLimiterRegistry.of(config);
    }

    @Bean
    public RateLimiter alertApiRateLimiter(RateLimiterRegistry registry) {
        return registry.rateLimiter("alertApi");
    }

    @Bean
    public RetryRegistry retryRegistry() {
        RetryConfig config = RetryConfig.custom()
                .maxAttempts(3)
                .waitDuration(Duration.ofMillis(500))
                .retryExceptions(
                        org.springframework.dao.DataAccessException.class,
                        io.r2dbc.spi.R2dbcException.class
                )
                .build();
        return RetryRegistry.of(config);
    }

    @Bean
    public Retry dbRetry(RetryRegistry registry) {
        return registry.retry("dbRetry");
    }
}