package com.preciolocal.analytics.repository.mongodb;

import com.preciolocal.analytics.model.Alert;
import com.preciolocal.analytics.model.AlertEvent;
import org.springframework.data.mongodb.repository.ReactiveMongoRepository;
import org.springframework.data.mongodb.repository.Query;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;

public interface AlertMongoRepository extends ReactiveMongoRepository<Alert, Long> {

    @Query("{ 'userId': ?0, 'active': true }")
    Flux<Alert> findActiveByUserId(java.util.UUID userId);

    @Query("{ 'productId': ?0, 'active': true }")
    Flux<Alert> findActiveByProductId(Long productId);
}

interface AlertEventMongoRepository extends ReactiveMongoRepository<AlertEvent, Long> {

    @Query("{ 'alertId': ?0 }")
    Flux<AlertEvent> findByAlertId(Long alertId);

    @Query("{ 'userId': ?0 }")
    Flux<AlertEvent> findByUserId(java.util.UUID userId);
}