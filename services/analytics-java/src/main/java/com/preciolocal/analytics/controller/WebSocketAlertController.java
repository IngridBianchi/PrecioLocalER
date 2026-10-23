package com.preciolocal.analytics.controller;

import com.preciolocal.analytics.model.AlertEvent;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.messaging.handler.annotation.DestinationVariable;
import org.springframework.messaging.handler.annotation.MessageMapping;
import org.springframework.messaging.handler.annotation.SendTo;
import org.springframework.messaging.simp.SimpMessagingTemplate;
import org.springframework.stereotype.Controller;

@Controller
public class WebSocketAlertController {

    private static final Logger log = LoggerFactory.getLogger(WebSocketAlertController.class);

    private final SimpMessagingTemplate messagingTemplate;

    public WebSocketAlertController(SimpMessagingTemplate messagingTemplate) {
        this.messagingTemplate = messagingTemplate;
    }

    @MessageMapping("/alerts/subscribe/{userId}")
    @SendTo("/topic/alerts/{userId}")
    public AlertEvent subscribeToAlerts(@DestinationVariable String userId) {
        log.info("User {} subscribed to alerts", userId);
        return null;
    }

    @MessageMapping("/prices/subscribe/{productId}")
    @SendTo("/topic/prices/{productId}")
    public void subscribeToPrices(@DestinationVariable Long productId) {
        log.info("Client subscribed to price updates for product {}", productId);
    }

    public void sendAlertToUser(String userId, AlertEvent event) {
        messagingTemplate.convertAndSend("/topic/alerts/" + userId, event);
    }

    public void sendPriceUpdate(Long productId, Object priceUpdate) {
        messagingTemplate.convertAndSend("/topic/prices/" + productId, priceUpdate);
    }
}