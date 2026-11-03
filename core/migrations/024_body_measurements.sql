-- Body measurements, T1 (03.11.2026; spec docs/specs/body-measurements-spec.md, plan and the owner's answers in the doc
-- "Logger v2: status and web app plan", Current tab): five tape measurements in cm, about every 28 days, from the Health
-- card's form or typed ("waist 123.5 hips 128", read without the AI by tape.py). Stored like weight, one value per measure
-- and health day. The notes hold the landmark the form shows as a hint.
INSERT INTO log_types (name, label, kind, category, unit, min1, max1, aliases, on_checklist, daily_target, active, sort_order, notes)
VALUES ('waist', 'Waist', 'measurement', 'vitals', 'cm', 50, 200, '{belly,tummy,waistline}', false, 1, true, 15,
        'at the navel, after a normal breath out'),
       ('hips', 'Hips', 'measurement', 'vitals', 'cm', 60, 200, '{hip,butt}', false, 1, true, 16,
        'widest point of the buttocks'),
       ('chest', 'Chest', 'measurement', 'vitals', 'cm', 60, 200, '{}', false, 1, true, 17,
        'across the nipples, arms down'),
       ('upper_arm', 'Upper arm', 'measurement', 'vitals', 'cm', 15, 70, '{arm,bicep,biceps}', false, 1, true, 18,
        'halfway shoulder to elbow, relaxed, right side'),
       ('thigh', 'Thigh', 'measurement', 'vitals', 'cm', 30, 110, '{leg}', false, 1, true, 19,
        'halfway hip to knee, standing, right side')
ON CONFLICT (name) DO NOTHING;

-- The owner's height for waist ÷ height. Dated like food_targets, so a later change does not rewrite past ratios: a
-- session uses the height valid on its day. Not visible to the Sheet's reader.
CREATE TABLE body_height (
  id          serial PRIMARY KEY,
  valid_from  date NOT NULL UNIQUE,
  height_cm   numeric NOT NULL CHECK (height_cm BETWEEN 120 AND 230),
  logged_at   timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER log_change_trg AFTER INSERT OR UPDATE OR DELETE ON body_height FOR EACH ROW EXECUTE FUNCTION log_change();

GRANT SELECT, INSERT, UPDATE, DELETE ON body_height TO core_rw;
GRANT USAGE, SELECT ON SEQUENCE body_height_id_seq TO core_rw;
