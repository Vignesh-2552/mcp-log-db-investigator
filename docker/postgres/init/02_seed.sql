-- Seed data for local dev/testing. Includes the order/user ids referenced
-- in design doc §5's example investigation flow (order 88213, user 4417).

INSERT INTO public.users (email, phone, name, created_at) VALUES
    ('asha.mehta@example.com', '9820011223', 'Asha Mehta', now() - interval '90 days'),
    ('rahul.iyer@example.com', '9833344556', 'Rahul Iyer', now() - interval '60 days'),
    ('priya.nair@example.com', '9845566778', 'Priya Nair', now() - interval '45 days'),
    ('vikram.rao@example.com', '9856677889', 'Vikram Rao', now() - interval '30 days');

-- Explicit id so it matches the doc's example ("user 4417").
INSERT INTO public.users (id, email, phone, name, created_at) VALUES
    (4417, 'deepak.shah@example.com', '9867788990', 'Deepak Shah', now() - interval '120 days');
SELECT setval('public.users_id_seq', (SELECT max(id) FROM public.users));

INSERT INTO public.orders (user_id, status, error_code, amount_cents, request_id, created_at, updated_at) VALUES
    (1, 'PAID', NULL, 249900, 'req-a1b2c3d4', now() - interval '5 days', now() - interval '5 days'),
    (2, 'PAID', NULL, 89900, 'req-b2c3d4e5', now() - interval '4 days', now() - interval '4 days'),
    (3, 'FAILED', 'CARD_DECLINED', 149900, 'req-c3d4e5f6', now() - interval '3 days', now() - interval '3 days'),
    (1, 'FAILED', 'GATEWAY_502', 349900, 'req-d4e5f6g7', now() - interval '1 day 2 hours', now() - interval '1 day 2 hours'),
    (4, 'PAID', NULL, 59900, 'req-e5f6g7h8', now() - interval '10 hours', now() - interval '10 hours');

-- Explicit id so it matches the doc's example ("order 88213").
INSERT INTO public.orders (id, user_id, status, error_code, amount_cents, request_id, created_at, updated_at) VALUES
    (88213, 4417, 'FAILED', 'GATEWAY_502', 150000, 'req-9f2c1a2b3c4d', now() - interval '1 day 3 hours', now() - interval '1 day 3 hours');
SELECT setval('public.orders_id_seq', (SELECT max(id) FROM public.orders));

INSERT INTO public.payments (order_id, provider, status, provider_ref, amount_cents, created_at) VALUES
    (1, 'razorpay', 'CAPTURED', 'pay_a1b2c3', 249900, now() - interval '5 days'),
    (2, 'razorpay', 'CAPTURED', 'pay_b2c3d4', 89900, now() - interval '4 days'),
    (3, 'razorpay', 'FAILED', 'pay_c3d4e5', 149900, now() - interval '3 days'),
    (5, 'razorpay', 'CAPTURED', 'pay_e5f6g7', 59900, now() - interval '10 hours'),
    (88213, 'razorpay', 'FAILED', 'pay_9f2c1a2b', 150000, now() - interval '1 day 3 hours');

INSERT INTO public.audit_events (entity_type, entity_id, event_type, payload, created_at) VALUES
    ('order', '3', 'STATUS_CHANGED', '{"from": "PENDING", "to": "FAILED", "reason": "card_declined"}', now() - interval '3 days'),
    ('order', '88213', 'STATUS_CHANGED', '{"from": "PENDING", "to": "FAILED", "reason": "gateway_502"}', now() - interval '1 day 3 hours'),
    ('payment', 'pay_9f2c1a2b', 'PROVIDER_ERROR', '{"status_code": 502, "message": "Bad Gateway"}', now() - interval '1 day 3 hours');

ANALYZE public.users, public.orders, public.payments, public.audit_events;
