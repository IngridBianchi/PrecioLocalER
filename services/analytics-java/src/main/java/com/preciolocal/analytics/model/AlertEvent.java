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

@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
@Table("alert_events")
@Document(collection = "alert_events")
public class AlertEvent {

    @Id
    private Long id;
    private Long alertId;
    private Long pricePointId;
    private BigDecimal price;
    private Instant triggeredAt;
}