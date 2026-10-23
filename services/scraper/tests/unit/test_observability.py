"""Tests for observability module."""

import uuid
from unittest.mock import MagicMock, patch

import pytest
from app.observability import (
    CorrelationIdMiddleware,
    get_correlation_id,
    get_tracer,
    get_user_id,
    inject_trace_context,
    set_correlation_id,
    set_user_id,
)


class TestObservability:
    """Tests for observability functions."""

    def test_set_get_correlation_id(self):
        """Test setting and getting correlation ID."""
        test_id = "test-correlation-123"
        set_correlation_id(test_id)
        assert get_correlation_id() == test_id

    def test_set_get_user_id(self):
        """Test setting and getting user ID."""
        test_id = "user-456"
        set_user_id(test_id)
        assert get_user_id() == test_id

    def test_inject_trace_context(self):
        """Test trace context injection into headers."""
        headers = {}
        with patch('app.observability.TraceContextTextMapPropagator') as mock_propagator:
            mock_instance = MagicMock()
            mock_propagator.return_value = mock_instance
            mock_instance.inject.return_value = None
            
            inject_trace_context(headers)
            
            mock_instance.inject.assert_called_once()

    def test_get_tracer(self):
        """Test getting a tracer instance."""
        tracer = get_tracer("test.module")
        assert tracer is not None
        assert hasattr(tracer, 'start_span')


class TestCorrelationIdMiddleware:
    """Tests for CorrelationIdMiddleware."""

    @pytest.mark.asyncio
    async def test_middleware_sets_contextvar_and_response_header(self):
        """El ID entrante viaja por contextvar y se devuelve en la respuesta."""
        middleware = CorrelationIdMiddleware(None)

        scope = {
            "type": "http",
            "headers": [
                (b"x-correlation-id", b"test-correlation-id"),
            ],
        }
        seen: list[str | None] = []
        messages: list[dict] = []

        async def mock_app(scope, receive, send):
            seen.append(get_correlation_id())
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        async def receive():
            return {"type": "http.request"}

        async def send(message):
            messages.append(message)

        middleware.app = mock_app

        previous = "previous-correlation"
        set_correlation_id(previous)
        await middleware(scope, receive, send)

        assert seen == ["test-correlation-id"]
        assert get_correlation_id() == previous, "el contextvar se restaura al salir"
        assert (b"x-correlation-id", b"test-correlation-id") in messages[0]["headers"]

    @pytest.mark.asyncio
    async def test_middleware_generates_correlation_id_if_missing(self):
        """Sin header, genera un UUID y también lo devuelve en la respuesta."""
        middleware = CorrelationIdMiddleware(None)

        scope = {
            "type": "http",
            "headers": [],
        }
        seen: list[str | None] = []
        messages: list[dict] = []

        async def mock_app(scope, receive, send):
            seen.append(get_correlation_id())
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        async def receive():
            return {"type": "http.request"}

        async def send(message):
            messages.append(message)

        middleware.app = mock_app

        previous = "previous-correlation"
        set_correlation_id(previous)
        await middleware(scope, receive, send)

        assert len(seen) == 1
        generated = seen[0]
        assert generated is not None
        uuid.UUID(generated)  # es un UUID válido
        assert (b"x-correlation-id", generated.encode()) in messages[0]["headers"]
        assert get_correlation_id() == previous


if __name__ == "__main__":
    pytest.main([__file__, "-v"])