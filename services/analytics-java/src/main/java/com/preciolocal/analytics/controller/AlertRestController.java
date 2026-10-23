package com.preciolocal.analytics.controller;

import com.preciolocal.analytics.model.Alert;
import com.preciolocal.analytics.model.AlertEvent;
import com.preciolocal.analytics.repository.r2dbc.AlertR2dbcRepository;
import com.preciolocal.analytics.service.AlertEngine;
import io.github.resilience4j.ratelimiter.annotation.RateLimiter;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.Map;
import java.util.UUID;

@RestController
@RequestMapping("/api")
public class AlertRestController {

    private final AlertR2dbcRepository alertRepository;
    private final AlertEngine alertEngine;

    public AlertRestController(AlertR2dbcRepository alertRepository, AlertEngine alertEngine) {
        this.alertRepository = alertRepository;
        this.alertEngine = alertEngine;
    }

    @GetMapping(value = "/alerts", produces = MediaType.APPLICATION_JSON_VALUE)
    @RateLimiter(name = "alertApi")
    public Flux<Alert> getAlerts(@RequestParam("userId") UUID userId) {
        return alertRepository.findByUserId(userId);
    }

    @PostMapping(value = "/alerts", produces = MediaType.APPLICATION_JSON_VALUE)
    @RateLimiter(name = "alertApi")
    public Mono<Alert> createAlert(@RequestBody Map<String, Object> body) {
        Long productId = toLong(body.get("productId"));
        String directionRaw = String.valueOf(body.get("direction"));
        BigDecimal targetPrice = new BigDecimal(String.valueOf(body.get("targetPrice")));
        UUID userId = UUID.fromString(String.valueOf(body.get("userId")));

        Alert alert = Alert.builder()
                .userId(userId)
                .productId(productId)
                .direction(Alert.Direction.valueOf(directionRaw))
                .targetPrice(targetPrice)
                .active(true)
                .createdAt(Instant.now())
                .build();
        return alertRepository.save(alert);
    }

    @PatchMapping(value = "/alerts/{id}", produces = MediaType.APPLICATION_JSON_VALUE)
    @RateLimiter(name = "alertApi")
    public Mono<Alert> toggleAlert(@PathVariable Long id, @RequestBody Map<String, Object> body) {
        Boolean active = body.get("active") == null ? null : Boolean.valueOf(String.valueOf(body.get("active")));
        return alertRepository.findById(id)
                .switchIfEmpty(Mono.error(new ResponseStatusException(HttpStatus.NOT_FOUND)))
                .flatMap(alert -> {
                    if (active != null) {
                        alert.setActive(active);
                    }
                    return alertRepository.save(alert);
                });
    }

    @DeleteMapping("/alerts/{id}")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    @RateLimiter(name = "alertApi")
    public Mono<Void> deleteAlert(@PathVariable Long id) {
        return alertRepository.deleteById(id);
    }

    @GetMapping(value = "/alert-events", produces = MediaType.APPLICATION_JSON_VALUE)
    @RateLimiter(name = "alertApi")
    public Flux<AlertEvent> getAlertEvents(@RequestParam("userId") UUID userId) {
        return alertEngine.getAlertEventsByUserId(userId);
    }

    private Long toLong(Object value) {
        if (value instanceof Number) {
            return ((Number) value).longValue();
        }
        return Long.valueOf(String.valueOf(value));
    }
}
