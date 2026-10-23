package com.preciolocal.analytics.service;

import com.preciolocal.analytics.model.Alert;
import com.preciolocal.analytics.model.AlertEvent;
import com.preciolocal.analytics.model.PricePointEvent;
import com.preciolocal.analytics.repository.r2dbc.AlertR2dbcRepository;
import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import io.github.resilience4j.ratelimiter.RateLimiter;
import io.github.resilience4j.retry.Retry;
import io.lettuce.core.pubsub.api.reactive.RedisPubSubReactiveCommands;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;
import reactor.test.StepVerifier;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.UUID;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class AlertEngineTest {

    @Mock
    private AlertR2dbcRepository alertRepository;

    @Mock
    private RedisPubSubReactiveCommands<String, String> redisPubSub;

    @Mock
    private CircuitBreaker circuitBreaker;

    @Mock
    private RateLimiter rateLimiter;

    @Mock
    private Retry retry;

    private AlertEngine alertEngine;

    private static final UUID TEST_USER_ID = UUID.randomUUID();
    private static final Long TEST_PRODUCT_ID = 123L;
    private static final Long TEST_ALERT_ID = 1L;

    @BeforeEach
    void setUp() {
        alertEngine = new AlertEngine(alertRepository, redisPubSub, circuitBreaker, rateLimiter, retry);
        // Use reflection to set private fields for testing
        setField(alertEngine, "priceEventsChannel", "pl:events:price");
        setField(alertEngine, "alertEventsChannel", "pl:events:alert");
        setField(alertEngine, "bufferSize", 100);
        setField(alertEngine, "windowDurationSeconds", 1);
    }

    @Test
    void matchesAlert_belowDirection_priceBelowTarget_returnsTrue() {
        Alert alert = Alert.builder()
                .id(TEST_ALERT_ID)
                .userId(TEST_USER_ID)
                .productId(TEST_PRODUCT_ID)
                .direction(Alert.Direction.BELOW)
                .targetPrice(new BigDecimal("100.00"))
                .active(true)
                .build();

        PricePointEvent pricePoint = PricePointEvent.builder()
                .id(1L)
                .productId(TEST_PRODUCT_ID)
                .price(new BigDecimal("90.00"))
                .capturedAt(Instant.now())
                .build();

        boolean result = invokeMatchesAlert(alertEngine, alert, pricePoint);

        StepVerifier.create(Mono.just(result))
                .expectNext(true)
                .verifyComplete();
    }

    @Test
    void matchesAlert_belowDirection_priceAboveTarget_returnsFalse() {
        Alert alert = Alert.builder()
                .id(TEST_ALERT_ID)
                .userId(TEST_USER_ID)
                .productId(TEST_PRODUCT_ID)
                .direction(Alert.Direction.BELOW)
                .targetPrice(new BigDecimal("100.00"))
                .active(true)
                .build();

        PricePointEvent pricePoint = PricePointEvent.builder()
                .id(1L)
                .productId(TEST_PRODUCT_ID)
                .price(new BigDecimal("110.00"))
                .capturedAt(Instant.now())
                .build();

        boolean result = invokeMatchesAlert(alertEngine, alert, pricePoint);

        StepVerifier.create(Mono.just(result))
                .expectNext(false)
                .verifyComplete();
    }

    @Test
    void matchesAlert_aboveDirection_priceAboveTarget_returnsTrue() {
        Alert alert = Alert.builder()
                .id(TEST_ALERT_ID)
                .userId(TEST_USER_ID)
                .productId(TEST_PRODUCT_ID)
                .direction(Alert.Direction.ABOVE)
                .targetPrice(new BigDecimal("100.00"))
                .active(true)
                .build();

        PricePointEvent pricePoint = PricePointEvent.builder()
                .id(1L)
                .productId(TEST_PRODUCT_ID)
                .price(new BigDecimal("110.00"))
                .capturedAt(Instant.now())
                .build();

        boolean result = invokeMatchesAlert(alertEngine, alert, pricePoint);

        StepVerifier.create(Mono.just(result))
                .expectNext(true)
                .verifyComplete();
    }

    @Test
    void matchesAlert_aboveDirection_priceBelowTarget_returnsFalse() {
        Alert alert = Alert.builder()
                .id(TEST_ALERT_ID)
                .userId(TEST_USER_ID)
                .productId(TEST_PRODUCT_ID)
                .direction(Alert.Direction.ABOVE)
                .targetPrice(new BigDecimal("100.00"))
                .active(true)
                .build();

        PricePointEvent pricePoint = PricePointEvent.builder()
                .id(1L)
                .productId(TEST_PRODUCT_ID)
                .price(new BigDecimal("90.00"))
                .capturedAt(Instant.now())
                .build();

        boolean result = invokeMatchesAlert(alertEngine, alert, pricePoint);

        StepVerifier.create(Mono.just(result))
                .expectNext(false)
                .verifyComplete();
    }

    @Test
    void triggerAlert_persistsAndPublishes_whenMatch() {
        Alert alert = Alert.builder()
                .id(TEST_ALERT_ID)
                .userId(TEST_USER_ID)
                .productId(TEST_PRODUCT_ID)
                .direction(Alert.Direction.BELOW)
                .targetPrice(new BigDecimal("100.00"))
                .active(true)
                .build();

        PricePointEvent pricePoint = PricePointEvent.builder()
                .id(456L)
                .productId(TEST_PRODUCT_ID)
                .price(new BigDecimal("90.00"))
                .capturedAt(Instant.now())
                .build();

        AlertEvent savedEvent = AlertEvent.builder()
                .id(789L)
                .alertId(TEST_ALERT_ID)
                .pricePointId(456L)
                .price(new BigDecimal("90.00"))
                .triggeredAt(Instant.now())
                .build();

        when(alertRepository.insertAlertEvent(TEST_ALERT_ID, 456L, new BigDecimal("90.00")))
                .thenReturn(Mono.just(savedEvent));
        when(redisPubSub.publish(anyString(), anyString()))
                .thenReturn(Mono.just(1L));

        // Test the private method via reflection
        Mono<AlertEvent> result = invokeTriggerAlert(alertEngine, alert, pricePoint);

        StepVerifier.create(result)
                .expectNext(savedEvent)
                .verifyComplete();
    }

    @Test
    void getAlertEventsByUserId_returnsFluxOfEvents() {
        Alert alert1 = Alert.builder().id(1L).userId(TEST_USER_ID).productId(100L).active(true).build();
        Alert alert2 = Alert.builder().id(2L).userId(TEST_USER_ID).productId(200L).active(true).build();

        Instant now = Instant.now();
        AlertEvent event1 = AlertEvent.builder().id(10L).alertId(1L).price(new BigDecimal("50.00")).triggeredAt(now.minusSeconds(5)).build();
        AlertEvent event2 = AlertEvent.builder().id(11L).alertId(1L).price(new BigDecimal("45.00")).triggeredAt(now.minusSeconds(10)).build();
        AlertEvent event3 = AlertEvent.builder().id(12L).alertId(2L).price(new BigDecimal("200.00")).triggeredAt(now).build();

        when(alertRepository.findActiveByUserId(TEST_USER_ID))
                .thenReturn(Flux.just(alert1, alert2));
        when(alertRepository.findByAlertId(1L))
                .thenReturn(Flux.just(event1, event2));
        when(alertRepository.findByAlertId(2L))
                .thenReturn(Flux.just(event3));

        Flux<AlertEvent> result = alertEngine.getAlertEventsByUserId(TEST_USER_ID);

        StepVerifier.create(result)
                .expectNextMatches(e -> e.getId().equals(12L))
                .expectNextMatches(e -> e.getId().equals(10L))
                .expectNextMatches(e -> e.getId().equals(11L))
                .verifyComplete();
    }

    // Helper methods to access private methods/fields via reflection
    private boolean invokeMatchesAlert(AlertEngine engine, Alert alert, PricePointEvent pricePoint) {
        try {
            java.lang.reflect.Method method = AlertEngine.class.getDeclaredMethod("matchesAlert", Alert.class, PricePointEvent.class);
            method.setAccessible(true);
            return (boolean) method.invoke(engine, alert, pricePoint);
        } catch (Exception e) {
            throw new RuntimeException(e);
        }
    }

    private Mono<AlertEvent> invokeTriggerAlert(AlertEngine engine, Alert alert, PricePointEvent pricePoint) {
        try {
            java.lang.reflect.Method method = AlertEngine.class.getDeclaredMethod("triggerAlert", Alert.class, PricePointEvent.class);
            method.setAccessible(true);
            return (Mono<AlertEvent>) method.invoke(engine, alert, pricePoint);
        } catch (Exception e) {
            throw new RuntimeException(e);
        }
    }

    private void setField(Object target, String fieldName, Object value) {
        try {
            java.lang.reflect.Field field = AlertEngine.class.getDeclaredField(fieldName);
            field.setAccessible(true);
            field.set(target, value);
        } catch (Exception e) {
            throw new RuntimeException(e);
        }
    }

    private String anyString() {
        return org.mockito.ArgumentMatchers.anyString();
    }
}