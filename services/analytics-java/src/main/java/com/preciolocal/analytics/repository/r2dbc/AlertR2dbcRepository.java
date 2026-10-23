package com.preciolocal.analytics.repository.r2dbc;

import com.preciolocal.analytics.model.Alert;
import com.preciolocal.analytics.model.AlertEvent;
import org.springframework.data.r2dbc.repository.Query;
import org.springframework.data.r2dbc.repository.R2dbcRepository;
import org.springframework.data.repository.query.Param;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;

public interface AlertR2dbcRepository extends R2dbcRepository<Alert, Long> {

    @Query("SELECT * FROM alerts WHERE user_id = :userId AND active = true")
    Flux<Alert> findActiveByUserId(@Param("userId") java.util.UUID userId);

    @Query("SELECT * FROM alerts WHERE user_id = :userId")
    Flux<Alert> findByUserId(@Param("userId") java.util.UUID userId);

    @Query("SELECT * FROM alerts WHERE id = :id")
    Mono<Alert> findById(@Param("id") Long id);

    @Query("SELECT * FROM alerts WHERE product_id = :productId AND active = true")
    Flux<Alert> findActiveByProductId(@Param("productId") Long productId);

    @Query("""
        UPDATE alerts 
        SET last_triggered_at = now() 
        WHERE id = :id
        """)
    Mono<Integer> updateLastTriggered(@Param("id") Long id);

    @Query("""
        INSERT INTO alert_events (alert_id, price_point_id, price, triggered_at)
        VALUES (:alertId, :pricePointId, :price, now())
        RETURNING id, alert_id, price_point_id, price, triggered_at
        """)
    Mono<AlertEvent> insertAlertEvent(
            @Param("alertId") Long alertId,
            @Param("pricePointId") Long pricePointId,
            @Param("price") java.math.BigDecimal price
    );

    @Query("SELECT * FROM alert_events WHERE alert_id = :alertId ORDER BY triggered_at DESC")
    Flux<AlertEvent> findByAlertId(@Param("alertId") Long alertId);
}