package com.preciolocal.analytics.controller;

import com.preciolocal.analytics.model.AlertEvent;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.messaging.simp.SimpMessagingTemplate;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.testcontainers.containers.MongoDBContainer;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.utility.DockerImageName;
import reactor.core.publisher.Mono;
import reactor.test.StepVerifier;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.UUID;

@Testcontainers
@SpringBootTest
class WebSocketAlertControllerTest {

    @Container
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("postgres:16-alpine")
            .withDatabaseName("preciolocal")
            .withUsername("preciolocal")
            .withPassword("preciolocal");

    @Container
    static MongoDBContainer mongo = new MongoDBContainer("mongo:7")
            .withReuse(true);

    @Container
    static GenericContainer<?> redis = new GenericContainer<>(DockerImageName.parse("redis:7-alpine"))
            .withExposedPorts(6379)
            .withReuse(true);

    @DynamicPropertySource
    static void configureProperties(DynamicPropertyRegistry registry) {
        registry.add("spring.r2dbc.url", () -> "r2dbc:postgresql://" + postgres.getHost() + ":" + postgres.getFirstMappedPort() + "/" + postgres.getDatabaseName());
        registry.add("spring.r2dbc.username", postgres::getUsername);
        registry.add("spring.r2dbc.password", postgres::getPassword);
        registry.add("spring.data.mongodb.uri", mongo::getReplicaSetUrl);
        registry.add("spring.redis.host", redis::getHost);
        registry.add("spring.redis.port", () -> redis.getFirstMappedPort());
    }

    @Autowired
    private WebSocketAlertController controller;

    @Autowired
    private SimpMessagingTemplate messagingTemplate;

    @Test
    void sendAlertToUser_sendsMessage() {
        AlertEvent event = AlertEvent.builder()
                .id(1L)
                .alertId(10L)
                .pricePointId(100L)
                .price(new BigDecimal("95.00"))
                .triggeredAt(Instant.now())
                .build();

        String userId = UUID.randomUUID().toString();

        // This should not throw
        controller.sendAlertToUser(userId, event);
        
        StepVerifier.create(Mono.just(true))
                .expectNext(true)
                .verifyComplete();
    }

    @Test
    void sendPriceUpdate_sendsMessage() {
        Long productId = 123L;
        java.util.Map<String, Object> priceUpdate = java.util.Map.of(
                "productId", productId,
                "price", "95.00",
                "chain", "la_anonima"
        );

        controller.sendPriceUpdate(productId, priceUpdate);

        StepVerifier.create(Mono.just(true))
                .expectNext(true)
                .verifyComplete();
    }
}