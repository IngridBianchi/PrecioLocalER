package com.preciolocal.analytics.integration;

import com.preciolocal.analytics.model.Alert;
import com.preciolocal.analytics.model.AlertEvent;
import com.preciolocal.analytics.repository.r2dbc.AlertR2dbcRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.r2dbc.core.DatabaseClient;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.testcontainers.containers.MongoDBContainer;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.utility.DockerImageName;
import reactor.core.publisher.Flux;
import reactor.test.StepVerifier;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.UUID;

@Testcontainers
@SpringBootTest
class AlertRepositoryIntegrationTest {

    @Container
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("postgres:16-alpine")
            .withDatabaseName("preciolocal")
            .withUsername("preciolocal")
            .withPassword("preciolocal")
            .withInitScript("db/supabase/migrations/001_init.sql");

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
    private AlertR2dbcRepository alertRepository;

    @Autowired
    private DatabaseClient databaseClient;

    private UUID testUserId;
    private Long testProductId;

    @BeforeEach
    void setUp() {
        testUserId = UUID.randomUUID();
        testProductId = 999L;
        databaseClient.sql("DELETE FROM alert_events").then().block();
        databaseClient.sql("DELETE FROM alerts").then().block();
        createUser(testUserId);
        databaseClient.sql("""
                        INSERT INTO chains (id, name, slug)
                        OVERRIDING SYSTEM VALUE
                        VALUES (1, 'Analytics Test Chain', 'analytics_test_chain')
                        ON CONFLICT (id) DO NOTHING
                        """)
                .then()
                .block();
        databaseClient.sql("""
                        INSERT INTO products (id, name, normalized_name, size_unit)
                        OVERRIDING SYSTEM VALUE
                        VALUES (999, 'Analytics Test Product', 'analytics test product', 'und'),
                               (1000, 'Analytics Test Product 2', 'analytics test product 2', 'und')
                        ON CONFLICT (id) DO NOTHING
                        """)
                .then()
                .block();
        databaseClient.sql("""
                        INSERT INTO price_points (id, product_id, chain_id, price, captured_at, raw_json, hash)
                        OVERRIDING SYSTEM VALUE
                        VALUES (12345, 999, 1, 95.00, now(), '{}'::jsonb, 'analytics-test-price-point-12345')
                        ON CONFLICT (id) DO NOTHING
                        """)
                .then()
                .block();
    }

    private void createUser(UUID userId) {
        databaseClient.sql("INSERT INTO users (id, email) VALUES (:id, :email)")
                .bind("id", userId)
                .bind("email", "test-" + userId + "@example.com")
                .then()
                .block();
    }

    @Test
    void saveAndFindAlert_works() {
        Alert alert = Alert.builder()
                .userId(testUserId)
                .productId(testProductId)
                .direction(Alert.Direction.BELOW)
                .targetPrice(new BigDecimal("100.00"))
                .active(true)
                .build();

        Alert saved = alertRepository.save(alert).block();

        StepVerifier.create(alertRepository.findById(saved.getId()))
                .expectNextMatches(a -> a.getUserId().equals(testUserId) &&
                        a.getProductId().equals(testProductId) &&
                        a.getDirection() == Alert.Direction.BELOW &&
                        a.getTargetPrice().compareTo(new BigDecimal("100.00")) == 0 &&
                        a.isActive())
                .verifyComplete();
    }

    @Test
    void findActiveByUserId_returnsOnlyActive() {
        Alert activeAlert = Alert.builder()
                .userId(testUserId)
                .productId(testProductId)
                .direction(Alert.Direction.BELOW)
                .targetPrice(new BigDecimal("100.00"))
                .active(true)
                .build();

        Alert inactiveAlert = Alert.builder()
                .userId(testUserId)
                .productId(testProductId + 1)
                .direction(Alert.Direction.ABOVE)
                .targetPrice(new BigDecimal("200.00"))
                .active(false)
                .build();

        alertRepository.save(activeAlert).block();
        alertRepository.save(inactiveAlert).block();

        StepVerifier.create(alertRepository.findActiveByUserId(testUserId))
                .expectNextCount(1)
                .verifyComplete();
    }

    @Test
    void findActiveByProductId_returnsMatchingAlerts() {
        UUID anotherUser = UUID.randomUUID();
        createUser(anotherUser);

        Alert alert1 = Alert.builder()
                .userId(testUserId)
                .productId(testProductId)
                .direction(Alert.Direction.BELOW)
                .targetPrice(new BigDecimal("100.00"))
                .active(true)
                .build();

        Alert alert2 = Alert.builder()
                .userId(anotherUser)
                .productId(testProductId)
                .direction(Alert.Direction.ABOVE)
                .targetPrice(new BigDecimal("200.00"))
                .active(true)
                .build();

        alertRepository.save(alert1).block();
        alertRepository.save(alert2).block();

        StepVerifier.create(alertRepository.findActiveByProductId(testProductId))
                .expectNextCount(2)
                .verifyComplete();
    }

    @Test
    void insertAlertEvent_persistsCorrectly() {
        Alert alert = Alert.builder()
                .userId(testUserId)
                .productId(testProductId)
                .direction(Alert.Direction.BELOW)
                .targetPrice(new BigDecimal("100.00"))
                .active(true)
                .build();

        Alert savedAlert = alertRepository.save(alert).block();

        AlertEvent event = alertRepository.insertAlertEvent(
                savedAlert.getId(),
                12345L,
                new BigDecimal("95.00")
        ).block();

        StepVerifier.create(alertRepository.findByAlertId(savedAlert.getId()))
                .expectNextMatches(e -> e.getAlertId().equals(savedAlert.getId()) &&
                        e.getPricePointId().equals(12345L) &&
                        e.getPrice().compareTo(new BigDecimal("95.00")) == 0)
                .verifyComplete();
    }

    @Test
    void updateLastTriggered_updatesTimestamp() {
        Alert alert = Alert.builder()
                .userId(testUserId)
                .productId(testProductId)
                .direction(Alert.Direction.BELOW)
                .targetPrice(new BigDecimal("100.00"))
                .active(true)
                .build();

        Alert savedAlert = alertRepository.save(alert).block();
        Instant before = savedAlert.getLastTriggeredAt();

        alertRepository.updateLastTriggered(savedAlert.getId()).block();

        StepVerifier.create(alertRepository.findById(savedAlert.getId()))
                .expectNextMatches(a -> a.getLastTriggeredAt() != null &&
                        (before == null || a.getLastTriggeredAt().isAfter(before)))
                .verifyComplete();
    }
}