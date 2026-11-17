-- Minimal schema for the `integration`-marked tests in CI
-- (src/opsinsight/service/database_service_integration_test.py).
-- Mirrors the table/column names those tests use; data is synthetic.
CREATE SCHEMA private;

CREATE TABLE private.customer (
    id uuid PRIMARY KEY,
    email text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE private.orders (
    id uuid PRIMARY KEY,
    order_number text NOT NULL,
    customer_id uuid NOT NULL REFERENCES private.customer (id),
    status text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE private.order_payment (
    id uuid PRIMARY KEY,
    order_id uuid NOT NULL REFERENCES private.orders (id),
    amount numeric(12, 2) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Unrelated tables that also carry order_id, sorting before order_payment:
-- the identifier search must still rank order_payment inside its cap.
CREATE TABLE private.fulfillment (
    id uuid PRIMARY KEY,
    order_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO private.customer (id, email) VALUES
    ('11111111-1111-1111-1111-111111111111', 'jane@example.com'),
    ('22222222-2222-2222-2222-222222222222', 'raj@example.com');

INSERT INTO private.orders (id, order_number, customer_id, status, created_at) VALUES
    ('aaaaaaaa-0000-0000-0000-000000000001', 'ORD-1001', '11111111-1111-1111-1111-111111111111', 'paid', now() - interval '2 hours'),
    ('aaaaaaaa-0000-0000-0000-000000000002', 'ORD-1002', '22222222-2222-2222-2222-222222222222', 'failed', now() - interval '1 hour');

INSERT INTO private.order_payment (id, order_id, amount, created_at) VALUES
    ('bbbbbbbb-0000-0000-0000-000000000001', 'aaaaaaaa-0000-0000-0000-000000000001', 49.90, now() - interval '2 hours'),
    ('bbbbbbbb-0000-0000-0000-000000000002', 'aaaaaaaa-0000-0000-0000-000000000002', 12.00, now() - interval '30 minutes');

INSERT INTO private.fulfillment (id, order_id) VALUES
    ('cccccccc-0000-0000-0000-000000000002', 'aaaaaaaa-0000-0000-0000-000000000002');

ANALYZE;
