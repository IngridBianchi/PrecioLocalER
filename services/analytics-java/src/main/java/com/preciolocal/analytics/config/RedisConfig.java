package com.preciolocal.analytics.config;

import io.lettuce.core.RedisClient;
import io.lettuce.core.RedisURI;
import io.lettuce.core.api.reactive.RedisReactiveCommands;
import io.lettuce.core.pubsub.api.reactive.RedisPubSubReactiveCommands;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class RedisConfig {

    @Value("${spring.redis.host:localhost}")
    private String host;

    @Value("${spring.redis.port:6381}")
    private int port;

    @Value("${spring.redis.password:}")
    private String password;

    @Bean
    public RedisClient redisClient() {
        RedisURI.Builder builder = RedisURI.Builder.redis(host, port);
        if (password != null && !password.isBlank()) {
            builder.withPassword(password.toCharArray());
        }
        return RedisClient.create(builder.build());
    }

    @Bean
    public RedisReactiveCommands<String, String> redisCommands(RedisClient client) {
        return client.connect().reactive();
    }

    @Bean
    public RedisPubSubReactiveCommands<String, String> redisPubSubCommands(RedisClient client) {
        return client.connectPubSub().reactive();
    }
}