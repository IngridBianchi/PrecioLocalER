package com.preciolocal.analytics.model;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;
import org.springframework.data.annotation.Id;
import org.springframework.data.mongodb.core.mapping.Document;
import org.springframework.data.relational.core.mapping.Table;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.UUID;

@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
@Table("alerts")
@Document(collection = "alerts")
public class Alert {

    @Id
    private Long id;
    private UUID userId;
    private Long productId;
    private Direction direction;
    private BigDecimal targetPrice;
    private boolean active;
    private Instant lastTriggeredAt;
    private Instant createdAt;

    public enum Direction {
        BELOW, ABOVE
    }
}