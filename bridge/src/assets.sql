-- Preserve every source ID and full geometry. No inferred buses or electrical flow.
WITH bounds AS (
    SELECT ST_MakeEnvelope(126, 33, 127, 33.7, 4326) AS geom
), assets AS (
    SELECT p.id, 'power_line'::text AS kind, p.geom,
           to_jsonb(p) - 'geom' AS properties
    FROM public.power_line p, bounds b
    WHERE p.geom IS NOT NULL AND (p.sido = '제주특별자치도' OR ST_Intersects(p.geom, b.geom))
    UNION ALL
    SELECT p.id, 'substation', p.geom, to_jsonb(p) - 'geom'
    FROM public.substation p, bounds b
    WHERE p.geom IS NOT NULL AND ST_Intersects(p.geom, b.geom)
    UNION ALL
    SELECT p.id, 'power_plant', p.geom, to_jsonb(p) - 'geom'
    FROM public.power_plant p, bounds b
    WHERE p.geom IS NOT NULL AND ST_Intersects(p.geom, b.geom)
    UNION ALL
    SELECT p.id, 'pv_facility', p.geom, to_jsonb(p) - 'geom'
    FROM public.pv_facility p, bounds b
    WHERE p.geom IS NOT NULL AND ST_Intersects(p.geom, b.geom)
)
SELECT COALESCE(jsonb_agg(jsonb_build_object(
    'type', 'Feature',
    'id', 'hub:' || a.kind || ':' || a.id,
    'geometry', ST_AsGeoJSON(a.geom)::jsonb,
    'properties', a.properties || jsonb_build_object(
        'source_id', a.id,
        'source_table', 'public.' || a.kind,
        'facility_kind', a.kind,
        'coordinate_system', 'EPSG:4326',
        'voltage_unit', 'V',
        'capacity_kw_unit', 'kW',
        'quality_flags', CASE
            WHEN a.kind = 'power_line' THEN jsonb_build_array('electrical_topology_unavailable')
            ELSE '[]'::jsonb END
    )
) ORDER BY a.kind, a.id), '[]'::jsonb) FROM assets a
