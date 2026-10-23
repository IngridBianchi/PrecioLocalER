package com.preciolocal.analytics.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.server.WebFilter;

@Configuration
public class WebFilterConfig {

    @Bean
    public WebFilter correlationIdFilter() {
        return new CorrelationIdFilter();
    }
}