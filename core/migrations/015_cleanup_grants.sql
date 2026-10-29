-- The daily clean-up (core/app/maintenance.py) removes old idempotency keys and old answered or expired questions.
-- It runs as the service's own login, so that login may delete from these two tables and no others new.
GRANT DELETE ON idempotency_keys, pending TO core_rw;
