-- Copied from logger-app sql/schema-v4-labs.sql (v1 schema v4 labs). Only change: role mcp_writer -> core_rw, NocoDB and sheets_reader grants removed.
-- schema-v4-labs.sql  (additive only: new tables and views; nothing existing is changed)
-- Lab and imaging reports, their results, a test catalogue, and medication courses.
-- Run as app_owner in database appdata, like v3:
--   docker compose exec -T db psql -U pgadmin -d appdata -v ON_ERROR_STOP=1 -c "SET ROLE app_owner" -f - < sql/schema-v4-labs.sql
-- Needs schema v2 (raw_inbox) and v3 (measurements, log_types; used by v_glucose). One transaction.
--
-- Shape
--   lab_tests    catalogue of standard test names (one standard unit each, aliases incl. German, comparability note)
--   lab_reports  one row per report (lab or imaging), raw_id = verbatim source, times stored in the lab's own time zone
--   lab_results  one row per value or finding; value in the standard unit, printed value/unit/range kept as printed
--   medications  one row per course (name, dose, start, stop); a dose change = stop one course, start another
-- Display ids in chat: R12 report, V345 result, D3 medication course.

BEGIN;

CREATE TABLE lab_tests (
  id           smallserial PRIMARY KEY,
  name         text NOT NULL CHECK (btrim(name) <> ''),
  category     text NOT NULL CHECK (category IN ('lab','imaging')),
  unit         text NOT NULL DEFAULT '',          -- standard unit; '' for text findings and unitless values
  aliases      text[] NOT NULL DEFAULT '{}',      -- lower case; printed names (also German) that mean this test
  compare_note text,                              -- shown with every trend (method changes, not comparable ...)
  created_by   text NOT NULL DEFAULT 'import' CHECK (created_by IN ('import','claude','user')),
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX lab_tests_name_ci ON lab_tests (lower(name));

CREATE TABLE lab_reports (
  id                   serial PRIMARY KEY,
  raw_id               bigint NOT NULL REFERENCES raw_inbox(id),
  report_type          text NOT NULL CHECK (report_type IN ('lab','imaging')),
  title                text NOT NULL,
  provider             text NOT NULL,
  department           text,
  city                 text,
  country              text,
  tz                   text NOT NULL DEFAULT 'Europe/Berlin',   -- the lab's time zone; times below are wall-clock times there
  accession_no         text,
  ordering_doctor      text,
  signing_doctors      text,
  collected_date       date NOT NULL,                           -- sample or scan date as printed
  collected_at         timestamptz NOT NULL,
  collected_time_known boolean NOT NULL DEFAULT false,          -- false = only the date is known (stored at 12:00 local)
  reported_at          timestamptz,
  reported_time_known  boolean NOT NULL DEFAULT false,
  report_status        text,
  fasting_status       text,
  source               text NOT NULL DEFAULT 'image' CHECK (source IN ('image','document','import')),
  source_detail        text,                                    -- file name, "photo of 3 pages", ...
  document_role        text,                                    -- primary, translation, transcription ...
  notes                text,
  details              jsonb NOT NULL DEFAULT '{}',
  logged_at            timestamptz NOT NULL DEFAULT now(),
  extractor_version    text NOT NULL,
  needs_review         boolean NOT NULL DEFAULT false,
  review_reason        text,
  import_key           text UNIQUE
);
CREATE INDEX lab_reports_date ON lab_reports (collected_date);

CREATE TABLE lab_results (
  id                bigserial PRIMARY KEY,
  report_id         integer NOT NULL REFERENCES lab_reports(id) ON DELETE CASCADE,
  test_id           smallint NOT NULL REFERENCES lab_tests(id),
  name_printed      text,                                       -- test name exactly as printed
  panel             text,
  body_site         text,                                       -- imaging and DEXA regions
  side              text CHECK (side IN ('left','right','both')),
  specimen          text,
  method            text,
  is_calculated     boolean,                                    -- NULL = not stated
  qualifier         text CHECK (qualifier IN ('<','>','<=','>=')),
  value             numeric,                                    -- in the standard unit (NULL for text, or if not convertible)
  unit              text,                                       -- standard unit at the time of storing
  value_text        text,                                       -- text results and findings
  value_raw         text,                                       -- as printed
  unit_raw          text,
  conversion        text,                                       -- formula used, if the unit was converted
  ref_range_raw     text,                                       -- as printed by that lab
  ref_low           numeric,                                    -- normal band in the standard unit
  ref_high          numeric,
  flag              text CHECK (flag IN ('L','H','N','A')),     -- against that lab's own range; A = abnormal text finding
  note              text,
  details           jsonb NOT NULL DEFAULT '{}',
  logged_at         timestamptz NOT NULL DEFAULT now(),
  extractor_version text NOT NULL,
  needs_review      boolean NOT NULL DEFAULT false,
  review_reason     text,
  import_key        text UNIQUE,
  CHECK (value IS NOT NULL OR value_text IS NOT NULL OR value_raw IS NOT NULL)
);
CREATE INDEX lab_results_test ON lab_results (test_id);
CREATE INDEX lab_results_report ON lab_results (report_id);

CREATE TABLE medications (
  id                serial PRIMARY KEY,
  raw_id            bigint NOT NULL REFERENCES raw_inbox(id),
  name              text NOT NULL,          -- as on the pack (brand)
  ingredient        text,
  dose              text,                   -- e.g. '5 mg'
  frequency         text,                   -- e.g. 'once daily, with food'
  drug_class        text,
  indication        text,
  start_date        date,                   -- NULL = start not known
  stop_date         date,                   -- NULL = still taken
  stop_reason       text,
  note              text,
  details           jsonb NOT NULL DEFAULT '{}',
  logged_at         timestamptz NOT NULL DEFAULT now(),
  extractor_version text NOT NULL,
  needs_review      boolean NOT NULL DEFAULT false,
  review_reason     text,
  import_key        text UNIQUE,
  CHECK (stop_date IS NULL OR start_date IS NULL OR stop_date >= start_date)
);

-- Readable views (NocoDB and questions)
CREATE VIEW v_lab_results AS
SELECT r.id, r.report_id, p.collected_date, p.provider, p.city, p.report_type, t.name AS test, t.category,
       r.panel, r.body_site, r.side, r.qualifier, r.value, r.unit, r.value_text, r.ref_low, r.ref_high, r.flag,
       r.method, r.is_calculated, r.note, r.needs_review, r.review_reason
FROM lab_results r JOIN lab_reports p ON p.id = r.report_id JOIN lab_tests t ON t.id = r.test_id;

CREATE VIEW v_medications AS
SELECT id, name, ingredient, dose, frequency, drug_class, indication, start_date, stop_date,
       (stop_date IS NULL OR stop_date > current_date) AS active, note, needs_review, review_reason
FROM medications;

-- Fasting glucose from the daily meter and from lab reports, side by side (mg/dL).
CREATE VIEW v_glucose AS
SELECT m.day, 'meter'::text AS source, m.value1 AS mg_dl, m.event_at AS at
FROM measurements m JOIN log_types t ON t.id = m.type_id AND t.name = 'blood_sugar'
UNION ALL
SELECT p.collected_date, 'lab', r.value, p.collected_at
FROM lab_results r JOIN lab_tests t ON t.id = r.test_id AND t.name = 'Glucose, fasting'
JOIN lab_reports p ON p.id = r.report_id
WHERE r.value IS NOT NULL;

-- Rights, per table on purpose (same pattern as before)
-- (grant for nocodb_ui removed; NocoDB comes back after cutover)
GRANT SELECT, INSERT ON lab_tests TO core_rw;                    -- new tests from new reports; no edits or deletes
GRANT SELECT, INSERT, UPDATE, DELETE ON lab_reports, lab_results, medications TO core_rw;   -- report delete: server allows it only for fresh chat reports
GRANT SELECT ON v_lab_results, v_medications, v_glucose TO core_rw;
GRANT USAGE, SELECT ON SEQUENCE lab_tests_id_seq, lab_reports_id_seq, lab_results_id_seq, medications_id_seq TO core_rw;

COMMIT;
