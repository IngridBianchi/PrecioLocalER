package com.preciolocal.analytics.config;

import com.preciolocal.analytics.model.Alert;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.convert.converter.Converter;
import org.springframework.data.convert.ReadingConverter;
import org.springframework.data.convert.WritingConverter;
import org.springframework.data.r2dbc.convert.R2dbcCustomConversions;
import org.springframework.data.r2dbc.dialect.PostgresDialect;

import java.util.List;
import java.util.Locale;

/**
 * PostgreSQL guarda `direction` en minúsculas (`below`/`above`), mientras que
 * el enum de Java usa las constantes en mayúsculas. Sin estos conversores
 * Spring Data R2DBC escribe `BELOW` y el check constraint rechaza la fila.
 */
@Configuration
public class R2dbcConfig {

    @Bean
    public R2dbcCustomConversions r2dbcCustomConversions() {
        return R2dbcCustomConversions.of(
                PostgresDialect.INSTANCE,
                List.of(new DirectionToStringConverter(), new StringToDirectionConverter())
        );
    }

    @WritingConverter
    static class DirectionToStringConverter implements Converter<Alert.Direction, String> {
        @Override
        public String convert(Alert.Direction source) {
            return source.name().toLowerCase(Locale.ROOT);
        }
    }

    @ReadingConverter
    static class StringToDirectionConverter implements Converter<String, Alert.Direction> {
        @Override
        public Alert.Direction convert(String source) {
            return Alert.Direction.valueOf(source.toUpperCase(Locale.ROOT));
        }
    }
}
