package com.preciolocal.analytics.model;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.math.BigDecimal;
import java.time.Instant;

@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class PricePointEvent {

    private Long id;
    private Long productId;
    private String chain;
    private Long chainId;
    private Long skuId;
    private Long storeId;
    private BigDecimal price;
    private String promoType;
    private Instant capturedAt;
    private Instant scrapedAt;
    private String hash;
}