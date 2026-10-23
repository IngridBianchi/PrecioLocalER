package com.preciolocal.analytics.controller;

import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import io.github.resilience4j.circuitbreaker.CircuitBreakerRegistry;
import io.github.resilience4j.ratelimiter.RateLimiter;
import io.github.resilience4j.ratelimiter.RateLimiterRegistry;
import io.r2dbc.spi.ConnectionFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.data.mongodb.core.ReactiveMongoTemplate;
import org.springframework.data.r2dbc.core.R2dbcEntityTemplate;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import reactor.core.publisher.Mono;

import java.time.Instant;
import java.util.HashMap;
import java.util.Map;

@RestController
@RequestMapping("/")
public class HealthController {

    private final ConnectionFactory connectionFactory;
    private final ReactiveMongoTemplate mongoTemplate;
    private final CircuitBreakerRegistry circuitBreakerRegistry;
    private final RateLimiterRegistry rateLimiterRegistry;
    private final R2dbcEntityTemplate r2dbcEntityTemplate;

    @Autowired(required = false)
    public HealthController(ConnectionFactory connectionFactory,
                            ReactiveMongoTemplate mongoTemplate,
                            CircuitBreakerRegistry circuitBreakerRegistry,
                            RateLimiterRegistry rateLimiterRegistry,
                            R2dbcEntityTemplate r2dbcEntityTemplate) {
        this.connectionFactory = connectionFactory;
        this.mongoTemplate = mongoTemplate;
        this.circuitBreakerRegistry = circuitBreakerRegistry;
        this.rateLimiterRegistry = rateLimiterRegistry;
        this.r2dbcEntityTemplate = r2dbcEntityTemplate;
    }

    @GetMapping("health")
    public Mono<ResponseEntity<Map<String, Object>>> health() {
        Map<String, Object> response = new HashMap<>();
        response.put("status", "UP");
        response.put("service", "analytics-java");
        response.put("timestamp", Instant.now().toString());
        return Mono.just(ResponseEntity.ok(response));
    }

    @GetMapping("ready")
    public Mono<ResponseEntity<Map<String, Object>>> ready() {
        Map<String, Object> checks = new HashMap<>();
        Map<String, Object> response = new HashMap<>();

        // Check PostgreSQL (R2DBC)
        Mono<Boolean> pgCheck = checkPostgres();

        // Check MongoDB
        Mono<Boolean> mongoCheck = checkMongo();

        return Mono.zip(pgCheck, mongoCheck)
                .map(tuple -> {
                    checks.put("postgres", tuple.getT1());
                    checks.put("mongodb", tuple.getT2());

                    boolean allReady = tuple.getT1() && tuple.getT2();
                    response.put("status", allReady ? "READY" : "NOT_READY");
                    response.put("checks", checks);
                    response.put("timestamp", Instant.now().toString());

                    return ResponseEntity.status(allReady ? HttpStatus.OK : HttpStatus.SERVICE_UNAVAILABLE)
                            .body(response);
                });
    }

    private Mono<Boolean> checkPostgres() {
        if (connectionFactory == null) {
            return Mono.just(false);
        }
        return Mono.from(connectionFactory.create())
                .flatMap(conn -> Mono.from(conn.createStatement("SELECT 1").execute()))
                .flatMap(result -> Mono.from(result.map((row, metadata) -> true)))
                .onErrorResume(e -> Mono.just(false));
    }

    private Mono<Boolean> checkMongo() {
        if (mongoTemplate == null) {
            return Mono.just(false);
        }
        return mongoTemplate.executeCommand(new org.bson.Document("ping", 1))
                .map(doc -> {
                    Object ok = doc.get("ok");
                    return ok instanceof Number && ((Number) ok).intValue() == 1;
                })
                .onErrorResume(e -> Mono.just(false));
    }
}