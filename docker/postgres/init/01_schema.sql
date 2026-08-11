-- Sample schema for local development/testing of the investigation MCP server.
-- Matches the table allowlist example in doc §7.2 and the cookbook queries in Appendix B.

CREATE TABLE public.users (
    id BIGSERIAL PRIMARY KEY,
    email TEXT NOT NULL,
    phone TEXT,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE public.users IS 'End users who can place orders.';

CREATE TABLE public.orders (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES public.users (id),
    status TEXT NOT NULL,
    error_code TEXT,
    amount_cents BIGINT NOT NULL,
    request_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE public.orders IS 'Checkout orders; status in (PENDING, PAID, FAILED, REFUNDED).';
CREATE INDEX idx_orders_created_at ON public.orders (created_at);
CREATE INDEX idx_orders_status ON public.orders (status);
CREATE INDEX idx_orders_user_id ON public.orders (user_id);

CREATE TABLE public.payments (
    id BIGSERIAL PRIMARY KEY,
    order_id BIGINT NOT NULL REFERENCES public.orders (id),
    provider TEXT NOT NULL,
    status TEXT NOT NULL,
    provider_ref TEXT,
    amount_cents BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE public.payments IS 'Payment gateway attempts linked to an order.';
CREATE INDEX idx_payments_order_id ON public.payments (order_id);
CREATE INDEX idx_payments_created_at ON public.payments (created_at);

CREATE TABLE public.audit_events (
    id BIGSERIAL PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE public.audit_events IS 'Generic entity event trail (order/payment/user lifecycle events).';
CREATE INDEX idx_audit_events_entity ON public.audit_events (entity_type, entity_id);

-- Dedicated read-only role (design doc §6.1) — the DB itself is the last
-- line of defence, not just the application-layer guardrail.
CREATE ROLE mcp_ro LOGIN PASSWORD 'mcp_ro';
GRANT USAGE ON SCHEMA public TO mcp_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO mcp_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO mcp_ro;
