SELECT section_id, position, layer, road, risk_level, condition, reference, district, minlat, maxlat, minlon, maxlon,
       geometry, length_km
FROM patna_obstanovka.risk_section WHERE superseded_at IS NULL
