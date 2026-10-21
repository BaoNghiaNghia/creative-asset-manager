-- Controlled one-time Catinat location bootstrap, no inventory balance writes.
BEGIN;
DO $bootstrap$
DECLARE target_tenant varchar(36);
DECLARE matches integer;
DECLARE created integer;
BEGIN
  PERFORM pg_advisory_xact_lock(43214321);
  SELECT COUNT(*), MIN(tenant_id) INTO matches, target_tenant
  FROM inventory_settings
  WHERE enabled = TRUE
    AND daily_sheet_automation_enabled = TRUE
    AND daily_sheet_config_json->>'version' = '4'
    AND (daily_sheet_config_json->'source'->'allowed_sheets')::jsonb
      @> '["KHO PHA CHẾ", "KHO PHÒNG RANG", "PHÒNG PHA CHẾ"]'::jsonb;
  IF matches <> 1 THEN
    RAISE EXCEPTION 'expected_one_authorized_catinat_inventory_tenant';
  END IF;
  IF EXISTS (
    SELECT 1 FROM inventory_locations WHERE tenant_id = target_tenant
    AND (
      (code = 'CATINAT_KHO_PHA_CHE' AND name <> 'KHO PHA CHẾ') OR
      (code = 'CATINAT_KHO_PHONG_RANG' AND name <> 'KHO PHÒNG RANG') OR
      (code = 'CATINAT_PHONG_PHA_CHE' AND name <> 'PHÒNG PHA CHẾ') OR
      (name = 'KHO PHA CHẾ' AND code <> 'CATINAT_KHO_PHA_CHE') OR
      (name = 'KHO PHÒNG RANG' AND code <> 'CATINAT_KHO_PHONG_RANG') OR
      (name = 'PHÒNG PHA CHẾ' AND code <> 'CATINAT_PHONG_PHA_CHE')
    )
  ) THEN
    RAISE EXCEPTION 'conflicting_location_identity';
  END IF;
  INSERT INTO inventory_locations (id, tenant_id, code, name, active, created_at, updated_at)
  SELECT gen_random_uuid()::text, target_tenant, entries.code, entries.name, TRUE, NOW(), NOW()
  FROM (VALUES
    ('CATINAT_KHO_PHA_CHE', 'KHO PHA CHẾ'),
    ('CATINAT_KHO_PHONG_RANG', 'KHO PHÒNG RANG'),
    ('CATINAT_PHONG_PHA_CHE', 'PHÒNG PHA CHẾ')
  ) AS entries(code, name)
  WHERE NOT EXISTS (
    SELECT 1 FROM inventory_locations
    WHERE tenant_id = target_tenant AND code = entries.code AND name = entries.name
  );
  GET DIAGNOSTICS created = ROW_COUNT;
  RAISE NOTICE 'catinat_locations_created=%', created;
END
$bootstrap$;
COMMIT;
