package com.preciolocal.analytics.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.preciolocal.analytics.model.Alert;
import com.preciolocal.analytics.model.AlertEvent;
import com.preciolocal.analytics.model.PricePointEvent;
import com.preciolocal.analytics.repository.r2dbc.AlertR2dbcRepository;
import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import io.github.resilience4j.ratelimiter.RateLimiter;
import io.github.resilience4j.retry.Retry;
import io.lettuce.core.pubsub.api.reactive.RedisPubSubReactiveCommands;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.stereotype.Service;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;

import java.time.Duration;
import java.util.concurrent.atomic.AtomicLong;

@Service
public class AlertEngine {

    private static final Logger log = LoggerFactory.getLogger(AlertEngine.class);
    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper().findAndRegisterModules();

    private final AlertR2dbcRepository alertRepository;
    private final RedisPubSubReactiveCommands<String, String> redisPubSub;
    private final CircuitBreaker circuitBreaker;
    private final RateLimiter rateLimiter;
    private final Retry retry;

    @Value("${app.redis.price-events-channel:pl:events:price}")
    private String priceEventsChannel;

    @Value("${app.redis.alert-events-channel:pl:events:alert}")
    private String alertEventsChannel;

    @Value("${app.alert-engine.buffer-size:1000}")
    private int bufferSize;

    @Value("${app.alert-engine.window-duration-seconds:5}")
    private int windowDurationSeconds;

    private final AtomicLong processedCount = new AtomicLong(0);
    private final AtomicLong triggeredCount = new AtomicLong(0);

    public AlertEngine(AlertR2dbcRepository alertRepository,
                       RedisPubSubReactiveCommands<String, String> redisPubSub,
                       CircuitBreaker circuitBreaker,
                       RateLimiter rateLimiter,
                       Retry retry) {
        this.alertRepository = alertRepository;
        this.redisPubSub = redisPubSub;
        this.circuitBreaker = circuitBreaker;
        this.rateLimiter = rateLimiter;
        this.retry = retry;
    }

    @EventListener(ApplicationReadyEvent.class)
    public void start() {
        log.info("Starting AlertEngine - subscribing to channel: {}", priceEventsChannel);

        redisPubSub.subscribe(priceEventsChannel)
                .thenMany(redisPubSub.observeChannels())
                .filter(message -> priceEventsChannel.equals(message.getChannel()))
                // flatMap y no map: un mensaje malformado no puede devolver
                // null (Reactor lanza NPE y corta el stream entero). Se
                // descarta sólo ese mensaje y el motor sigue escuchando.
                .flatMap(message -> {
                    try {
                        return Mono.just(OBJECT_MAPPER.readValue(message.getMessage(), PricePointEvent.class));
                    } catch (Exception e) {
                        log.warn("Failed to parse price event: {}", message.getMessage(), e);
                        return Mono.empty();
                    }
                })
                .onBackpressureBuffer(bufferSize, dropped -> {
                    log.warn("Backpressure: dropped price event, buffer full");
                })
                .window(Duration.ofSeconds(windowDurationSeconds))
                .flatMap(window -> window
                        .flatMap(this::processPricePoint)
                        .onErrorResume(e -> {
                            log.error("Error processing price point window", e);
                            return Mono.empty();
                        })
                )
                // Si se cae Redis o el stream termina con error, reconectar
                // con backoff en vez de dejar el motor muerto en silencio.
                .retryWhen(reactor.util.retry.Retry.backoff(5, Duration.ofSeconds(1))
                        .doBeforeRetry(signal -> log.warn(
                                "AlertEngine stream error; reconnecting (attempt {})",
                                signal.totalRetries() + 1)))
                .subscribe(
                        event -> log.debug("Alert event processed: {}", event),
                        error -> log.error("AlertEngine stream error", error),
                        () -> log.info("AlertEngine stream completed")
                );
    }

    private Mono<Void> processPricePoint(PricePointEvent pricePoint) {
        return alertRepository.findActiveByProductId(pricePoint.getProductId())
                .filter(alert -> matchesAlert(alert, pricePoint))
                .flatMap(alert -> triggerAlert(alert, pricePoint))
                .then()
                .doOnSuccess(unused -> processedCount.incrementAndGet());
    }

    private boolean matchesAlert(Alert alert, PricePointEvent pricePoint) {
        if (alert.getDirection() == Alert.Direction.BELOW) {
            return pricePoint.getPrice().compareTo(alert.getTargetPrice()) < 0;
        } else {
            return pricePoint.getPrice().compareTo(alert.getTargetPrice()) > 0;
        }
    }

    private Mono<AlertEvent> triggerAlert(Alert alert, PricePointEvent pricePoint) {
        return alertRepository.insertAlertEvent(alert.getId(), pricePoint.getId(), pricePoint.getPrice())
                .doOnSuccess(event -> {
                    triggeredCount.incrementAndGet();
                    publishAlertEvent(event);
                    log.info("Alert triggered: alertId={}, productId={}, price={}, direction={}",
                            alert.getId(), pricePoint.getProductId(), pricePoint.getPrice(), alert.getDirection());
                })
                .doOnError(e -> log.error("Failed to persist alert event for alert {}", alert.getId(), e))
                .onErrorResume(e -> Mono.empty());
    }

    private void publishAlertEvent(AlertEvent event) {
        try {
            String json = OBJECT_MAPPER.writeValueAsString(event);
            redisPubSub.publish(alertEventsChannel, json)
                    .subscribe(
                            unused -> log.debug("Published alert event to Redis: {}", event.getId()),
                            error -> log.error("Failed to publish alert event to Redis", error)
                    );
        } catch (Exception e) {
            log.error("Failed to serialize alert event", e);
        }
    }

    public Mono<Long> getProcessedCount() {
        return Mono.just(processedCount.get());
    }

    public Mono<Long> getTriggeredCount() {
        return Mono.just(triggeredCount.get());
    }

    public Flux<AlertEvent> getAlertEventsByUserId(java.util.UUID userId) {
        return alertRepository.findActiveByUserId(userId)
                .flatMap(alert -> alertRepository.findByAlertId(alert.getId()))
                .sort((a, b) -> b.getTriggeredAt().compareTo(a.getTriggeredAt()));
    }
}